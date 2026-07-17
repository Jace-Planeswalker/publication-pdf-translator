from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
import urllib.error

import pytest

from m0v2_helpers import ARTIFACT_BYTES
from m0v2_helpers import make_document
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m1.terminology import DecisionConfidence
from pubtrans.m1.terminology import Conventionality
from pubtrans.m1.terminology import EvidenceKind
from pubtrans.m1.terminology import EvidenceStance
from pubtrans.m1.terminology import EvidenceTier
from pubtrans.m1.terminology import TermTreatment
from pubtrans.m2.store import RecoveryStore
from pubtrans.m5.config import ProductConfig
from pubtrans.m5.evidence import EvidenceCatalog
from pubtrans.m5.evidence import EvidenceMaterial
from pubtrans.m5.evidence import SafeHTTPPageFetcher
from pubtrans.m5.evidence import harvest_citations
from pubtrans.m5.errors import TerminologyPlanningError
from pubtrans.m5.openai import WebCitation
from pubtrans.m5.planner import ProductionPlanner


@dataclass
class ScriptedPlanningClient:
    analysis: dict[str, object]
    review: dict[str, object]
    fail_if_called: bool = False

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    def structured(self, *, schema_name: str, **_kwargs):
        if self.fail_if_called:
            raise AssertionError("cached planning call invoked the model")
        self.calls.append(schema_name)
        if schema_name == "publication_source_analysis":
            return self.analysis
        if schema_name == "publication_terminology_review":
            return self.review
        raise AssertionError(f"unexpected schema: {schema_name}")


class StaticPageFetcher:
    def __init__(self, text: str | None):
        self.text = text

    def fetch_text(self, _url: str) -> str | None:
        return self.text


def product() -> ProductConfig:
    return ProductConfig.create(
        default_model="fixture-quality-model",
        enable_web_research=False,
        max_planning_calls=100,
    )


def material(
    *,
    target: str,
    key: str,
    tier: EvidenceTier,
) -> EvidenceMaterial:
    return EvidenceMaterial(
        source_term="world",
        sense_id="world.general.fixture",
        target_form=target,
        stance=EvidenceStance.SUPPORTS,
        kind=(
            EvidenceKind.AUTHORITY_TERMBANK
            if tier is EvidenceTier.A_AUTHORITY
            else EvidenceKind.CORPUS_ATTESTATION
        ),
        tier=tier,
        source_key=key * 64,
        source_uri=f"https://evidence-{key}.test/{target}",
        source_title=f"Evidence {key}",
        excerpt=f"In this sense world is rendered as {target}.",
        retrieved_on="2026-07-17",
        sense_match=True,
        domain_match=True,
        notes="Reviewed fixture evidence.",
    )


def analysis(*candidates: str) -> dict[str, object]:
    return {
        "brief": "A general publication fixture discussing the world.",
        "concepts": [
            {
                "source_term": "world",
                "sense_id": "world.general.fixture",
                "concept_definition": "The general inhabited or known world.",
                "domain": "general publication",
                "candidate_forms": list(candidates),
                "rationale": "Repeated concept requiring consistency.",
            }
        ],
        "risks": [],
    }


def reviewed_candidate(
    *,
    target: str,
    conventionality: str,
    source_keys: tuple[str, ...],
) -> dict[str, object]:
    return {
        "target_form": target,
        "semantic_fit": "CONFIRMED",
        "conventionality": conventionality,
        "rationale": "The captured excerpts match the document sense.",
        "assessments": [
            {
                "source_key": key,
                "stance": "SUPPORTS",
                "sense_match": True,
                "domain_match": True,
            }
            for key in source_keys
        ],
    }


def open_store(root: Path):
    artifacts = PreparedArtifactStore(root / "artifacts")
    reference = artifacts.put(ARTIFACT_BYTES)
    document = make_document()
    store = RecoveryStore(root / "project.sqlite3", artifacts)
    store.register_document(document, reference)
    return document, store


def test_planner_verifies_mainstream_term_and_reuses_cached_calls(tmp_path: Path) -> None:
    document, store = open_store(tmp_path)
    entries = (
        material(target="世界", key="1", tier=EvidenceTier.A_AUTHORITY),
        material(target="世界", key="2", tier=EvidenceTier.C_CORPUS),
    )
    client = ScriptedPlanningClient(
        analysis=analysis("世界"),
        review={
            "candidates": [
                reviewed_candidate(
                    target="世界",
                    conventionality="ESTABLISHED",
                    source_keys=("1" * 64, "2" * 64),
                )
            ],
            "selected_target_form": "世界",
            "rationale": "Authority and established usage agree.",
            "mainstream_override_reason": "",
        },
    )
    planner = ProductionPlanner(
        config=product(),
        structured_client=client,
        evidence_catalog=EvidenceCatalog(entries),
    )
    planned = planner.plan(document, store)
    assert client.calls == [
        "publication_source_analysis",
        "publication_terminology_review",
    ]
    term = planned.terminology.terms[0]
    assert term.target_term == "世界"
    assert term.decision.confidence is DecisionConfidence.VERIFIED
    assert term.treatment is TermTreatment.TRANSLATE_ONLY

    replay_client = ScriptedPlanningClient(
        analysis={},
        review={},
        fail_if_called=True,
    )
    replay = ProductionPlanner(
        config=product(),
        structured_client=replay_client,
        evidence_catalog=EvidenceCatalog(entries),
    ).plan(document, store)
    assert replay == planned
    assert replay_client.calls == []
    store.close()


