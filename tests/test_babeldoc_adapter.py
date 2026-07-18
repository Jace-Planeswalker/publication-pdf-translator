from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    ENGINE_COMMIT,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    BoxFingerprint,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    DocumentTranslationBlockedError,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    DocumentTranslationContext,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    ParagraphRecord,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PlaceholderContract,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedILArtifact,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedSnapshot,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedTranslationDocument,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedTranslationUnit,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    UnitLocator,
)

from pubtrans.babeldoc_adapter import SQLiteDocumentTranslationProvider
from pubtrans.m0v2.errors import ApprovalSetError
from pubtrans.m0v2.errors import SnapshotConflictError
from pubtrans.m0v2.model import ApprovalRevision


def make_external_document(
    *,
    blocker: bool = False,
) -> tuple[
    DocumentTranslationContext,
    PreparedILArtifact,
    PreparedTranslationDocument,
]:
    context = DocumentTranslationContext.create(
        original_pdf_sha256="a" * 64,
        prepared_pdf_sha256="b" * 64,
        source_language="en",
        target_language="zh-Hans",
        profile_name="publication",
        engine_name="BabelDOC",
        engine_version="0.6.4",
        engine_commit=ENGINE_COMMIT,
        extraction_profile={"layout_model_sha256": "c" * 64},
        part_key="whole-document",
    )
    artifact = PreparedILArtifact.create(context, "<document/>\n")
    snapshot = PreparedSnapshot.create(context, artifact)
    locator = UnitLocator(0, 0)
    box = BoxFingerprint.create(1, 2, 100, 20)
    placeholders = PlaceholderContract.create(
        f"PT2-{snapshot.snapshot_key[:12]}",
        (),
    )
    unit = PreparedTranslationUnit.create(
        snapshot=snapshot,
        locator=locator,
        source_text="Hello",
        placeholders=placeholders,
        layout_label="text",
        vertical=False,
        box=box,
    )
    records = [
        ParagraphRecord(
            snapshot_key=snapshot.snapshot_key,
            locator=locator,
            disposition="translatable",
            reason="TEXT",
            source_text="Hello",
            layout_label="text",
            vertical=False,
            box=box,
            unit=unit,
        )
    ]
    if blocker:
        records.append(
            ParagraphRecord(
                snapshot_key=snapshot.snapshot_key,
                locator=UnitLocator(0, 1),
                disposition="blocker",
                reason="VERTICAL_TEXT_UNSUPPORTED",
                source_text="Vertical text",
                layout_label="text",
                vertical=True,
                box=BoxFingerprint.create(110, 2, 130, 100),
                unit=None,
            )
        )
    document = PreparedTranslationDocument.create(
        snapshot=snapshot,
        page_paragraph_counts=(len(records),),
        records=records,
    )
    return context, artifact, document


class BabelDOCAdapterV2Tests(unittest.TestCase):
    def test_artifact_capture_survives_provider_restart(self) -> None:
        context, artifact, _document = make_external_document()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            first = SQLiteDocumentTranslationProvider(database)
            first.save_prepared_artifact(context, artifact)

            second = SQLiteDocumentTranslationProvider(database)
            self.assertEqual(second.load_prepared_artifact(context), artifact)
            with second._store() as store:
                self.assertEqual(store.status()["prepared_contexts"], 1)

    def test_first_manifest_capture_fails_closed_until_approved(self) -> None:
        context, artifact, document = make_external_document()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            provider = SQLiteDocumentTranslationProvider(database)
            provider.save_prepared_artifact(context, artifact)

            with self.assertRaises(ApprovalSetError):
                provider.translate_document(document)

            with provider._store() as store:
                self.assertEqual(store.status()["records"], 1)
                self.assertEqual(store.status()["units"], 1)
                self.assertEqual(store.status()["pending"], 1)

    def test_approved_revision_returns_exact_babeldoc_envelope(self) -> None:
        context, artifact, document = make_external_document()
        with tempfile.TemporaryDirectory() as directory:
            provider = SQLiteDocumentTranslationProvider(
                Path(directory) / "project.sqlite3"
            )
            provider.save_prepared_artifact(context, artifact)
            with self.assertRaises(ApprovalSetError):
                provider.translate_document(document)

            with provider._store() as store:
                core_document = store.load_document()
                approval = ApprovalRevision.create(
                    unit=core_document.units[0],
                    target_text="你好",
                    origin="test-adjudicator",
                )
                store.record_approvals(core_document, (approval,))

            result = provider.translate_document(document)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].approval_id, approval.approval_id)
            self.assertEqual(result[0].unit_key, approval.unit_key)
            self.assertEqual(result[0].unit_revision, approval.unit_revision)
            self.assertEqual(result[0].target_text, "你好")
            self.assertEqual(type(result[0]).__module__.split(".")[0], "babeldoc")

    def test_blocker_manifest_is_persisted_and_resolution_stops(self) -> None:
        context, artifact, document = make_external_document(blocker=True)
        with tempfile.TemporaryDirectory() as directory:
            provider = SQLiteDocumentTranslationProvider(
                Path(directory) / "project.sqlite3"
            )
            provider.save_prepared_artifact(context, artifact)
            with self.assertRaises(DocumentTranslationBlockedError):
                provider.translate_document(document)
            with provider._store() as store:
                self.assertEqual(store.status()["blockers"], 1)

    def test_same_context_cannot_be_rebound_to_different_artifact(self) -> None:
        context, artifact, _document = make_external_document()
        with tempfile.TemporaryDirectory() as directory:
            provider = SQLiteDocumentTranslationProvider(
                Path(directory) / "project.sqlite3"
            )
            provider.save_prepared_artifact(context, artifact)
            changed = PreparedILArtifact.create(context, "<document changed='1'/>\n")
            with self.assertRaises(SnapshotConflictError):
                provider.save_prepared_artifact(context, changed)


if __name__ == "__main__":
    unittest.main()
