from __future__ import annotations

from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from babeldoc.docvision.base_doclayout import YoloResult
from babeldoc.format.pdf.high_level import translate
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode

from m1_helpers import actor
from m1_helpers import target_draft_from_rendered
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import ContextPolicy
from pubtrans.m1.plan import KernelPlan
from pubtrans.m1.plan import LaneSpec
from pubtrans.m1.plan import RiskLevel
from pubtrans.m1.plan import UnitRoute
from pubtrans.m1.services import AdjudicationDraft
from pubtrans.m1.services import ApplicationDraft
from pubtrans.m1.services import CandidateDraft
from pubtrans.m1.services import EditDraft
from pubtrans.m1.services import GlobalReviewDraft
from pubtrans.m1.services import RenderedTargetDraft
from pubtrans.m1.services import ReviewDraft
from pubtrans.m1.services import ServiceBundle
from pubtrans.m1.services import VerificationDraft
from pubtrans.m1.terminology import Conventionality
from pubtrans.m1.terminology import DecisionConfidence
from pubtrans.m1.terminology import EvidenceKind
from pubtrans.m1.terminology import EvidenceStance
from pubtrans.m1.terminology import EvidenceTier
from pubtrans.m1.terminology import RenderedTarget
from pubtrans.m1.terminology import SemanticFit
from pubtrans.m1.terminology import TargetTermCandidate
from pubtrans.m1.terminology import TermDecision
from pubtrans.m1.terminology import TermEvidence
from pubtrans.m1.terminology import TermOccurrence
from pubtrans.m1.terminology import TermRevision
from pubtrans.m1.terminology import TermTreatment
from pubtrans.m1.terminology import TerminologySnapshot
from pubtrans.m1.workflow import AdjudicationMode
from pubtrans.m1.workflow import EditImpactVerdict
from pubtrans.m1.workflow import EditRevision
from pubtrans.m1.workflow import VerificationVerdict
from pubtrans.m2.executor import ResilientExecutor
from pubtrans.m2.executor import RetryPolicy
from pubtrans.m2.model import BudgetPolicy
from pubtrans.m2.services import ResilientServices
from pubtrans.m3.workflow import BabelDOCPDFLoop
from pubtrans.m3.workflow import PlannedTranslation
from pubtrans.m3.workflow import RenderPhase
from pubtrans.m4.artifacts import FinalPDFStore
from pubtrans.m4.errors import ArtifactStoreConflictError
from pubtrans.m4.model import ArtifactCategory
from pubtrans.m4.model import ArtifactVerdict
from pubtrans.m4.store import VerificationStore
from pubtrans.m4.verifier import PDFArtifactVerifier


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


class FixturePlanner:
    def plan(self, document) -> PlannedTranslation:
        researcher = actor(ActorRole.TERMINOLOGY_RESEARCHER, "pdf-term-r")
        term_reviewer = actor(ActorRole.BILINGUAL_REVIEWER, "pdf-term-v")
        evidence = tuple(
            TermEvidence.create(
                target_form="世界",
                stance=EvidenceStance.SUPPORTS,
                kind=(
                    EvidenceKind.AUTHORITY_TERMBANK
                    if index == 0
                    else EvidenceKind.CORPUS_ATTESTATION
                ),
                tier=(
                    EvidenceTier.A_AUTHORITY
                    if index == 0
                    else EvidenceTier.C_CORPUS
                ),
                source_key=source_key,
                source_uri=f"https://example.test/{source_key}",
                source_title=source_key,
                excerpt="world 对应世界",
                retrieved_on="2026-07-17",
                sense_match=True,
                domain_match=True,
            )
            for index, source_key in enumerate(("authority", "corpus"))
        )
        candidate = TargetTermCandidate.create(
            target_form="世界",
            semantic_fit=SemanticFit.CONFIRMED,
            conventionality=Conventionality.ESTABLISHED,
            rationale="Mainstream rendering for the fixture's general sense.",
            evidence=evidence,
        )
        decision = TermDecision.create(
            project_key=document.project.project_key,
            source_term="world",
            sense_id="world.general.fixture.v1",
            concept_definition="The general world referenced by the publication.",
            domain="general publication",
            candidates=(candidate,),
            selected_candidate_id=candidate.candidate_id,
            confidence=DecisionConfidence.VERIFIED,
            rationale="Authority and corpus evidence agree.",
            researcher_actor_key=researcher.actor_key,
            reviewer_actor_key=term_reviewer.actor_key,
        )
        occurrences = []
        for unit in document.units:
            if "world" not in unit.source_text:
                continue
            start = unit.source_text.index("world")
            occurrences.append(
                TermOccurrence.create(
                    term_key=decision.term_key,
                    unit=unit,
                    source_start=start,
                    source_end=start + 5,
                )
            )
        term = TermRevision.create(
            decision=decision,
            treatment=TermTreatment.TRANSLATE_WITH_SOURCE_FIRST,
            rationale="Show the source on first use only.",
            occurrences=occurrences,
        )
        terminology = TerminologySnapshot.create(document, (term,))

        baseline = LaneSpec.create(
            label="fixture-baseline",
            actor=actor(ActorRole.TRANSLATOR, "pdf-translator-a"),
        )
        independent = LaneSpec.create(
            label="fixture-formula-risk",
            actor=actor(ActorRole.TRANSLATOR, "pdf-translator-b"),
        )
        routes = []
        for unit in document.units:
            has_formula = any(
                item.kind.value == "formula" for item in unit.placeholders.specs
            )
            routes.append(
                UnitRoute.create(
                    unit_key=unit.unit_key,
                    unit_revision=unit.unit_revision,
                    risk_level=RiskLevel.R3 if has_formula else RiskLevel.R1,
                    lanes=(baseline, independent) if has_formula else (baseline,),
                    reasons=("protected mathematical content",) if has_formula else (),
                )
            )
        plan = KernelPlan.create(
            document=document,
            terminology=terminology,
            context_policy=ContextPolicy.create(
                before_records=3,
                after_records=3,
                max_neighbor_characters=4000,
            ),
            source_brief=None,
            lanes=(baseline, independent),
            routes=routes,
            reviewer=actor(ActorRole.BILINGUAL_REVIEWER, "pdf-review"),
            adjudicator=actor(ActorRole.ADJUDICATOR, "pdf-adjudicate"),
            editor=actor(ActorRole.CHINESE_EDITOR, "pdf-edit"),
            verifier=actor(ActorRole.FINAL_VERIFIER, "pdf-verify"),
            global_reviewer=actor(ActorRole.GLOBAL_REVIEWER, "pdf-global"),
        )
        return PlannedTranslation(terminology=terminology, plan=plan)


