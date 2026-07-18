from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from babeldoc.docvision.base_doclayout import YoloResult

from pubtrans.cli import main
from pubtrans.m1.terminology import EvidenceKind
from pubtrans.m1.terminology import EvidenceStance
from pubtrans.m1.terminology import EvidenceTier
from pubtrans.m4.model import ArtifactVerdict
from pubtrans.m5.config import ProductConfig
from pubtrans.m5.evidence import EvidenceCatalog
from pubtrans.m5.evidence import EvidenceMaterial
from pubtrans.m5.runtime import PublicationTranslationRuntime


FONT_PATH = Path(
    os.environ.get(
        "PUBTRANS_TEST_CJK_FONT",
        "/workspace/bersani_delivery_run/layout/fonts/source-han-serif-sc/"
        "SourceHanSerifSC-Regular.otf",
    )
)


class SyntheticLayoutModel:
    def handle_document(
        self,
        pages,
        _mupdf_document,
        _translation_config,
        _save_debug_image,
    ):
        for page in pages:
            yield page, YoloResult(names={0: "plain text"}, boxes=[])


@dataclass(frozen=True)
class Trial:
    name: str
    source_term: str
    target_term: str
    sense_id: str
    domain: str
    expected_fragments: tuple[str, ...]


TRIALS = (
    Trial(
        name="thermodynamics",
        source_term="Entropy",
        target_term="熵",
        sense_id="entropy.thermodynamics.fixture",
        domain="thermodynamics",
        expected_fragments=("熵", "热力学状态", "不可逆过程"),
    ),
    Trial(
        name="narrative",
        source_term="Odysseus",
        target_term="奥德修斯",
        sense_id="odysseus.homeric-name.fixture",
        domain="literary narrative",
        expected_fragments=("奥德修斯", "港口", "大海"),
    ),
)


