from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    DocumentTranslationContext,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedTranslationUnit,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    make_unit_id,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    placeholder_signature,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    sha256_text,
)

from pubtrans.babeldoc_adapter import SQLiteDocumentTranslationProvider
from pubtrans.errors import UnitSetMismatchError
from pubtrans.models import ApprovedTranslation
from pubtrans.state import ProjectState


DOCUMENT_HASH = "a" * 64


def make_external_unit() -> PreparedTranslationUnit:
    token = "<b0>"
    closing_token = "</b0>"
    source_text = f"Hello {token}world{closing_token}"
    source_hash = sha256_text(source_text)
    tokens = (token, closing_token)
    pairs = ((token, closing_token),)
    return PreparedTranslationUnit(
        unit_id=make_unit_id(
            document_sha256=DOCUMENT_HASH,
            page_number=1,
            paragraph_debug_id="paragraph-1",
            reading_order=0,
            source_sha256=source_hash,
        ),
        page_number=1,
        paragraph_debug_id="paragraph-1",
        reading_order=0,
        source_text=source_text,
        source_sha256=source_hash,
        required_placeholders=tokens,
        paired_placeholders=pairs,
        placeholder_signature=placeholder_signature(tokens, pairs),
        layout_label="text",
    )


class BabelDOCAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.unit = make_external_unit()
        self.context = DocumentTranslationContext(
            document_sha256=DOCUMENT_HASH,
            lang_in="en",
            lang_out="zh-CN",
        )

    def test_first_call_captures_units_and_fails_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            provider = SQLiteDocumentTranslationProvider(database)

            with self.assertRaises(UnitSetMismatchError):
                provider.translate_document((self.unit,), self.context)

            with ProjectState(database) as state:
                self.assertEqual(
                    state.status(), {"units": 1, "approved": 0, "pending": 1}
                )
                self.assertEqual(state.load_units()[0].unit_id, self.unit.unit_id)

    def test_approved_map_round_trip_uses_babeldoc_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            provider = SQLiteDocumentTranslationProvider(database)
            with self.assertRaises(UnitSetMismatchError):
                provider.translate_document((self.unit,), self.context)

            with ProjectState(database) as state:
                core_unit = state.load_units()[0]
                state.record_approvals(
                    [core_unit],
                    [
                        ApprovedTranslation(
                            unit_id=core_unit.unit_id,
                            source_sha256=core_unit.source_sha256,
                            placeholder_signature=core_unit.placeholder_signature,
                            target_text="你好 <b0>世界</b0>",
                        )
                    ],
                )

            results = provider.translate_document((self.unit,), self.context)
            self.assertEqual(results[0].target_text, "你好 <b0>世界</b0>")
            self.assertEqual(type(results[0]).__module__.split(".")[0], "babeldoc")


if __name__ == "__main__":
    unittest.main()
