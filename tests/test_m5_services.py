from __future__ import annotations

from pathlib import Path

import pytest

from m0v2_helpers import ARTIFACT_BYTES
from m0v2_helpers import make_document
from m1_helpers import make_plan
from m1_helpers import make_terminology
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m1.kernel import TranslationKernel
from pubtrans.m1.errors import VerificationContractError
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import ActorSpec
from pubtrans.m1.services import GlobalReviewRequest
from pubtrans.m1.store import KernelStore
from pubtrans.m1.workflow import VerificationVerdict
from pubtrans.m2.executor import ResilientExecutor
from pubtrans.m2.executor import RetryPolicy
from pubtrans.m2.model import BudgetPolicy
from pubtrans.m2.store import RecoveryStore
from pubtrans.m5.services import HierarchicalGlobalReviewService
from pubtrans.m5.services import ModelQualityServices


class PassingStructuredClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def structured(
        self,
        *,
        schema_name: str,
        input_payload: object,
        **_kwargs,
    ) -> dict[str, object]:
        assert isinstance(input_payload, dict)
        self.calls.append(schema_name)
        if schema_name == "publication_translation":
            stage = input_payload["stage"]
            assert isinstance(stage, dict)
            context = stage["context"]
            assert isinstance(context, dict)
            current = context["current"]
            assert isinstance(current, dict)
            text = str(current["source_text"])
            text = text.replace("Hello", "你好").replace(" equals ", "等于")
            for marker in input_payload["term_marker_contract"]:
                assert isinstance(marker, dict)
                text = text.replace(
                    "world",
                    str(marker["open_marker"])
                    + str(marker["required_rendering"])
                    + str(marker["close_marker"]),
                    1,
                )
            return {
                "marked_target_text": text,
                "translator_note": "Deterministic quality fixture.",
            }
        if schema_name == "publication_blind_review":
            options = input_payload["options"]
            assert isinstance(options, list)
            return {
                "findings": [],
                "recommended_option_keys": [options[0]["option_key"]],
                "summary": "The option is complete and accurate.",
            }
        if schema_name == "publication_adjudication":
            options = input_payload["options"]
            assert isinstance(options, list)
            return {
                "mode": "SELECT",
                "selected_option_key": options[0]["option_key"],
                "marked_target_text": "",
                "resolutions": [],
                "rationale": "The reviewed option needs no correction.",
            }
        if schema_name == "publication_chinese_edit":
            adjudication = input_payload["adjudication"]
            assert isinstance(adjudication, dict)
            target = adjudication["rendered_target"]
            assert isinstance(target, dict)
            text = str(target["target_text"])
            for marker in input_payload["term_marker_contract"]:
                assert isinstance(marker, dict)
                required = str(marker["required_rendering"])
                text = text.replace(
                    required,
                    str(marker["open_marker"])
                    + required
                    + str(marker["close_marker"]),
                    1,
                )
            return {
                "marked_target_text": text,
                "summary": "No unnecessary stylistic rewrite.",
            }
        if schema_name == "publication_final_verification":
            return {
                "verdict": "PASS",
                "edit_impact": "EQUIVALENT",
                "findings": [],
                "summary": "Accuracy and protected structures pass.",
            }
        if schema_name == "publication_global_review":
            return {
                "verdict": "PASS",
                "findings": [],
                "summary": "Whole-document consistency passes.",
            }
        raise AssertionError(f"unexpected schema {schema_name}")


class UntranslatedStructuredClient(PassingStructuredClient):
    def structured(
        self,
        *,
        schema_name: str,
        input_payload: object,
        **kwargs,
    ) -> dict[str, object]:
        if schema_name != "publication_translation":
            return super().structured(
                schema_name=schema_name,
                input_payload=input_payload,
                **kwargs,
            )
        assert isinstance(input_payload, dict)
        self.calls.append(schema_name)
        stage = input_payload["stage"]
        assert isinstance(stage, dict)
        context = stage["context"]
        assert isinstance(context, dict)
        current = context["current"]
        assert isinstance(current, dict)
        text = str(current["source_text"])
        for marker in input_payload["term_marker_contract"]:
            assert isinstance(marker, dict)
            text = text.replace(
                "world",
                str(marker["open_marker"])
                + str(marker["required_rendering"])
                + str(marker["close_marker"]),
                1,
            )
        return {
            "marked_target_text": text,
            "translator_note": "Intentionally untranslated adversarial fixture.",
        }