class ProductFixtureClient:
    def __init__(self, trial: Trial, *, fail_if_called: bool = False) -> None:
        self.trial = trial
        self.fail_if_called = fail_if_called
        self.calls: Counter[str] = Counter()

    def structured(
        self,
        *,
        schema_name: str,
        input_payload: object,
        **_kwargs,
    ) -> dict[str, object]:
        if self.fail_if_called:
            raise AssertionError("a completed project invoked the model")
        assert isinstance(input_payload, dict)
        self.calls[schema_name] += 1
        if schema_name == "publication_source_analysis":
            return self._analysis(input_payload)
        if schema_name == "publication_terminology_review":
            return self._term_review(input_payload)
        if schema_name == "publication_translation":
            return self._translation(input_payload)
        if schema_name == "publication_blind_review":
            options = input_payload["options"]
            assert isinstance(options, list) and options
            return {
                "findings": [],
                "recommended_option_keys": [options[0]["option_key"]],
                "summary": "The complete meaning and protected material are preserved.",
            }
        if schema_name == "publication_adjudication":
            options = input_payload["options"]
            assert isinstance(options, list) and options
            return {
                "mode": "SELECT",
                "selected_option_key": options[0]["option_key"],
                "marked_target_text": "",
                "resolutions": [],
                "rationale": "The independently reviewed option needs no correction.",
            }
        if schema_name == "publication_chinese_edit":
            return self._edit(input_payload)
        if schema_name == "publication_final_verification":
            return {
                "verdict": "PASS",
                "edit_impact": "EQUIVALENT",
                "findings": [],
                "summary": "The edit preserves accuracy and every protected structure.",
            }
        if schema_name in {
            "publication_global_review",
            "publication_global_review_synthesis",
        }:
            return {
                "verdict": "PASS",
                "findings": [],
                "summary": "Names, terminology, register and references are consistent.",
            }
        if schema_name == "publication_global_review_chunk":
            units = input_payload["unit_payloads"]
            assert isinstance(units, list) and units
            keys = []
            for unit in units:
                assert isinstance(unit, dict)
                stage = unit["stage"]
                assert isinstance(stage, dict)
                context = stage["context"]
                assert isinstance(context, dict)
                keys.append(context["unit_key"])
            return {
                "verdict": "PASS",
                "findings": [],
                "summary": "This bounded sequence is complete and consistent.",
                "continuity_observations": [
                    {
                        "subject": "publication register",
                        "rendering": "consistent Simplified Chinese prose",
                        "unit_keys": keys,
                        "note": "The same register is maintained throughout the chunk.",
                    }
                ],
            }
        raise AssertionError(f"unexpected structured schema {schema_name}")

    def _analysis(self, payload: dict[str, object]) -> dict[str, object]:
        raw_units = payload["units"]
        assert isinstance(raw_units, list)
        concepts = []
        if any(
            self.trial.source_term in str(item["source_text"])
            for item in raw_units
            if isinstance(item, dict)
        ):
            concepts.append(
                {
                    "source_term": self.trial.source_term,
                    "sense_id": self.trial.sense_id,
                    "concept_definition": (
                        "The exact recurring concept or proper name in this fixture."
                    ),
                    "domain": self.trial.domain,
                    "candidate_forms": [self.trial.target_term],
                    "rationale": "A repeated high-impact rendering requires consistency.",
                }
            )
        risks = []
        for item in raw_units:
            assert isinstance(item, dict)
            protected = item["protected_placeholders"]
            assert isinstance(protected, list)
            source = str(item["source_text"])
            if protected:
                risks.append(
                    {
                        "unit_key": item["unit_key"],
                        "risk_level": "R3",
                        "reasons": ["protected mathematical content"],
                    }
                )
            elif self.trial.source_term in source:
                risks.append(
                    {
                        "unit_key": item["unit_key"],
                        "risk_level": "R2",
                        "reasons": ["high-impact terminology or proper name"],
                    }
                )
        return {
            "brief": f"A {self.trial.domain} publication-quality fixture.",
            "concepts": concepts,
            "risks": risks,
        }

    def _term_review(self, payload: dict[str, object]) -> dict[str, object]:
        evidence = payload["evidence"]
        assert isinstance(evidence, list) and len(evidence) == 2
        return {
            "candidates": [
                {
                    "target_form": self.trial.target_term,
                    "semantic_fit": "CONFIRMED",
                    "conventionality": "ESTABLISHED",
                    "rationale": (
                        "An authority record and independent corpus record agree in "
                        "this exact sense and domain."
                    ),
                    "assessments": [
                        {
                            "source_key": item["source_key"],
                            "stance": "SUPPORTS",
                            "sense_match": True,
                            "domain_match": True,
                        }
                        for item in evidence
                    ],
                }
            ],
            "selected_target_form": self.trial.target_term,
            "rationale": "The mainstream, sense-matched form has independent support.",
            "mainstream_override_reason": "",
        }

    def _translation(self, payload: dict[str, object]) -> dict[str, object]:
        stage = payload["stage"]
        assert isinstance(stage, dict)
        context = stage["context"]
        assert isinstance(context, dict)
        current = context["current"]
        assert isinstance(current, dict)
        text = str(current["source_text"])
        markers = payload["term_marker_contract"]
        assert isinstance(markers, list)
        for item in markers:
            assert isinstance(item, dict)
            text = text.replace(
                self.trial.source_term,
                str(item["open_marker"])
                + str(item["required_rendering"])
                + str(item["close_marker"]),
                1,
            )
        text = _translate_fixture_text(text)
        return {
            "marked_target_text": text,
            "translator_note": "Deterministic end-to-end quality fixture.",
        }

    @staticmethod
    def _edit(payload: dict[str, object]) -> dict[str, object]:
        adjudication = payload["adjudication"]
        assert isinstance(adjudication, dict)
        rendered = adjudication["rendered_target"]
        assert isinstance(rendered, dict)
        text = str(rendered["target_text"])
        markers = payload["term_marker_contract"]
        assert isinstance(markers, list)
        for item in markers:
            assert isinstance(item, dict)
            required = str(item["required_rendering"])
            text = text.replace(
                required,
                str(item["open_marker"]) + required + str(item["close_marker"]),
                1,
            )
        return {
            "marked_target_text": text,
            "summary": "No stylistic rewrite is needed.",
        }