class FixtureServices:
    def __init__(self, document):
        self.units = {item.unit_key: item for item in document.units}
        self.calls: Counter[str] = Counter()

    @property
    def bundle(self) -> ServiceBundle:
        return ServiceBundle(self, self, self, self, self, self)

    def generate(self, request):
        self.calls["generate"] += 1
        unit = self.units[request.stage.context.unit_key]
        text = unit.source_text
        applications = []
        for guidance in request.stage.terminology:
            text = text.replace(
                guidance.term.source_term,
                guidance.directive.required_rendering,
                1,
            )
        for source, target in (
            ("Hello", "你好"),
            ("publication", "出版"),
            ("Short", "简短"),
            ("Hi", "嗨"),
            ("Equation", "方程"),
        ):
            text = text.replace(source, target)
        for guidance in request.stage.terminology:
            start = text.index(guidance.directive.required_rendering)
            applications.append(
                ApplicationDraft(
                    occurrence_key=guidance.directive.occurrence_key,
                    target_start=start,
                    target_end=start + len(guidance.directive.required_rendering),
                )
            )
        return CandidateDraft(
            rendered_target=RenderedTargetDraft(text, tuple(applications)),
            translator_note="deterministic licensed fixture translation",
        )

    def review(self, request):
        self.calls["review"] += 1
        return ReviewDraft(
            findings=(),
            recommended_option_keys=(request.options[0].option_key,),
            summary="Fixture translation is complete and structurally sound.",
        )

    def adjudicate(self, request):
        self.calls["adjudicate"] += 1
        option = request.options[0]
        return AdjudicationDraft(
            mode=AdjudicationMode.SELECT,
            selected_option_key=option.option_key,
            rendered_target=target_draft_from_rendered(option.rendered_target),
            resolutions=(),
            rationale="The first blind option preserves meaning and structure.",
        )

    def edit(self, request):
        self.calls["edit"] += 1
        raw = request.adjudication_payload["rendered_target"]
        assert isinstance(raw, dict)
        target = RenderedTarget.from_payload(raw)
        return EditDraft(
            rendered_target=target_draft_from_rendered(target),
            summary="No further fixture edit is required.",
        )

    def verify(self, request):
        self.calls["verify"] += 1
        edit = EditRevision.from_payload(request.edit_payload)
        return VerificationDraft(
            verdict=VerificationVerdict.PASS,
            edit_impact=(
                EditImpactVerdict.IMPROVES
                if edit.changed
                else EditImpactVerdict.EQUIVALENT
            ),
            findings=(),
            summary="Bilingual and placeholder verification passed.",
        )

    def review_document(self, _request):
        self.calls["global"] += 1
        return GlobalReviewDraft(
            verdict=VerificationVerdict.PASS,
            findings=(),
            summary="Terminology and register are consistent across the fixture.",
        )