def test_structured_model_services_complete_the_full_quality_bus(
    tmp_path: Path,
) -> None:
    document = make_document(repeated=False)
    terminology = make_terminology(document)
    plan = make_plan(document, terminology)
    artifacts = PreparedArtifactStore(tmp_path / "artifacts")
    reference = artifacts.put(ARTIFACT_BYTES)
    client = PassingStructuredClient()
    with KernelStore(tmp_path / "project.sqlite3", artifacts) as store:
        store.register_document(document, reference)
        release = TranslationKernel(
            store,
            ModelQualityServices(client).bundle,
        ).run(
            document=document,
            terminology=terminology,
            plan=plan,
        )
    assert len(release.outcomes) == 1
    target = release.outcomes[0].rendered_target
    assert "世界" in target.target_text
    assert len(target.term_applications) == 1
    application = target.term_applications[0]
    assert (
        target.target_text[application.target_start : application.target_end]
        == "世界"
    )
    assert client.calls == [
        "publication_translation",
        "publication_blind_review",
        "publication_adjudication",
        "publication_chinese_edit",
        "publication_final_verification",
        "publication_global_review",
    ]


def test_deterministic_gate_blocks_untranslated_text_despite_model_pass(
    tmp_path: Path,
) -> None:
    document = make_document(repeated=False)
    terminology = make_terminology(document)
    plan = make_plan(document, terminology)
    artifacts = PreparedArtifactStore(tmp_path / "artifacts")
    reference = artifacts.put(ARTIFACT_BYTES)
    with KernelStore(tmp_path / "project.sqlite3", artifacts) as store:
        store.register_document(document, reference)
        with pytest.raises(VerificationContractError, match="remains blocked"):
            TranslationKernel(
                store,
                ModelQualityServices(UntranslatedStructuredClient()).bundle,
            ).run(
                document=document,
                terminology=terminology,
                plan=plan,
            )


class ChunkReviewClient:
    def __init__(self, *, fail_if_called: bool = False) -> None:
        self.fail_if_called = fail_if_called
        self.calls: list[str] = []

    def structured(
        self,
        *,
        schema_name: str,
        input_payload: object,
        **_kwargs,
    ) -> dict[str, object]:
        if self.fail_if_called:
            raise AssertionError("cached global review invoked the model")
        assert isinstance(input_payload, dict)
        self.calls.append(schema_name)
        if schema_name == "publication_global_review_chunk":
            units = input_payload["unit_payloads"]
            assert isinstance(units, list) and len(units) == 1
            stage = units[0]["stage"]
            assert isinstance(stage, dict)
            context = stage["context"]
            assert isinstance(context, dict)
            key = context["unit_key"]
            return {
                "verdict": "PASS",
                "findings": [],
                "summary": "The bounded sequence passes.",
                "continuity_observations": [
                    {
                        "subject": "register",
                        "rendering": "formal",
                        "unit_keys": [key],
                        "note": "The local register is stable.",
                    }
                ],
            }
        if schema_name == "publication_global_review_synthesis":
            reports = input_payload["chunk_reports"]
            assert isinstance(reports, list) and len(reports) == 3
            return {
                "verdict": "PASS",
                "findings": [],
                "summary": "Cross-chunk continuity passes.",
            }
        raise AssertionError(f"unexpected schema {schema_name}")


def test_hierarchical_global_review_bounds_and_replays_each_paid_call(
    tmp_path: Path,
) -> None:
    plan_key = "f" * 64
    actor = ActorSpec.create(
        role=ActorRole.GLOBAL_REVIEWER,
        provider="fixture",
        model="quality-model",
        prompt_revision="global-review-v1",
    )
    payloads = tuple(
        {
            "stage": {"context": {"unit_key": str(index) * 64}},
            "outcome": {"target": "结果"},
            "padding": "x" * 500,
        }
        for index in range(1, 4)
    )
    request = GlobalReviewRequest(
        actor=actor,
        plan_key=plan_key,
        source_language="en",
        target_language="zh-Hans",
        source_brief="A three-part fixture.",
        unit_payloads=payloads,
    )
    artifacts = PreparedArtifactStore(tmp_path / "artifacts")
    budget = BudgetPolicy.create(
        scope_key=plan_key,
        max_attempted_calls=20,
        max_estimated_tokens=1_000_000,
        max_estimated_microusd=1_000_000,
    )
    with RecoveryStore(tmp_path / "project.sqlite3", artifacts) as store:
        client = ChunkReviewClient()
        service = HierarchicalGlobalReviewService(
            client=client,
            executor=ResilientExecutor(
                store,
                owner_id="global-review-first",
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                ),
                sleeper=lambda _seconds: None,
            ),
            budget=budget,
            max_chunk_characters=700,
        )
        first = service.review_document(request)
        assert first.verdict is VerificationVerdict.PASS
        assert client.calls == [
            "publication_global_review_chunk",
            "publication_global_review_chunk",
            "publication_global_review_chunk",
            "publication_global_review_synthesis",
        ]

        replay = HierarchicalGlobalReviewService(
            client=ChunkReviewClient(fail_if_called=True),
            executor=ResilientExecutor(
                store,
                owner_id="global-review-replay",
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                ),
                sleeper=lambda _seconds: None,
            ),
            budget=budget,
            max_chunk_characters=700,
        ).review_document(request)
        assert replay == first