def _translate_fixture_text(text: str) -> str:
    replacements = (
        (" measures thermodynamic state.", "衡量热力学状态。"),
        ("For this closed system, ", "对于该封闭系统，"),
        (" at 300 K.", "，温度为 300 K。"),
        (" increases during an irreversible process.", "在不可逆过程中增加。"),
        ("A Return", "归来"),
        (" stood at the harbor before dawn.", "黎明前站在港口。"),
        (
            "He remembered Ithaca, but did not speak its name.",
            "他想起伊萨卡，却没有说出它的名字。",
        ),
        ("At last, ", "终于，"),
        (" turned toward the sea.", "转身面向大海。"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    return text


def _evidence(trial: Trial) -> EvidenceCatalog:
    entries = []
    for index, (kind, tier, host) in enumerate(
        (
            (
                EvidenceKind.AUTHORITY_TERMBANK,
                EvidenceTier.A_AUTHORITY,
                "www.termonline.cn",
            ),
            (
                EvidenceKind.CORPUS_ATTESTATION,
                EvidenceTier.C_CORPUS,
                "parallel-publication.example.org",
            ),
        )
    ):
        key = hashlib.sha256(f"{trial.name}:{index}".encode()).hexdigest()
        entries.append(
            EvidenceMaterial(
                source_term=trial.source_term,
                sense_id=trial.sense_id,
                target_form=trial.target_term,
                stance=EvidenceStance.SUPPORTS,
                kind=kind,
                tier=tier,
                source_key=key,
                source_uri=f"https://{host}/{trial.name}/{index}",
                source_title=f"Captured evidence {index + 1}",
                excerpt=(
                    f"In this exact {trial.domain} sense, {trial.source_term} is "
                    f"rendered as {trial.target_term}."
                ),
                retrieved_on="2026-07-17",
                sense_match=False,
                domain_match=False,
                notes="Captured independently; reviewer must assess the excerpt.",
            )
        )
    return EvidenceCatalog(tuple(entries))


def _create_source(path: Path, trial: Trial) -> None:
    source = pymupdf.open()
    if trial.name == "thermodynamics":
        first = source.new_page(width=440, height=320)
        diagram = pymupdf.Pixmap(
            pymupdf.csRGB,
            pymupdf.IRect(0, 0, 24, 24),
            False,
        )
        diagram.clear_with(0x2A6F97)
        first.insert_image(
            pymupdf.Rect(350, 35, 398, 83),
            stream=diagram.tobytes("png"),
        )
        first.insert_text(
            (45, 70),
            "Entropy measures thermodynamic state.",
            fontsize=13,
        )
        first.insert_text(
            (45, 135),
            "For this closed system, S = klnW at 300 K.",
            fontsize=12,
            fontname="Times-Italic",
        )
        second = source.new_page(width=440, height=320)
        second.insert_text(
            (45, 80),
            "Entropy increases during an irreversible process.",
            fontsize=13,
        )
    else:
        page = source.new_page(width=360, height=540)
        page.insert_text((44, 60), "A Return", fontsize=18)
        page.insert_text(
            (44, 125),
            "Odysseus stood at the harbor before dawn.",
            fontsize=12,
        )
        page.insert_text(
            (44, 205),
            "He remembered Ithaca, but did not speak its name.",
            fontsize=12,
        )
        page.insert_text(
            (44, 285),
            "At last, Odysseus turned toward the sea.",
            fontsize=12,
        )
    source.save(path)
    source.close()


def _font_family(_language):
    return {
        "normal": ["fixture-cjk"],
        "script": ["fixture-cjk"],
        "fallback": ["fixture-cjk"],
        "base": ["fixture-cjk"],
    }


def _font_and_metadata(_font_name):
    return FONT_PATH, {
        "ascent": 0.88,
        "descent": -0.12,
        "encoding_length": 2,
    }


@pytest.mark.parametrize("trial", TRIALS, ids=lambda item: item.name)
def test_two_domain_products_restore_and_verify_pdf(
    tmp_path: Path,
    trial: Trial,
    capsys,
) -> None:
    if not FONT_PATH.is_file():
        pytest.skip("Source Han Serif SC integration font is unavailable")
    source = tmp_path / f"{trial.name}.pdf"
    project = tmp_path / f"{trial.name}.pubtrans"
    _create_source(source, trial)
    client = ProductFixtureClient(trial)
    runtime = PublicationTranslationRuntime(
        config=ProductConfig.create(
            default_model="fixture-quality-model",
            enable_web_research=False,
            max_planning_calls=100,
            max_translation_calls=500,
        ),
        structured_client=client,
        evidence_catalog=_evidence(trial),
        layout_model=SyntheticLayoutModel(),
        layout_profile={"layout_model_sha256": "5" * 64},
        skip_scanned_detection=True,
    )
    with (
        patch("babeldoc.assets.assets.get_font_family", _font_family),
        patch("babeldoc.assets.assets.get_font_and_metadata", _font_and_metadata),
        patch(
            "babeldoc.assets.assets.get_cmap_data",
            lambda _name: {"u": "", "r": [], "c": []},
        ),
    ):
        result = runtime.run(source_pdf=source, project_directory=project)

    assert result.output_pdf_path.is_file()
    assert result.report_path.is_file()
    assert result.artifact_report.verdict is ArtifactVerdict.PASS
    assert result.artifact_report.findings == ()
    assert result.prepared_capture_was_needed
    assert client.calls["publication_source_analysis"] == 1
    assert client.calls["publication_terminology_review"] == 1
    assert client.calls["publication_translation"] >= 4
    output = pymupdf.open(result.output_pdf_path)
    try:
        assert output.page_count == (2 if trial.name == "thermodynamics" else 1)
        text = "".join(page.get_text() for page in output)
    finally:
        output.close()
    normalized = " ".join(
        unicodedata.normalize("NFKC", text)
        .replace("\N{NO-BREAK SPACE}", " ")
        .split()
    )
    for fragment in trial.expected_fragments:
        assert fragment in normalized
    if trial.name == "thermodynamics":
        assert "S = klnW" in normalized
        assert "300" in normalized

    assert main(["status", str(project)]) == 0
    status = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert status["product_state"] == "RELEASED"
    assert status["active_artifact_report_id"] == result.artifact_report.report_id
    assert status["verified_output_pdf"] == str(result.output_pdf_path)


def test_completed_product_resumes_without_model_calls(tmp_path: Path) -> None:
    if not FONT_PATH.is_file():
        pytest.skip("Source Han Serif SC integration font is unavailable")
    trial = TRIALS[0]
    source = tmp_path / "resume.pdf"
    project = tmp_path / "resume.pubtrans"
    _create_source(source, trial)
    config = ProductConfig.create(
        default_model="fixture-quality-model",
        enable_web_research=False,
        max_planning_calls=100,
        max_translation_calls=500,
    )

    def run(client: ProductFixtureClient):
        return PublicationTranslationRuntime(
            config=config,
            structured_client=client,
            evidence_catalog=_evidence(trial),
            layout_model=SyntheticLayoutModel(),
            layout_profile={"layout_model_sha256": "5" * 64},
            skip_scanned_detection=True,
        ).run(source_pdf=source, project_directory=project)

    with (
        patch("babeldoc.assets.assets.get_font_family", _font_family),
        patch("babeldoc.assets.assets.get_font_and_metadata", _font_and_metadata),
        patch(
            "babeldoc.assets.assets.get_cmap_data",
            lambda _name: {"u": "", "r": [], "c": []},
        ),
    ):
        first = run(ProductFixtureClient(trial))
        resumed = run(ProductFixtureClient(trial, fail_if_called=True))

    assert resumed.release == first.release
    assert not resumed.prepared_capture_was_needed
    assert resumed.artifact_report.verdict is ArtifactVerdict.PASS