class FixtureServiceFactory:
    def __init__(self):
        self.instances = []

    def create(self, *, store, document, planned):
        underlying = FixtureServices(document)
        self.instances.append(underlying)
        budget = BudgetPolicy.create(
            scope_key=planned.plan.plan_key,
            max_attempted_calls=100,
            max_estimated_tokens=2_000_000,
            max_estimated_microusd=1_000_000,
        )
        return ResilientServices(
            underlying.bundle,
            ResilientExecutor(
                store,
                owner_id="m3-fixture-worker",
                retry_policy=RetryPolicy(
                    max_attempts=2,
                    base_delay_seconds=0,
                    max_delay_seconds=0,
                ),
                sleeper=lambda _seconds: None,
            ),
            budget,
        ).bundle


class RenderFixture:
    def __init__(self, source_path: Path, root: Path):
        self.source_path = source_path
        self.root = root
        self.calls = 0

    def __call__(self, provider, phase: RenderPhase):
        self.calls += 1
        return translate(
            TranslationConfig(
                translator=None,
                input_file=self.source_path,
                lang_in="en",
                lang_out="zh-Hans",
                doc_layout_model=SyntheticLayoutModel(),
                output_dir=self.root / f"output-{self.calls}-{phase.value.lower()}",
                working_dir=self.root / f"work-{self.calls}-{phase.value.lower()}",
                no_dual=True,
                no_mono=False,
                skip_scanned_detection=True,
                watermark_output_mode=WatermarkOutputMode.NoWatermark,
                document_translation_provider=provider,
                document_translation_profile={"layout_model_sha256": "2" * 64},
            )
        )


def create_source_pdf(path: Path) -> None:
    source = pymupdf.open()
    first = source.new_page(width=420, height=300)
    logo = pymupdf.Pixmap(
        pymupdf.csRGB,
        pymupdf.IRect(0, 0, 20, 20),
        False,
    )
    logo.clear_with(0x3366CC)
    first.insert_image(
        pymupdf.Rect(330, 40, 370, 80),
        stream=logo.tobytes("png"),
    )
    first.insert_text((50, 65), "Hello publication world.", fontsize=14)
    first.insert_text((50, 115), "Short: Hi", fontsize=12)
    first.insert_text(
        (50, 165),
        "Equation: E = mc2",
        fontsize=12,
        fontname="Times-Italic",
    )
    second = source.new_page(width=420, height=300)
    second.insert_text((50, 70), "Hello world again.", fontsize=13)
    source.save(path)
    source.close()


