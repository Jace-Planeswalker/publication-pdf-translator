"""Schema-v5 immutable artifact reports and atomic final-PDF activation."""

from __future__ import annotations

import json
from pathlib import Path

from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m1.workflow import Release
from pubtrans.m2.store import RecoveryStore
from pubtrans.schema import M2_SCHEMA_VERSION
from pubtrans.schema import M4_SCHEMA_VERSION

from .artifacts import FinalPDFRef
from .artifacts import FinalPDFStore
from .errors import ArtifactStoreConflictError
from .model import ArtifactReport
from .model import ArtifactVerdict


M4_SCHEMA = """
CREATE TABLE IF NOT EXISTS m4_artifact_report (
    report_id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL
        REFERENCES m1_release(release_id) ON DELETE RESTRICT,
    project_key TEXT NOT NULL
        REFERENCES m0v2_project(project_key) ON DELETE RESTRICT,
    target_pdf_sha256 TEXT NOT NULL,
    verdict TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    artifact_ref_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(release_id, target_pdf_sha256)
);

CREATE TABLE IF NOT EXISTS m4_active_artifact (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    report_id TEXT NOT NULL UNIQUE
        REFERENCES m4_artifact_report(report_id) ON DELETE RESTRICT,
    activated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m4_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class VerificationStore(RecoveryStore):
    """Recovery store extended with the verified final-artifact authority."""

    def __init__(
        self,
        database_path: str | Path,
        prepared_artifact_store: PreparedArtifactStore,
        final_pdf_store: FinalPDFStore,
    ) -> None:
        super().__init__(database_path, prepared_artifact_store)
        self.final_pdf_store = final_pdf_store
        self._initialize_m4_schema()

    def _initialize_m4_schema(self) -> None:
        current = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current == M2_SCHEMA_VERSION:
            try:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + M4_SCHEMA
                    + f"\nPRAGMA user_version = {M4_SCHEMA_VERSION};\nCOMMIT;"
                )
            except BaseException:
                self.connection.rollback()
                raise
        elif current >= M4_SCHEMA_VERSION:
            self.connection.executescript(M4_SCHEMA)
        else:
            raise ArtifactStoreConflictError(
                f"cannot initialize M4 on database schema {current}"
            )

    def record_report(
        self,
        *,
        document: PreparedDocument,
        release: Release,
        report: ArtifactReport,
        target_pdf_path: str | Path,
        activate: bool = True,
    ) -> FinalPDFRef:
        self._validate_bindings(document=document, release=release, report=report)
        payload = Path(target_pdf_path).read_bytes()
        reference = self.final_pdf_store.reference_for(payload)
        if reference.sha256 != report.target_pdf_sha256:
            raise ArtifactStoreConflictError(
                "target PDF differs from the artifact verification report"
            )
        reference = self.final_pdf_store.put(payload)
        report_json = canonical_json(report.as_payload())
        reference_json = canonical_json(reference.as_payload())

        with self._transaction():
            self._assert_registered(document)
            release_row = self.connection.execute(
                "SELECT payload_json FROM m1_release WHERE release_id = ?",
                (release.release_id,),
            ).fetchone()
            if release_row is None:
                raise ArtifactStoreConflictError(
                    "artifact report release is not registered"
                )
            if str(release_row["payload_json"]) != canonical_json(release.as_payload()):
                raise ArtifactStoreConflictError("registered release payload differs")

            existing = self.connection.execute(
                "SELECT report_id, payload_json, artifact_ref_json "
                "FROM m4_artifact_report WHERE report_id = ? OR "
                "(release_id = ? AND target_pdf_sha256 = ?)",
                (report.report_id, report.release_id, report.target_pdf_sha256),
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO m4_artifact_report("
                    "report_id, release_id, project_key, target_pdf_sha256, "
                    "verdict, payload_json, artifact_ref_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        report.report_id,
                        report.release_id,
                        report.project_key,
                        report.target_pdf_sha256,
                        report.verdict.value,
                        report_json,
                        reference_json,
                    ),
                )
                self._m4_event(
                    "artifact_report_recorded",
                    report.report_id,
                    {
                        "release_id": report.release_id,
                        "target_pdf_sha256": report.target_pdf_sha256,
                        "verdict": report.verdict.value,
                    },
                )
            elif (
                str(existing["report_id"]) != report.report_id
                or str(existing["payload_json"]) != report_json
                or str(existing["artifact_ref_json"]) != reference_json
            ):
                raise ArtifactStoreConflictError(
                    "artifact report slot is immutable and already differs"
                )

            if activate:
                if report.verdict is not ArtifactVerdict.PASS:
                    raise ArtifactStoreConflictError(
                        "a blocked artifact cannot become the active final PDF"
                    )
                active = self.connection.execute(
                    "SELECT report_id FROM m4_active_artifact WHERE singleton = 1"
                ).fetchone()
                previous = str(active["report_id"]) if active is not None else None
                if previous != report.report_id:
                    self.connection.execute(
                        "INSERT INTO m4_active_artifact(singleton, report_id) "
                        "VALUES (1, ?) ON CONFLICT(singleton) DO UPDATE SET "
                        "report_id = excluded.report_id, "
                        "activated_at = CURRENT_TIMESTAMP",
                        (report.report_id,),
                    )
                    self._m4_event(
                        "final_pdf_activated",
                        report.report_id,
                        {"supersedes": previous},
                    )
        return reference

    def load_report(self, report_id: str) -> ArtifactReport | None:
        row = self.connection.execute(
            "SELECT payload_json FROM m4_artifact_report WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise ArtifactStoreConflictError("artifact report payload is malformed")
        return ArtifactReport.from_payload(payload)

    def load_active_artifact(self) -> tuple[ArtifactReport, FinalPDFRef] | None:
        row = self.connection.execute(
            "SELECT report.payload_json, report.artifact_ref_json "
            "FROM m4_active_artifact AS active "
            "JOIN m4_artifact_report AS report "
            "ON report.report_id = active.report_id WHERE active.singleton = 1"
        ).fetchone()
        if row is None:
            return None
        report_payload = json.loads(str(row["payload_json"]))
        reference_payload = json.loads(str(row["artifact_ref_json"]))
        if not isinstance(report_payload, dict) or not isinstance(
            reference_payload, dict
        ):
            raise ArtifactStoreConflictError("active artifact payload is malformed")
        report = ArtifactReport.from_payload(report_payload)
        reference = FinalPDFRef.from_payload(reference_payload)
        if report.verdict is not ArtifactVerdict.PASS:
            raise ArtifactStoreConflictError("active artifact has a blocking report")
        if reference.sha256 != report.target_pdf_sha256:
            raise ArtifactStoreConflictError("active artifact digest binding differs")
        self.final_pdf_store.verify(reference)
        return report, reference

    def _validate_bindings(
        self,
        *,
        document: PreparedDocument,
        release: Release,
        report: ArtifactReport,
    ) -> None:
        if (
            release.project_key != document.project.project_key
            or release.snapshot_key != document.snapshot.snapshot_key
            or release.manifest_sha256 != document.manifest_sha256
        ):
            raise ArtifactStoreConflictError("release does not match source document")
        if (
            report.release_id != release.release_id
            or report.project_key != document.project.project_key
            or report.source_pdf_sha256 != document.project.original_pdf_sha256
        ):
            raise ArtifactStoreConflictError("artifact report binding differs")

    def _m4_event(
        self,
        event_type: str,
        subject_key: str | None,
        payload: dict[str, object],
    ) -> None:
        self.connection.execute(
            "INSERT INTO m4_event(event_type, subject_key, payload_json) "
            "VALUES (?, ?, ?)",
            (event_type, subject_key, canonical_json(payload)),
        )
