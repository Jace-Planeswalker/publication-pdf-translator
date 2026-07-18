from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from m0v2_helpers import ARTIFACT_BYTES
from m0v2_helpers import make_document
from m1_helpers import PassingServices
from m1_helpers import make_plan
from m1_helpers import make_terminology
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.store import ProjectStore
from pubtrans.m1.context import build_context_packages
from pubtrans.m1.errors import StageConflictError
from pubtrans.m1.kernel import TranslationKernel
from pubtrans.m1.store import KernelStore
from pubtrans.m1.terminology import RenderedTarget
from pubtrans.m1.terminology import TermApplication
from pubtrans.m1.workflow import Candidate


def altered_target(document, terminology) -> RenderedTarget:
    unit = document.units[0]
    directive = terminology.directives_for_unit(unit.unit_key)[0]
    source = unit.source_text
    style = unit.placeholders.specs[0]
    formula = unit.placeholders.specs[1]
    text = (
        f"您好，{style.open_token}{directive.required_rendering}{style.close_token}"
        f"等于{formula.open_token}。"
    )
    start = text.index(directive.required_rendering)
    application = TermApplication.create(
        occurrence_key=directive.occurrence_key,
        target_text=text,
        target_start=start,
        target_end=start + len(directive.required_rendering),
    )
    assert "world" in source
    return RenderedTarget.create(
        unit=unit,
        terminology=terminology,
        target_text=text,
        term_applications=(application,),
    )


class FailsAtFirstReview(PassingServices):
    def review(self, request):
        self.calls["review"] += 1
        raise RuntimeError("simulated interruption")


class KernelStoreTests(unittest.TestCase):
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

    def test_schema_two_migrates_additively_to_three(self) -> None:
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
        with KernelStore(self.database, self.artifacts) as store:
            self.assertEqual(store.load_document(), self.document)
            self.assertEqual(
                store.connection.execute("PRAGMA user_version").fetchone()[0],
                3,
            )
            self.assertIsNotNone(
                store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'm1_plan'"
                ).fetchone()
            )

    def test_end_to_end_release_and_clean_resume(self) -> None:
        services = PassingServices(self.document)
        with KernelStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            release = TranslationKernel(store, services.bundle).run(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
            )
            self.assertEqual(services.calls["generate"], 3)
            self.assertEqual(services.calls["review"], 2)
            self.assertEqual(services.calls["adjudicate"], 2)
            self.assertEqual(services.calls["edit"], 2)
            self.assertEqual(services.calls["verify"], 2)
            self.assertEqual(services.calls["global"], 1)
            self.assertEqual(store.resolve(self.document), release.approvals)
            self.assertEqual(store.load_active_release(), release)
            self.assertEqual(store.m1_status(self.plan.plan_key)["outcomes"], 2)

        resumed_services = PassingServices(self.document)
        with KernelStore(self.database, self.artifacts) as resumed:
            resumed_release = TranslationKernel(
                resumed,
                resumed_services.bundle,
            ).run(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
            )
            self.assertEqual(resumed_release, release)
            self.assertEqual(sum(resumed_services.calls.values()), 0)

    def test_interruption_resumes_without_repeating_completed_candidate(self) -> None:
        failing = FailsAtFirstReview(self.document)
        with KernelStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                TranslationKernel(store, failing.bundle).run(
                    document=self.document,
                    terminology=self.terminology,
                    plan=self.plan,
                )
            self.assertEqual(failing.calls["generate"], 1)
            self.assertEqual(store.m1_status(self.plan.plan_key)["candidates"], 1)

        resumed = PassingServices(self.document)
        with KernelStore(self.database, self.artifacts) as store:
            TranslationKernel(store, resumed.bundle).run(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
            )
            self.assertEqual(resumed.calls["generate"], 2)
            self.assertEqual(resumed.calls["review"], 2)

    def test_conflicting_stage_replay_fails_closed(self) -> None:
        services = PassingServices(self.document)
        with KernelStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            TranslationKernel(store, services.bundle).run(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
            )
            contexts = build_context_packages(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
            )
            conflict = Candidate.create(
                plan=self.plan,
                unit=self.document.units[0],
                lane=self.plan.lanes[0],
                context=contexts[0],
                terminology=self.terminology,
                rendered_target=altered_target(self.document, self.terminology),
                translator_note="different immutable result",
            )
            with self.assertRaises(StageConflictError):
                store.record_candidate(conflict)

    def test_release_activation_rolls_back_as_one_transaction(self) -> None:
        services = PassingServices(self.document)
        with KernelStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            release = TranslationKernel(store, services.bundle).run(
                document=self.document,
                terminology=self.terminology,
                plan=self.plan,
                activate=False,
            )
            self.assertIsNone(store.load_active_release())
            self.assertEqual(store.status()["active_approvals"], 0)
            with mock.patch.object(
                store,
                "_record_approvals_in_transaction",
                side_effect=RuntimeError("activation crash"),
            ):
                with self.assertRaisesRegex(RuntimeError, "activation crash"):
                    store.record_release(
                        document=self.document,
                        plan=self.plan,
                        release=release,
                        activate=True,
                    )
            self.assertIsNone(store.load_active_release())
            self.assertEqual(store.status()["active_approvals"], 0)


if __name__ == "__main__":
    unittest.main()