def test_real_pdf_runs_prepare_quality_bus_and_babeldoc_restore(tmp_path: Path) -> None:
    font_path = Path(
        "/workspace/bersani_delivery_run/layout/fonts/source-han-serif-sc/"
        "SourceHanSerifSC-Regular.otf"
    )
    if not font_path.is_file():
        pytest.skip("Source Han Serif SC integration font is unavailable")
    source_path = tmp_path / "publication-fixture.pdf"
    create_source_pdf(source_path)
    factory = FixtureServiceFactory()
    renderer = RenderFixture(source_path, tmp_path)
    loop = BabelDOCPDFLoop(
        database_path=tmp_path / "project.sqlite3",
        planner=FixturePlanner(),
        service_factory=factory,
    )

    def font_family(_language):
        return {
            "normal": ["fixture-cjk"],
            "script": ["fixture-cjk"],
            "fallback": ["fixture-cjk"],
            "base": ["fixture-cjk"],
        }

    def font_and_metadata(_font_name):
        return font_path, {
            "ascent": 0.88,
            "descent": -0.12,
            "encoding_length": 2,
        }

    with (
        patch("babeldoc.assets.assets.get_font_family", font_family),
        patch("babeldoc.assets.assets.get_font_and_metadata", font_and_metadata),
        patch(
            "babeldoc.assets.assets.get_cmap_data",
            lambda _name: {"u": "", "r": [], "c": []},
        ),
    ):
        result = loop.run(renderer)
        resumed = loop.run(renderer)

    assert result.prepared_capture_was_needed
    assert result.rendered.mono_pdf_path is not None
    assert result.rendered.mono_pdf_path.is_file()
    assert len(result.release.outcomes) == 4
    assert any(
        "世界（world）" in item.rendered_target.target_text
        for item in result.release.outcomes
    )
    assert any(
        "方程" in item.rendered_target.target_text for item in result.release.outcomes
    )
    assert sum(factory.instances[0].calls.values()) > 0
    assert not resumed.prepared_capture_was_needed
    assert resumed.release == result.release
    assert sum(factory.instances[1].calls.values()) == 0

    rendered = pymupdf.open(result.rendered.mono_pdf_path)
    try:
        assert rendered.page_count == 2
        output_text = "".join(page.get_text() for page in rendered)
    finally:
        rendered.close()
    normalized_output = " ".join(output_text.replace("\N{NO-BREAK SPACE}", " ").split())
    assert "E = mc2" in normalized_output
    assert "world" in normalized_output

    prepared_store = PreparedArtifactStore(
        (tmp_path / "project.sqlite3").with_suffix(".sqlite3.artifacts")
    )
    final_store = FinalPDFStore(tmp_path / "verified-final-pdfs")
    with VerificationStore(
        tmp_path / "project.sqlite3",
        prepared_store,
        final_store,
    ) as store:
        document = store.load_document()
        report = PDFArtifactVerifier().verify(
            document=document,
            release=result.release,
            source_pdf=source_path,
            target_pdf=result.rendered.mono_pdf_path,
        )
        assert report.verdict is ArtifactVerdict.PASS
        assert report.findings == ()
        assert report.metrics["unit_literals_total"] >= 4
        assert report.metrics["source_anchors_found"] == 1
        reference = store.record_report(
            document=document,
            release=result.release,
            report=report,
            target_pdf_path=result.rendered.mono_pdf_path,
        )
        assert store.load_report(report.report_id) == report
        assert store.load_active_artifact() == (report, reference)
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 5

        missing_page_path = tmp_path / "missing-page.pdf"
        changed = pymupdf.open(result.rendered.mono_pdf_path)
        changed.delete_page(changed.page_count - 1)
        changed.save(missing_page_path)
        changed.close()
        missing_page = PDFArtifactVerifier().verify(
            document=document,
            release=result.release,
            source_pdf=source_path,
            target_pdf=missing_page_path,
        )
        assert missing_page.verdict is ArtifactVerdict.BLOCK
        assert ArtifactCategory.PAGE_COUNT in {
            item.category for item in missing_page.findings
        }

        missing_text_path = tmp_path / "missing-approved-text.pdf"
        changed = pymupdf.open(result.rendered.mono_pdf_path)
        text_rects = changed[0].search_for("出版")
        assert text_rects
        for rectangle in text_rects:
            changed[0].add_redact_annot(rectangle)
        changed[0].apply_redactions()
        changed.save(missing_text_path)
        changed.close()
        missing_text = PDFArtifactVerifier().verify(
            document=document,
            release=result.release,
            source_pdf=source_path,
            target_pdf=missing_text_path,
        )
        assert ArtifactCategory.TEXT_COVERAGE in {
            item.category for item in missing_text.findings
        }

        missing_anchor_path = tmp_path / "missing-formula.pdf"
        changed = pymupdf.open(result.rendered.mono_pdf_path)
        formula_rects = changed[0].search_for("E = mc2")
        assert formula_rects
        for rectangle in formula_rects:
            changed[0].add_redact_annot(rectangle)
        changed[0].apply_redactions()
        changed.save(missing_anchor_path)
        changed.close()
        missing_anchor = PDFArtifactVerifier().verify(
            document=document,
            release=result.release,
            source_pdf=source_path,
            target_pdf=missing_anchor_path,
        )
        assert ArtifactCategory.PROTECTED_ANCHOR in {
            item.category for item in missing_anchor.findings
        }

        missing_image_path = tmp_path / "missing-image.pdf"
        changed = pymupdf.open(result.rendered.mono_pdf_path)
        images = changed[0].get_images(full=True)
        assert images
        changed[0].delete_image(images[0][0])
        changed.save(missing_image_path)
        changed.close()
        missing_image = PDFArtifactVerifier().verify(
            document=document,
            release=result.release,
            source_pdf=source_path,
            target_pdf=missing_image_path,
        )
        assert ArtifactCategory.IMAGE_COVERAGE in {
            item.category for item in missing_image.findings
        }

        corrupt_path = tmp_path / "corrupt.pdf"
        corrupt_path.write_bytes(b"not a PDF")
        corrupt = PDFArtifactVerifier().verify(
            document=document,
            release=result.release,
            source_pdf=source_path,
            target_pdf=corrupt_path,
        )
        assert corrupt.verdict is ArtifactVerdict.BLOCK
        assert ArtifactCategory.FILE_INTEGRITY in {
            item.category for item in corrupt.findings
        }
        with pytest.raises(
            ArtifactStoreConflictError,
            match="blocked artifact",
        ):
            store.record_report(
                document=document,
                release=result.release,
                report=corrupt,
                target_pdf_path=corrupt_path,
            )
        assert store.load_report(corrupt.report_id) is None

        final_store.path_for(reference).write_bytes(b"corrupt")
        with pytest.raises(ArtifactStoreConflictError, match="size differs"):
            store.load_active_artifact()
