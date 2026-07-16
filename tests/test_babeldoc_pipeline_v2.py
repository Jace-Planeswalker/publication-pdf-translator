from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest

from babeldoc.docvision.base_doclayout import YoloResult
from babeldoc.format.pdf.high_level import translate
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.translation_config import WatermarkOutputMode

from pubtrans.babeldoc_adapter import SQLiteDocumentTranslationProvider
from pubtrans.m0v2.errors import ApprovalSetError
from pubtrans.m0v2.model import ApprovalRevision


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


def test_sqlite_provider_resumes_then_renders_synthetic_pdf(
    tmp_path: Path,
) -> None:
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if not font_path.is_file():
        pytest.skip("system DejaVu Sans fixture is unavailable")

    source_path = tmp_path / "source.pdf"
    source = pymupdf.open()
    page = source.new_page(width=420, height=300)
    page.insert_text(
        (50, 70),
        "Hello publication world.",
        fontsize=14,
        fontname="helv",
    )
    page.insert_text((50, 110), "Short: Hi", fontsize=12, fontname="helv")
    page.insert_text(
        (50, 150),
        "Equation: E = mc2",
        fontsize=12,
        fontname="Times-Italic",
    )
    source.save(source_path)
    source.close()

    database = tmp_path / "project.sqlite3"

    def config_for_run(run: int) -> TranslationConfig:
        return TranslationConfig(
            translator=None,
            input_file=source_path,
            lang_in="en",
            lang_out="zh-Hans",
            doc_layout_model=SyntheticLayoutModel(),
            output_dir=tmp_path / f"output-{run}",
            working_dir=tmp_path / f"work-{run}",
            no_dual=True,
            no_mono=False,
            skip_scanned_detection=True,
            watermark_output_mode=WatermarkOutputMode.NoWatermark,
            document_translation_provider=SQLiteDocumentTranslationProvider(
                database
            ),
            document_translation_profile={"layout_model_sha256": "1" * 64},
        )

    def font_family(_language):
        return {
            "normal": ["test-sans"],
            "script": ["test-sans"],
            "fallback": ["test-sans"],
            "base": ["test-sans"],
        }

    def font_and_metadata(_font_name):
        return font_path, {
            "ascent": 0.928,
            "descent": -0.236,
            "encoding_length": 2,
        }

    with (
        patch("babeldoc.assets.assets.get_font_family", font_family),
        patch(
            "babeldoc.assets.assets.get_font_and_metadata",
            font_and_metadata,
        ),
        patch(
            "babeldoc.assets.assets.get_cmap_data",
            lambda _name: {"u": "", "r": [], "c": []},
        ),
    ):
        with pytest.raises(ApprovalSetError):
            translate(config_for_run(1))

        provider = SQLiteDocumentTranslationProvider(database)
        with provider._store() as store:
            prepared = store.load_document()
            assert store.status()["prepared_contexts"] == 1
            assert store.status()["pending"] == 3
            assert any(
                spec.kind.value == "formula"
                for unit in prepared.units
                for spec in unit.placeholders.specs
            )
            approvals = tuple(
                ApprovalRevision.create(
                    unit=unit,
                    target_text=f"Translated {unit.source_text}",
                    origin="integration-adjudicator",
                )
                for unit in prepared.units
            )
            store.record_approvals(prepared, approvals)
            first_snapshot_key = prepared.snapshot.snapshot_key

        result = translate(config_for_run(2))

    assert result.mono_pdf_path is not None
    assert result.mono_pdf_path.is_file()
    rendered = pymupdf.open(result.mono_pdf_path)
    try:
        assert rendered.page_count == 1
        output_text = "".join(page.get_text() for page in rendered)
    finally:
        rendered.close()
    assert output_text.count("Translated") == 3
    assert "Short: Hi" in output_text
    assert "E = mc2" in output_text

    resumed_provider = SQLiteDocumentTranslationProvider(database)
    with resumed_provider._store() as store:
        resumed = store.load_document()
        assert resumed.snapshot.snapshot_key == first_snapshot_key
        assert store.status()["prepared_contexts"] == 1
        assert store.status()["pending"] == 0