def test_unsubstantiated_specialist_sounding_term_is_retained(tmp_path: Path) -> None:
    document, store = open_store(tmp_path)
    client = ScriptedPlanningClient(
        analysis=analysis("寰域总体"),
        review={
            "candidates": [
                reviewed_candidate(
                    target="寰域总体",
                    conventionality="ESTABLISHED",
                    source_keys=(),
                )
            ],
            "selected_target_form": "寰域总体",
            "rationale": "No captured evidence supports the proposed form.",
            "mainstream_override_reason": "",
        },
    )
    planned = ProductionPlanner(
        config=product(),
        structured_client=client,
    ).plan(document, store)
    term = planned.terminology.terms[0]
    assert term.target_term == "world"
    assert term.decision.confidence is DecisionConfidence.RETAINED_UNRESOLVED
    assert term.treatment is TermTreatment.RETAIN_SOURCE
    store.close()


def test_rare_selection_without_accuracy_override_loses_to_mainstream(
    tmp_path: Path,
) -> None:
    document, store = open_store(tmp_path)
    entries = tuple(
        material(target=target, key=key, tier=tier)
        for target, key, tier in (
            ("世界", "1", EvidenceTier.A_AUTHORITY),
            ("世界", "2", EvidenceTier.C_CORPUS),
            ("寰宇", "3", EvidenceTier.A_AUTHORITY),
            ("寰宇", "4", EvidenceTier.C_CORPUS),
        )
    )
    client = ScriptedPlanningClient(
        analysis=analysis("世界", "寰宇"),
        review={
            "candidates": [
                reviewed_candidate(
                    target="世界",
                    conventionality="ESTABLISHED",
                    source_keys=("1" * 64, "2" * 64),
                ),
                reviewed_candidate(
                    target="寰宇",
                    conventionality="RARE",
                    source_keys=("3" * 64, "4" * 64),
                ),
            ],
            "selected_target_form": "寰宇",
            "rationale": "The rare form was proposed without an accuracy reason.",
            "mainstream_override_reason": "",
        },
    )
    planned = ProductionPlanner(
        config=product(),
        structured_client=client,
        evidence_catalog=EvidenceCatalog(entries),
    ).plan(document, store)
    assert planned.terminology.terms[0].target_term == "世界"
    store.close()


def test_web_citation_becomes_evidence_only_after_page_text_match() -> None:
    citations = (
        WebCitation(
            url="https://www.termonline.cn/word/fixture",
            title="Termonline fixture",
            cited_text="discovery only",
        ),
    )
    harvested = harvest_citations(
        source_term="entropy",
        sense_id="entropy.physics",
        target_forms=("熵", "无序度"),
        citations=citations,
        retrieved_on="2026-07-17",
        fetcher=StaticPageFetcher("热力学中的熵是一个状态函数。"),
    )
    assert len(harvested) == 1
    assert harvested[0].target_form == "熵"
    assert harvested[0].tier is EvidenceTier.A_AUTHORITY
    assert not harvested[0].sense_match
    assert not harvested[0].domain_match


def test_two_pages_from_one_authority_are_not_independent_sources(
    tmp_path: Path,
) -> None:
    document, store = open_store(tmp_path)
    first = material(target="世界", key="1", tier=EvidenceTier.A_AUTHORITY)
    second = material(target="世界", key="2", tier=EvidenceTier.A_AUTHORITY)
    entries = (
        first,
        replace(
            second,
            source_uri="https://evidence-1.test/a-second-page",
        ),
    )
    client = ScriptedPlanningClient(
        analysis=analysis("世界"),
        review={
            "candidates": [
                reviewed_candidate(
                    target="世界",
                    conventionality="ESTABLISHED",
                    source_keys=("1" * 64, "2" * 64),
                )
            ],
            "selected_target_form": "世界",
            "rationale": "Two records share one publishing authority.",
            "mainstream_override_reason": "",
        },
    )
    planned = ProductionPlanner(
        config=product(),
        structured_client=client,
        evidence_catalog=EvidenceCatalog(entries),
    ).plan(document, store)
    term = planned.terminology.terms[0]
    assert term.decision.confidence is DecisionConfidence.SUPPORTED
    assert term.decision.selected_candidate.conventionality is Conventionality.ATTESTED
    assert term.treatment is TermTreatment.TRANSLATE_WITH_SOURCE_FIRST
    store.close()


def test_manual_evidence_requires_target_excerpt_and_stable_source_binding() -> None:
    entry = material(target="世界", key="1", tier=EvidenceTier.A_AUTHORITY)
    with pytest.raises(TerminologyPlanningError, match="does not contain"):
        replace(entry, excerpt="An unrelated passage without the candidate.")
    with pytest.raises(TerminologyPlanningError, match="multiple sources"):
        EvidenceCatalog(
            (
                entry,
                replace(entry, source_uri="https://another-authority.test/term"),
            )
        )


def test_page_fetcher_refuses_redirect_from_public_to_local_network() -> None:
    class RedirectingOpener:
        def __init__(self) -> None:
            self.calls = 0

        def open(self, request, *, timeout):
            del timeout
            self.calls += 1
            raise urllib.error.HTTPError(
                request.full_url,
                302,
                "Found",
                {"Location": "http://127.0.0.1/private"},
                None,
            )

    opener = RedirectingOpener()
    fetcher = SafeHTTPPageFetcher(opener=opener)
    assert fetcher.fetch_text("https://8.8.8.8/public") is None
    assert opener.calls == 1
