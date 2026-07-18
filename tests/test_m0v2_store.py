from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from m0v2_helpers import ARTIFACT_BYTES
from m0v2_helpers import make_document
from m0v2_helpers import make_project
from m0v2_helpers import make_snapshot
from m0v2_helpers import target_for
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.errors import ApprovalSetError
from pubtrans.m0v2.errors import ArtifactIntegrityError
from pubtrans.m0v2.errors import DocumentBlockedError
from pubtrans.m0v2.errors import LegacyDatabaseError
from pubtrans.m0v2.errors import ProjectBindingError
from pubtrans.m0v2.errors import SnapshotConflictError
from pubtrans.m0v2.errors import UnsupportedSchemaError
from pubtrans.m0v2.model import ApprovalRevision
from pubtrans.m0v2.store import ProjectStore


def approval_for(unit, text: str, origin: str = "test") -> ApprovalRevision:
    return ApprovalRevision.create(
        unit=unit,
        target_text=target_for(unit.placeholders, text=text),
        origin=origin,
    )


class ArtifactStoreTests(unittest.TestCase):
    def test_content_addressed_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreparedArtifactStore(directory)
            first = store.put(ARTIFACT_BYTES)
            second = store.put(ARTIFACT_BYTES)
            self.assertEqual(first, second)
            self.assertEqual(store.get(first), ARTIFACT_BYTES)

    def test_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreparedArtifactStore(directory)
            reference = store.put(ARTIFACT_BYTES)
            store.path_for(reference).write_bytes(b"tampered")
            with self.assertRaises(ArtifactIntegrityError):
                store.get(reference)

    def test_failed_atomic_replace_does_not_publish_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = PreparedArtifactStore(directory)
            reference = store.reference_for(ARTIFACT_BYTES)
            with mock.patch.object(os, "replace", side_effect=OSError("boom")):
                with self.assertRaises(OSError):
                    store.put(ARTIFACT_BYTES)
            self.assertFalse(store.path_for(reference).exists())
            self.assertEqual(list(store.path_for(reference).parent.glob("*.tmp")), [])


class ProjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifacts = PreparedArtifactStore(self.root / "artifacts")
        self.reference = self.artifacts.put(ARTIFACT_BYTES)
        self.database = self.root / "state" / "project.sqlite3"
        self.document = make_document()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_register_partial_resume_and_exact_resolve(self) -> None:
        first = approval_for(self.document.units[0], "世界一")
        second = approval_for(self.document.units[1], "世界二")
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            store.record_approvals(self.document, [first])
            self.assertEqual(store.status()["pending"], 1)
            with self.assertRaises(ApprovalSetError):
                store.resolve(self.document)

        with ProjectStore(self.database, self.artifacts) as resumed:
            resumed.register_document(self.document, self.reference)
            resumed.record_approvals(self.document, [second])
            self.assertEqual(resumed.resolve(self.document), (first, second))
            self.assertEqual(resumed.status()["pending"], 0)

    def test_corrected_approval_supersedes_without_deleting_history(self) -> None:
        unit = self.document.units[0]
        first = approval_for(unit, "旧译", origin="editor-v1")
        corrected = approval_for(unit, "新译", origin="editor-v2")
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            store.record_approvals(self.document, [first])
            store.record_approvals(self.document, [corrected])
            history = store.approval_history(unit.unit_key)
            self.assertEqual(history, (first, corrected))
            self.assertEqual(store.status()["approval_revisions"], 2)
            active = store.connection.execute(
                "SELECT approval_id FROM m0v2_active_approval WHERE unit_key = ?",
                (unit.unit_key,),
            ).fetchone()[0]
            self.assertEqual(active, corrected.approval_id)

    def test_project_binding_mismatch_is_rejected(self) -> None:
        other = make_document(project=make_project(pdf_sha="c" * 64))
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            with self.assertRaises(ProjectBindingError):
                store.register_document(other, self.reference)

    def test_snapshot_manifest_mismatch_is_rejected(self) -> None:
        changed = make_document(repeated=False)
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            with self.assertRaises(SnapshotConflictError):
                store.register_document(changed, self.reference)

    def test_loaded_document_and_artifact_are_verified(self) -> None:
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            self.assertEqual(store.load_document(), self.document)
            self.assertEqual(store.load_artifact_reference(), self.reference)
            self.artifacts.path_for(self.reference).write_bytes(b"corrupt")
            with self.assertRaises(ArtifactIntegrityError):
                store.resolve(self.document)

    def test_one_project_can_register_multiple_split_parts(self) -> None:
        project = self.document.project
        part_one = make_document(
            project=project,
            snapshot=make_snapshot(project, part_key="part-0001"),
            repeated=False,
        )
        part_two = make_document(
            project=project,
            snapshot=make_snapshot(project, part_key="part-0002"),
            repeated=False,
        )
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(part_one, self.reference)
            store.register_document(part_two, self.reference)
            approval_one = approval_for(part_one.units[0], "分片一")
            approval_two = approval_for(part_two.units[0], "分片二")
            store.record_approvals(part_one, (approval_one,))
            store.record_approvals(part_two, (approval_two,))
            self.assertEqual(store.load_document("part-0001"), part_one)
            self.assertEqual(store.load_document("part-0002"), part_two)
            self.assertEqual(store.resolve(part_one), (approval_one,))
            self.assertEqual(store.resolve(part_two), (approval_two,))
            self.assertEqual(store.status()["units"], 2)

    def test_blocked_document_is_recorded_but_cannot_receive_approvals(self) -> None:
        blocked = make_document(blocker=True)
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(blocked, self.reference)
            self.assertEqual(store.status()["blockers"], 1)
            with self.assertRaises(DocumentBlockedError):
                store.record_approvals(blocked, [])

    def test_bad_approval_batch_is_atomic(self) -> None:
        first = approval_for(self.document.units[0], "有效")
        other = make_document(project=make_project(pdf_sha="d" * 64))
        alien = approval_for(other.units[0], "外来")
        with ProjectStore(self.database, self.artifacts) as store:
            store.register_document(self.document, self.reference)
            with self.assertRaises(Exception):
                store.record_approvals(self.document, [first, alien])
            self.assertEqual(store.status()["approval_revisions"], 0)


class SchemaTests(unittest.TestCase):
    def test_unversioned_v1_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE unit(unit_id TEXT PRIMARY KEY)")
            connection.commit()
            connection.close()
            with self.assertRaises(LegacyDatabaseError):
                ProjectStore(path, PreparedArtifactStore(Path(directory) / "artifacts"))

    def test_newer_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA user_version = 999")
            connection.commit()
            connection.close()
            with self.assertRaises(UnsupportedSchemaError):
                ProjectStore(path, PreparedArtifactStore(Path(directory) / "artifacts"))


if __name__ == "__main__":
    unittest.main()
