"""One-command prepare, plan, translate, render, verify, and release runtime."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m1.workflow import Release
from pubtrans.m3.workflow import BabelDOCPDFLoop
from pubtrans.m3.workflow import RenderPhase
from pubtrans.m4.artifacts import FinalPDFRef
from pubtrans.m4.artifacts import FinalPDFStore
from pubtrans.m4.model import ArtifactReport
from pubtrans.m4.model import ArtifactVerdict
from pubtrans.m4.store import VerificationStore
from pubtrans.m4.verifier import PDFArtifactVerifier

from .config import ProductConfig
from .errors import M5Error
from .evidence import EvidenceCatalog
from .evidence import PageFetcher
from .factory import ProductionServiceFactory
from .openai import ResearchModelClient
from .openai import StructuredModelClient
from .planner import ProductionPlanner


class FinalArtifactBlockedError(M5Error):
    def __init__(self, report: ArtifactReport, report_path: Path):
        self.report = report
        self.report_path = report_path
        super().__init__(
            f"final PDF failed {len(report.findings)} artifact quality finding(s)"
        )


@dataclass(frozen=True, slots=True)
class ProductResult:
    project_directory: Path
    output_pdf_path: Path
    release: Release
    artifact_report: ArtifactReport
    final_reference: FinalPDFRef
    report_path: Path
    prepared_capture_was_needed: bool

    def as_payload(self) -> dict[str, object]:
        return {
            "state": "RELEASED",
            "project_directory": str(self.project_directory),
            "output_pdf_path": str(self.output_pdf_path),
            "release_id": self.release.release_id,
            "artifact_report_id": self.artifact_report.report_id,
            "verification_report_path": str(self.report_path),
            "source_pdf_sha256": self.artifact_report.source_pdf_sha256,
            "target_pdf_sha256": self.artifact_report.target_pdf_sha256,
            "prepared_capture_was_needed": self.prepared_capture_was_needed,
        }


class PublicationTranslationRuntime:
    def __init__(
        self,
        *,
        config: ProductConfig,
        structured_client: StructuredModelClient,
        research_client: ResearchModelClient | None = None,
        evidence_catalog: EvidenceCatalog | None = None,
        page_fetcher: PageFetcher | None = None,
        layout_model: object | None = None,
        layout_profile: dict[str, object] | None = None,
        skip_scanned_detection: bool = False,
        primary_font_family: str | None = None,
    ) -> None:
        self.config = config
        self.structured_client = structured_client
        self.research_client = research_client
        self.evidence_catalog = evidence_catalog or EvidenceCatalog()
        self.page_fetcher = page_fetcher
        self.layout_model = layout_model
        self.layout_profile = layout_profile or {
            "layout_model": "babeldoc-auto-v1",
            "product_runtime": "pubtrans-m5-v1",
        }
        self.skip_scanned_detection = skip_scanned_detection
        self.primary_font_family = primary_font_family

    def run(
        self,
        *,
        source_pdf: str | Path,
        project_directory: str | Path,
    ) -> ProductResult:
        source_path = Path(source_pdf).resolve()
        project = Path(project_directory).resolve()
        if not source_path.is_file():
            raise M5Error("source PDF does not exist")
        project.mkdir(parents=True, exist_ok=True)
        state_directory = project / "state"
        state_directory.mkdir(parents=True, exist_ok=True)
        database = state_directory / "project.sqlite3"
        prepared_directory = state_directory / "prepared-artifacts"
        final_directory = state_directory / "verified-final-pdfs"

        planner = ProductionPlanner(
            config=self.config,
            structured_client=self.structured_client,
            research_client=self.research_client,
            evidence_catalog=self.evidence_catalog,
            page_fetcher=self.page_fetcher,
        )
        loop = BabelDOCPDFLoop(
            database_path=database,
            planner=planner,
            service_factory=ProductionServiceFactory(
                config=self.config,
                client=self.structured_client,
            ),
            artifact_directory=prepared_directory,
        )
        renderer = _BabelDOCRenderer(
            source_path=source_path,
            project_directory=project,
            config=self.config,
            layout_model=self.layout_model,
            layout_profile=self.layout_profile,
            skip_scanned_detection=self.skip_scanned_detection,
            primary_font_family=self.primary_font_family,
        )
        result = loop.run(renderer)
        target_path = _mono_path(result.rendered)
        output = project / "output" / (
            f"{_filename_component(source_path.stem)}."
            f"{_filename_component(self.config.target_language)}.verified.pdf"
        )
        report_path = project / "output" / "verification-report.json"
        prepared_store = PreparedArtifactStore(prepared_directory)
        final_store = FinalPDFStore(final_directory)
        with VerificationStore(database, prepared_store, final_store) as store:
            document = store.load_document()
            report = PDFArtifactVerifier().verify(
                document=document,
                release=result.release,
                source_pdf=source_path,
                target_pdf=target_path,
            )
            reference = store.record_report(
                document=document,
                release=result.release,
                report=report,
                target_pdf_path=target_path,
                activate=report.verdict is ArtifactVerdict.PASS,
            )
            _publish_bytes(
                (
                    canonical_json(
                        {
                            "state": (
                                "RELEASED"
                                if report.verdict is ArtifactVerdict.PASS
                                else "BLOCKED"
                            ),
                            "output_pdf_path": str(output),
                            "report": report.as_payload(),
                            "final_reference": reference.as_payload(),
                        }
                    )
                    + "\n"
                ).encode("utf-8"),
                report_path,
            )
            if report.verdict is not ArtifactVerdict.PASS:
                raise FinalArtifactBlockedError(report, report_path)

        _publish_bytes(final_store.get(reference), output)
        return ProductResult(
            project_directory=project,
            output_pdf_path=output,
            release=result.release,
            artifact_report=report,
            final_reference=reference,
            report_path=report_path,
            prepared_capture_was_needed=result.prepared_capture_was_needed,
        )


class _BabelDOCRenderer:
    def __init__(
        self,
        *,
        source_path: Path,
        project_directory: Path,
        config: ProductConfig,
        layout_model: object | None,
        layout_profile: dict[str, object],
        skip_scanned_detection: bool,
        primary_font_family: str | None,
    ) -> None:
        self.source_path = source_path
        self.project_directory = project_directory
        self.config = config
        self.layout_model = layout_model
        self.layout_profile = layout_profile
        self.skip_scanned_detection = skip_scanned_detection
        self.primary_font_family = primary_font_family

    def __call__(self, provider, phase: RenderPhase):
        from babeldoc.format.pdf.high_level import translate
        from babeldoc.format.pdf.translation_config import TranslationConfig
        from babeldoc.format.pdf.translation_config import WatermarkOutputMode

        return translate(
            TranslationConfig(
                translator=None,
                input_file=self.source_path,
                lang_in=self.config.source_language,
                lang_out=self.config.target_language,
                doc_layout_model=self.layout_model,
                output_dir=self.project_directory / "build" / phase.value.lower(),
                working_dir=self.project_directory / "work" / phase.value.lower(),
                no_dual=True,
                no_mono=False,
                watermark_output_mode=WatermarkOutputMode.NoWatermark,
                skip_scanned_detection=self.skip_scanned_detection,
                auto_extract_glossary=False,
                save_auto_extracted_glossary=False,
                primary_font_family=self.primary_font_family,
                document_translation_provider=provider,
                document_translation_profile_name=self.config.profile_name,
                document_translation_profile=self.layout_profile,
            )
        )


def _mono_path(rendered: Any) -> Path:
    value = getattr(rendered, "mono_pdf_path", None)
    if value is None:
        raise M5Error("BabelDOC did not emit a monolingual translated PDF")
    path = Path(value)
    if not path.is_file():
        raise M5Error("BabelDOC reported a missing translated PDF")
    return path


def _publish_bytes(payload: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _filename_component(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return result or "publication"
