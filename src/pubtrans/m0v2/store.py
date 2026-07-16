"""Versioned SQLite authority for prepared snapshots and approval revisions."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

from pubtrans.schema import LATEST_SCHEMA_VERSION
from pubtrans.schema import M0_SCHEMA_VERSION

from .artifacts import ArtifactRef
from .artifacts import PreparedArtifactStore
from .canonical import canonical_json
from .errors import LegacyDatabaseError
from .errors import ProjectBindingError
from .errors import SnapshotConflictError
from .errors import StateConflictError
from .errors import StaleApprovalError
from .errors import UnsupportedSchemaError
from .model import ApprovalRevision
from .model import PreparedDocument
from .model import ProjectBinding
from .provider import validate_approval_set


SCHEMA_VERSION = M0_SCHEMA_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS m0v2_project (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    project_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m0v2_prepared_context (
    context_key TEXT PRIMARY KEY,
    project_key TEXT NOT NULL REFERENCES m0v2_project(project_key) ON DELETE RESTRICT,
    part_key TEXT NOT NULL,
    context_payload_json TEXT NOT NULL,
    artifact_ref_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_key, part_key)
);

CREATE TABLE IF NOT EXISTS m0v2_snapshot (
    snapshot_key TEXT PRIMARY KEY,
    project_key TEXT NOT NULL REFERENCES m0v2_project(project_key) ON DELETE RESTRICT,
    part_key TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL UNIQUE,
    snapshot_payload_json TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    artifact_ref_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_key, part_key)
);

CREATE TABLE IF NOT EXISTS m0v2_record (
    record_key TEXT PRIMARY KEY,
    snapshot_key TEXT NOT NULL REFERENCES m0v2_snapshot(snapshot_key) ON DELETE RESTRICT,
    page_ordinal INTEGER NOT NULL,
    paragraph_ordinal INTEGER NOT NULL,
    record_revision TEXT NOT NULL,
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(snapshot_key, page_ordinal, paragraph_ordinal)
);

CREATE TABLE IF NOT EXISTS m0v2_unit (
    unit_key TEXT PRIMARY KEY,
    record_key TEXT NOT NULL UNIQUE REFERENCES m0v2_record(record_key) ON DELETE RESTRICT,
    unit_revision TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS m0v2_approval_revision (
    approval_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id TEXT NOT NULL UNIQUE,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    unit_revision TEXT NOT NULL,
    target_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m0v2_active_approval (
    unit_key TEXT PRIMARY KEY REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    approval_id TEXT NOT NULL UNIQUE REFERENCES m0v2_approval_revision(approval_id) ON DELETE RESTRICT,
    activated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m0v2_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class ProjectStore:
    """Persist prepared parts, immutable snapshots, and append-only approvals."""

    def __init__(
        self,
        database_path: str | Path,
        artifact_store: PreparedArtifactStore,
    ):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_store = artifact_store
        self.connection = sqlite3.connect(self.database_path, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        current = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current > LATEST_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                "database schema "
                f"{current} is newer than supported {LATEST_SCHEMA_VERSION}"
            )

        user_tables = {
            row[0]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if current == 0 and user_tables:
            raise LegacyDatabaseError(
                "unversioned or M0 v1 database requires explicit migration"
            )
        if current not in (0, M0_SCHEMA_VERSION, LATEST_SCHEMA_VERSION):
            raise LegacyDatabaseError(
                f"database schema {current} has no automatic M0 v2 migration"
            )
        if current == 0:
            with self.connection:
                self.connection.executescript(SCHEMA)
                self.connection.execute(
                    f"PRAGMA user_version = {M0_SCHEMA_VERSION}"
                )
        else:
            self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ProjectStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def register_document(
        self,
        document: PreparedDocument,
        artifact: ArtifactRef,
    ) -> None:
        if artifact.sha256 != document.snapshot.artifact_sha256:
            raise SnapshotConflictError(
                "artifact reference does not match prepared snapshot"
            )
        self.artifact_store.verify(artifact)

        project_json = canonical_json(document.project.as_payload())
        snapshot_json = canonical_json(document.snapshot.as_payload())
        manifest_json = canonical_json(document.as_payload())
        artifact_json = canonical_json(artifact.as_payload())

        with self._transaction():
            self._bind_project(document.project, project_json)

            prepared_context = self.connection.execute(
                "SELECT artifact_ref_json FROM m0v2_prepared_context "
                "WHERE project_key = ? AND part_key = ?",
                (document.project.project_key, document.snapshot.part_key),
            ).fetchone()
            if (
                prepared_context is not None
                and prepared_context["artifact_ref_json"] != artifact_json
            ):
                raise SnapshotConflictError(
                    "prepared context artifact differs from document snapshot"
                )

            existing_snapshot = self.connection.execute(
                "SELECT * FROM m0v2_snapshot WHERE project_key = ? AND part_key = ?",
                (document.project.project_key, document.snapshot.part_key),
            ).fetchone()
            if existing_snapshot is not None:
                stored = (
                    existing_snapshot["snapshot_key"],
                    existing_snapshot["manifest_sha256"],
                    existing_snapshot["snapshot_payload_json"],
                    existing_snapshot["manifest_json"],
                    existing_snapshot["artifact_ref_json"],
                )
                incoming = (
                    document.snapshot.snapshot_key,
                    document.manifest_sha256,
                    snapshot_json,
                    manifest_json,
                    artifact_json,
                )
                if stored != incoming:
                    raise SnapshotConflictError(
                        "prepared snapshot differs from the registered snapshot"
                    )
                self._assert_record_set(document)
                return

            self.connection.execute(
                """
                INSERT INTO m0v2_snapshot(
                    snapshot_key, project_key, part_key, manifest_sha256,
                    snapshot_payload_json, manifest_json, artifact_ref_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document.snapshot.snapshot_key,
                    document.project.project_key,
                    document.snapshot.part_key,
                    document.manifest_sha256,
                    snapshot_json,
                    manifest_json,
                    artifact_json,
                ),
            )
            for record in document.records:
                self.connection.execute(
                    """
                    INSERT INTO m0v2_record(
                        record_key, snapshot_key, page_ordinal,
                        paragraph_ordinal, record_revision, disposition,
                        reason, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.record_key,
                        document.snapshot.snapshot_key,
                        record.locator.page_ordinal,
                        record.locator.paragraph_ordinal,
                        record.record_revision,
                        record.disposition.value,
                        record.reason.value,
                        canonical_json(record.as_payload()),
                    ),
                )
                if record.unit is not None:
                    self.connection.execute(
                        """
                        INSERT INTO m0v2_unit(
                            unit_key, record_key, unit_revision, payload_json
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            record.unit.unit_key,
                            record.record_key,
                            record.unit.unit_revision,
                            canonical_json(record.unit.as_payload()),
                        ),
                    )
            self._event(
                "snapshot_registered",
                document.snapshot.snapshot_key,
                {
                    "manifest_sha256": document.manifest_sha256,
                    "records": len(document.records),
                    "units": len(document.units),
                    "blockers": len(document.blockers),
                },
            )

    def register_prepared_artifact(
        self,
        *,
        project: ProjectBinding,
        context_key: str,
        part_key: str,
        context_payload: dict[str, object],
        artifact: ArtifactRef,
    ) -> None:
        if context_payload.get("context_key") != context_key:
            raise SnapshotConflictError("prepared context key payload mismatch")
        if context_payload.get("project_key") != project.project_key:
            raise ProjectBindingError("prepared context project payload mismatch")
        if context_payload.get("part_key") != part_key:
            raise SnapshotConflictError("prepared context part payload mismatch")
        self.artifact_store.verify(artifact)
        project_json = canonical_json(project.as_payload())
        context_json = canonical_json(context_payload)
        artifact_json = canonical_json(artifact.as_payload())

        with self._transaction():
            self._bind_project(project, project_json)
            existing = self.connection.execute(
                "SELECT context_key, context_payload_json, artifact_ref_json "
                "FROM m0v2_prepared_context "
                "WHERE project_key = ? AND part_key = ?",
                (project.project_key, part_key),
            ).fetchone()
            if existing is not None:
                stored = (
                    existing["context_key"],
                    existing["context_payload_json"],
                    existing["artifact_ref_json"],
                )
                incoming = (context_key, context_json, artifact_json)
                if stored != incoming:
                    raise SnapshotConflictError(
                        "prepared artifact differs for the registered context"
                    )
                return
            self.connection.execute(
                "INSERT INTO m0v2_prepared_context("
                "context_key, project_key, part_key, context_payload_json, "
                "artifact_ref_json) VALUES (?, ?, ?, ?, ?)",
                (
                    context_key,
                    project.project_key,
                    part_key,
                    context_json,
                    artifact_json,
                ),
            )
            self._event(
                "prepared_artifact_registered",
                context_key,
                {
                    "part_key": part_key,
                    "artifact_sha256": artifact.sha256,
                },
            )

    def load_prepared_artifact(
        self,
        context_key: str,
    ) -> tuple[dict[str, object], ArtifactRef] | None:
        row = self.connection.execute(
            "SELECT context_payload_json, artifact_ref_json "
            "FROM m0v2_prepared_context WHERE context_key = ?",
            (context_key,),
        ).fetchone()
        if row is None:
            return None
        context = json.loads(row["context_payload_json"])
        if not isinstance(context, dict):
            raise SnapshotConflictError("stored prepared context is malformed")
        artifact = ArtifactRef.from_payload(json.loads(row["artifact_ref_json"]))
        self.artifact_store.verify(artifact)
        return context, artifact

    def _assert_record_set(self, document: PreparedDocument) -> None:
        rows = self.connection.execute(
            "SELECT record_key, payload_json FROM m0v2_record "
            "WHERE snapshot_key = ? ORDER BY page_ordinal, paragraph_ordinal",
            (document.snapshot.snapshot_key,),
        ).fetchall()
        stored = [(row["record_key"], row["payload_json"]) for row in rows]
        incoming = [
            (record.record_key, canonical_json(record.as_payload()))
            for record in document.records
        ]
        if stored != incoming:
            raise SnapshotConflictError("stored paragraph record set is inconsistent")

    def record_approvals(
        self,
        document: PreparedDocument,
        approvals: Iterable[ApprovalRevision],
        *,
        activate: bool = True,
    ) -> None:
        approvals = self._validate_approvals(document, approvals)

        with self._transaction():
            self._record_approvals_in_transaction(
                document,
                approvals,
                activate=activate,
            )

    def _validate_approvals(
        self,
        document: PreparedDocument,
        approvals: Iterable[ApprovalRevision],
    ) -> tuple[ApprovalRevision, ...]:
        document.require_unblocked()
        approvals = tuple(approvals)
        approval_ids = [approval.approval_id for approval in approvals]
        unit_keys = [approval.unit_key for approval in approvals]
        if len(approval_ids) != len(set(approval_ids)):
            raise StateConflictError("duplicate approval revision in one transaction")
        if len(unit_keys) != len(set(unit_keys)):
            raise StateConflictError("multiple new approvals for one unit transaction")

        unit_by_key = {unit.unit_key: unit for unit in document.units}
        for approval in approvals:
            unit = unit_by_key.get(approval.unit_key)
            if unit is None:
                raise StaleApprovalError(
                    f"approval references unknown unit: {approval.unit_key}"
                )
            if approval.unit_revision != unit.unit_revision:
                raise StaleApprovalError(
                    f"approval references stale unit: {approval.unit_key}"
                )
            unit.placeholders.validate(
                approval.target_text,
                require_nonempty_styles=True,
            )
        return approvals

    def _record_approvals_in_transaction(
        self,
        document: PreparedDocument,
        approvals: tuple[ApprovalRevision, ...],
        *,
        activate: bool,
    ) -> None:
        """Record already-validated approvals inside the caller's transaction."""
        self._assert_registered(document)
        for approval in approvals:
            payload_json = canonical_json(approval.as_payload())
            existing = self.connection.execute(
                "SELECT payload_json FROM m0v2_approval_revision "
                "WHERE approval_id = ?",
                (approval.approval_id,),
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO m0v2_approval_revision(
                        approval_id, unit_key, unit_revision,
                        target_sha256, payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        approval.approval_id,
                        approval.unit_key,
                        approval.unit_revision,
                        approval.target_sha256,
                        payload_json,
                    ),
                )
                self._event(
                    "approval_recorded",
                    approval.approval_id,
                    {"unit_key": approval.unit_key},
                )
            elif existing["payload_json"] != payload_json:
                raise StateConflictError(
                    f"immutable approval changed: {approval.approval_id}"
                )

            if activate:
                active = self.connection.execute(
                    "SELECT approval_id FROM m0v2_active_approval "
                    "WHERE unit_key = ?",
                    (approval.unit_key,),
                ).fetchone()
                previous = active["approval_id"] if active is not None else None
                if previous != approval.approval_id:
                    self.connection.execute(
                        """
                        INSERT INTO m0v2_active_approval(unit_key, approval_id)
                        VALUES (?, ?)
                        ON CONFLICT(unit_key) DO UPDATE SET
                            approval_id = excluded.approval_id,
                            activated_at = CURRENT_TIMESTAMP
                        """,
                        (approval.unit_key, approval.approval_id),
                    )
                    self._event(
                        "approval_activated",
                        approval.unit_key,
                        {
                            "approval_id": approval.approval_id,
                            "supersedes": previous,
                        },
                    )

    def resolve(self, document: PreparedDocument) -> tuple[ApprovalRevision, ...]:
        self._assert_registered(document)
        artifact = self.load_artifact_reference(document.snapshot.part_key)
        self.artifact_store.verify(artifact)
        rows = self.connection.execute(
            """
            SELECT revision.payload_json
            FROM m0v2_active_approval AS active
            JOIN m0v2_approval_revision AS revision
              ON revision.approval_id = active.approval_id
            JOIN m0v2_unit AS unit
              ON unit.unit_key = active.unit_key
            JOIN m0v2_record AS record
              ON record.record_key = unit.record_key
            WHERE record.snapshot_key = ?
            ORDER BY revision.unit_key
            """,
            (document.snapshot.snapshot_key,),
        ).fetchall()
        approvals = [
            ApprovalRevision.from_payload(json.loads(row["payload_json"]))
            for row in rows
        ]
        return validate_approval_set(document, approvals)

    def load_document(self, part_key: str = "whole-document") -> PreparedDocument:
        row = self.connection.execute(
            "SELECT manifest_json FROM m0v2_snapshot WHERE part_key = ?",
            (part_key,),
        ).fetchone()
        if row is None:
            raise SnapshotConflictError("no prepared snapshot is registered")
        return PreparedDocument.from_payload(json.loads(row["manifest_json"]))

    def load_artifact_reference(
        self,
        part_key: str = "whole-document",
    ) -> ArtifactRef:
        row = self.connection.execute(
            "SELECT artifact_ref_json FROM m0v2_snapshot WHERE part_key = ?",
            (part_key,),
        ).fetchone()
        if row is None:
            raise SnapshotConflictError("no prepared snapshot is registered")
        return ArtifactRef.from_payload(json.loads(row["artifact_ref_json"]))

    def approval_history(self, unit_key: str) -> tuple[ApprovalRevision, ...]:
        rows = self.connection.execute(
            "SELECT payload_json FROM m0v2_approval_revision "
            "WHERE unit_key = ? ORDER BY approval_seq",
            (unit_key,),
        ).fetchall()
        return tuple(
            ApprovalRevision.from_payload(json.loads(row["payload_json"]))
            for row in rows
        )

    def status(self) -> dict[str, int]:
        def count(query: str) -> int:
            return int(self.connection.execute(query).fetchone()[0])

        units = count("SELECT COUNT(*) FROM m0v2_unit")
        active = count("SELECT COUNT(*) FROM m0v2_active_approval")
        return {
            "prepared_contexts": count(
                "SELECT COUNT(*) FROM m0v2_prepared_context"
            ),
            "records": count("SELECT COUNT(*) FROM m0v2_record"),
            "units": units,
            "safe_exclusions": count(
                "SELECT COUNT(*) FROM m0v2_record "
                "WHERE disposition = 'safe_exclusion'"
            ),
            "blockers": count(
                "SELECT COUNT(*) FROM m0v2_record WHERE disposition = 'blocker'"
            ),
            "approval_revisions": count(
                "SELECT COUNT(*) FROM m0v2_approval_revision"
            ),
            "active_approvals": active,
            "pending": units - active,
        }

    def _bind_project(
        self,
        project: ProjectBinding,
        project_json: str | None = None,
    ) -> None:
        project_json = project_json or canonical_json(project.as_payload())
        existing = self.connection.execute(
            "SELECT project_key, payload_json FROM m0v2_project WHERE singleton = 1"
        ).fetchone()
        if existing is None:
            self.connection.execute(
                "INSERT INTO m0v2_project(singleton, project_key, payload_json) "
                "VALUES (1, ?, ?)",
                (project.project_key, project_json),
            )
            self._event(
                "project_bound",
                project.project_key,
                {"project_key": project.project_key},
            )
            return
        if (
            existing["project_key"] != project.project_key
            or existing["payload_json"] != project_json
        ):
            raise ProjectBindingError(
                "project database is bound to a different source/profile"
            )

    def _assert_registered(self, document: PreparedDocument) -> None:
        row = self.connection.execute(
            "SELECT project_key, snapshot_key, manifest_sha256, manifest_json "
            "FROM m0v2_snapshot WHERE snapshot_key = ?",
            (document.snapshot.snapshot_key,),
        ).fetchone()
        if row is None:
            raise SnapshotConflictError("prepared document is not registered")
        if row["project_key"] != document.project.project_key:
            raise ProjectBindingError("registered project key differs")
        if row["snapshot_key"] != document.snapshot.snapshot_key:
            raise SnapshotConflictError("registered snapshot key differs")
        if row["manifest_sha256"] != document.manifest_sha256:
            raise SnapshotConflictError("registered manifest digest differs")
        if row["manifest_json"] != canonical_json(document.as_payload()):
            raise SnapshotConflictError("registered manifest payload differs")

    def _event(self, event_type: str, subject_key: str | None, payload: object) -> None:
        self.connection.execute(
            "INSERT INTO m0v2_event(event_type, subject_key, payload_json) "
            "VALUES (?, ?, ?)",
            (event_type, subject_key, canonical_json(payload)),
        )
