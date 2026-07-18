from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from m0v2_helpers import ARTIFACT_BYTES
from m0v2_helpers import make_document
from m1_helpers import PassingServices
from m1_helpers import actor
from m1_helpers import make_plan
from m1_helpers import make_terminology
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.store import ProjectStore
from pubtrans.m1.context import build_context_packages
from pubtrans.m1.kernel import TranslationKernel
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import KernelPlan
from pubtrans.m1.services import GenerationRequest
from pubtrans.m1.services import UnitStageInput
from pubtrans.m1.store import KernelStore
from pubtrans.m2.errors import BudgetExceededError
from pubtrans.m2.errors import LeaseBusyError
from pubtrans.m2.errors import LeaseLostError
from pubtrans.m2.errors import PermanentServiceError
from pubtrans.m2.errors import PreviouslyFailedCallError
from pubtrans.m2.errors import TransientServiceError
from pubtrans.m2.executor import ResilientExecutor
from pubtrans.m2.executor import RetryPolicy
from pubtrans.m2.model import AttemptOutcome
from pubtrans.m2.model import BudgetPolicy
from pubtrans.m2.model import CallDescriptor
from pubtrans.m2.model import CallEstimate
from pubtrans.m2.model import CallStage
from pubtrans.m2.services import EstimateSchedule
from pubtrans.m2.services import ResilientServices
from pubtrans.m2.store import RecoveryStore


class RecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifacts = PreparedArtifactStore(self.root / "artifacts")
        self.reference = self.artifacts.put(ARTIFACT_BYTES)
        self.database = self.root / "project.sqlite3"
        self.scope = "a" * 64
        self.descriptor = CallDescriptor.create(
            stage=CallStage.GENERATION,
            dependency_payload={"unit": "u1", "prompt": "v1"},
            slot_hint="u1:lane1",
        )
        self.estimate = CallEstimate(estimated_tokens=100, estimated_microusd=20)
        self.budget = BudgetPolicy.create(
            scope_key=self.scope,
            max_attempted_calls=10,
            max_estimated_tokens=1000,
            max_estimated_microusd=1000,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def executor(self, store, **kwargs) -> ResilientExecutor:
        return ResilientExecutor(
            store,
            owner_id="worker-one",
            retry_policy=kwargs.pop(
                "retry_policy",
                RetryPolicy(
                    max_attempts=3,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                ),
            ),
            sleeper=kwargs.pop("sleeper", lambda _seconds: None),
            **kwargs,
        )

    def test_success_is_cached_before_domain_persistence(self) -> None:
        calls = 0

        def operation():
            nonlocal calls
            calls += 1
            return {"translation": "结果"}

        with RecoveryStore(self.database, self.artifacts) as store:
            executor = self.executor(store)
            first = executor.execute(
                descriptor=self.descriptor,
                budget=self.budget,
                estimate=self.estimate,
                operation=operation,
                encode=lambda value: value,
                decode=lambda value: value,
            )
            second = executor.execute(
                descriptor=self.descriptor,
                budget=self.budget,
                estimate=self.estimate,
                operation=operation,
                encode=lambda value: value,
                decode=lambda value: value,
            )
            self.assertEqual(first, second)
            self.assertEqual(calls, 1)
            self.assertEqual(store.budget_usage(self.scope)["attempted_calls"], 1)

    def test_transient_failures_retry_with_attempt_ledger(self) -> None:
        calls = 0
        delays = []

        def operation():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise TransientServiceError("temporary upstream failure")
            return "ok"

        with RecoveryStore(self.database, self.artifacts) as store:
            value = self.executor(store, sleeper=delays.append).execute(
                descriptor=self.descriptor,
                budget=self.budget,
                estimate=self.estimate,
                operation=operation,
                encode=lambda item: item,
                decode=lambda item: str(item),
            )
            self.assertEqual(value, "ok")
            self.assertEqual(calls, 3)
            self.assertEqual(delays, [0, 0])
            self.assertEqual(
                tuple(item["outcome"] for item in store.attempts(self.descriptor.call_key)),
                (
                    AttemptOutcome.RETRYABLE_FAILURE.value,
                    AttemptOutcome.RETRYABLE_FAILURE.value,
                    AttemptOutcome.SUCCEEDED.value,
                ),
            )

    def test_permanent_failure_is_not_retried_or_replayed(self) -> None:
        calls = 0

        def operation():
            nonlocal calls
            calls += 1
            raise PermanentServiceError(
                "invalid model name; api_key=sk-supersecretvalue"
            )

        with RecoveryStore(self.database, self.artifacts) as store:
            executor = self.executor(store)
            with self.assertRaises(PermanentServiceError):
                executor.execute(
                    descriptor=self.descriptor,
                    budget=self.budget,
                    estimate=self.estimate,
                    operation=operation,
                    encode=lambda item: item,
                    decode=lambda item: item,
                )
            self.assertEqual(calls, 1)
            stored_error = store.attempts(self.descriptor.call_key)[0]["error"]
            self.assertNotIn("supersecretvalue", str(stored_error))
            self.assertIn("[REDACTED]", str(stored_error))
            with self.assertRaises(PreviouslyFailedCallError):
                executor.execute(
                    descriptor=self.descriptor,
                    budget=self.budget,
                    estimate=self.estimate,
                    operation=operation,
                    encode=lambda item: item,
                    decode=lambda item: item,
                )
            self.assertEqual(calls, 1)

    def test_budget_blocks_before_remote_invocation(self) -> None:
        no_calls = BudgetPolicy.create(
            scope_key=self.scope,
            max_attempted_calls=0,
            max_estimated_tokens=1000,
            max_estimated_microusd=1000,
        )
        calls = 0

        def operation():
            nonlocal calls
            calls += 1
            return "should not run"

        with RecoveryStore(self.database, self.artifacts) as store:
            with self.assertRaises(BudgetExceededError):
                self.executor(store).execute(
                    descriptor=self.descriptor,
                    budget=no_calls,
                    estimate=self.estimate,
                    operation=operation,
                    encode=lambda item: item,
                    decode=lambda item: item,
                )
            self.assertEqual(calls, 0)
            self.assertEqual(store.budget_usage(self.scope)["attempted_calls"], 0)
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM m2_lease").fetchone()[0],
                0,
            )

    def test_expired_lease_is_recovered_and_stale_worker_cannot_commit(self) -> None:
        now = datetime(2026, 7, 17, tzinfo=timezone.utc)
        with RecoveryStore(self.database, self.artifacts) as store:
            store.register_call(self.descriptor)
            store.register_budget(self.budget)
            first_lease = store.acquire_lease(
                self.descriptor,
                owner_id="worker-one",
                now=now,
                ttl_seconds=10,
            )
            first_attempt = store.begin_attempt(
                descriptor=self.descriptor,
                lease=first_lease,
                policy=self.budget,
                estimate=self.estimate,
                now=now,
            )
            with self.assertRaises(LeaseBusyError):
                store.acquire_lease(
                    self.descriptor,
                    owner_id="worker-two",
                    now=now + timedelta(seconds=5),
                    ttl_seconds=10,
                )
            second_lease = store.acquire_lease(
                self.descriptor,
                owner_id="worker-two",
                now=now + timedelta(seconds=11),
                ttl_seconds=10,
            )
            self.assertEqual(
                store.attempts(self.descriptor.call_key)[0]["outcome"],
                AttemptOutcome.ABANDONED.value,
            )
            with self.assertRaises(LeaseLostError):
                store.complete_success(
                    lease=first_lease,
                    attempt=first_attempt,
                    result_payload={"stale": True},
                    now=now + timedelta(seconds=12),
                )
            second_attempt = store.begin_attempt(
                descriptor=self.descriptor,
                lease=second_lease,
                policy=self.budget,
                estimate=self.estimate,
                now=now + timedelta(seconds=12),
            )
            store.complete_success(
                lease=second_lease,
                attempt=second_attempt,
                result_payload={"fresh": True},
                now=now + timedelta(seconds=13),
            )

    def test_schema_three_migrates_to_four(self) -> None:
        with KernelStore(self.database, self.artifacts):
            pass
        with RecoveryStore(self.database, self.artifacts) as store:
            self.assertEqual(
                store.connection.execute("PRAGMA user_version").fetchone()[0],
                4,
            )
        with ProjectStore(self.database, self.artifacts):
            pass
        with KernelStore(self.database, self.artifacts):
            pass


class ResilientKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifacts = PreparedArtifactStore(self.root / "artifacts")
        self.reference = self.artifacts.put(ARTIFACT_BYTES)
        self.database = self.root / "project.sqlite3"
        self.document = make_document()
        self.terminology = make_terminology(self.document)
        self.plan = make_plan(self.document, self.terminology)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def budget(self, scope_key: str) -> BudgetPolicy:
        return BudgetPolicy.create(
            scope_key=scope_key,
            max_attempted_calls=100,
            max_estimated_tokens=1_000_000,
            max_estimated_microusd=1_000_000,
        )

    def test_full_kernel_uses_recovery_controller_for_every_remote_stage(self) -> None:
        underlying = PassingServices(self.document)
        with RecoveryStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            resilient = ResilientServices(
                underlying.bundle,
                ResilientExecutor(
                    store,
                    owner_id="kernel-worker",
                    retry_policy=RetryPolicy(
                        max_attempts=2,
                        base_delay_seconds=0,
                        max_delay_seconds=0,
                    ),
                    sleeper=lambda _seconds: None,
                ),
                self.budget(self.plan.plan_key),
                estimates=EstimateSchedule(),
            )
            TranslationKernel(store, resilient.bundle).run(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
            )
            self.assertEqual(
                store.budget_usage(self.plan.plan_key)["attempted_calls"],
                12,
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM m2_call WHERE status = 'SUCCEEDED'"
                ).fetchone()[0],
                12,
            )

    def test_unchanged_generation_is_reused_after_downstream_replan(self) -> None:
        changed = KernelPlan.create(
            document=self.document,
            terminology=self.terminology,
            context_policy=self.plan.context_policy,
            source_brief=self.plan.source_brief,
            lanes=self.plan.lanes,
            routes=self.plan.routes,
            reviewer=self.plan.reviewer,
            adjudicator=self.plan.adjudicator,
            editor=actor(ActorRole.CHINESE_EDITOR, "new-editor"),
            verifier=self.plan.verifier,
            global_reviewer=self.plan.global_reviewer,
        )
        contexts_one = build_context_packages(
            document=self.document,
            terminology=self.terminology,
            plan=self.plan,
        )
        contexts_two = build_context_packages(
            document=self.document,
            terminology=self.terminology,
            plan=changed,
        )
        stage_one = UnitStageInput.create(
            document=self.document,
            plan=self.plan,
            unit=self.document.units[0],
            context=contexts_one[0],
            terminology=self.terminology,
        )
        stage_two = UnitStageInput.create(
            document=self.document,
            plan=changed,
            unit=self.document.units[0],
            context=contexts_two[0],
            terminology=self.terminology,
        )
        underlying = PassingServices(self.document)
        with RecoveryStore(self.database, self.artifacts) as store:
            executor = ResilientExecutor(
                store,
                owner_id="cache-worker",
                sleeper=lambda _seconds: None,
            )
            first = ResilientServices(
                underlying.bundle,
                executor,
                self.budget(self.plan.plan_key),
            )
            second = ResilientServices(
                underlying.bundle,
                executor,
                self.budget(changed.plan_key),
            )
            lane = self.plan.lanes[0]
            first.generate(GenerationRequest(actor=lane.actor, lane=lane, stage=stage_one))
            second.generate(GenerationRequest(actor=lane.actor, lane=lane, stage=stage_two))
            self.assertEqual(underlying.calls["generate"], 1)
            self.assertEqual(
                store.budget_usage(changed.plan_key)["attempted_calls"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
