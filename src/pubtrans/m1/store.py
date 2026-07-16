"""Schema-v3 immutable persistence and atomic M1 release activation."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.store import ProjectStore
from pubtrans.schema import M0_SCHEMA_VERSION
from pubtrans.schema import M1_SCHEMA_VERSION

from .errors import ReleaseContractError
from .errors import StageConflictError
from .plan import ContextPackage
from .plan import KernelPlan
from .terminology import TerminologySnapshot
from .workflow import Adjudication
from .workflow import Candidate
from .workflow import EditRevision
from .workflow import GlobalReport
from .workflow import Release
from .workflow import ReviewReport
from .workflow import UnitOutcome
from .workflow import VerificationReport


M1_SCHEMA = """
CREATE TABLE IF NOT EXISTS m1_terminology_snapshot (
    snapshot_id TEXT PRIMARY KEY,
    project_key TEXT NOT NULL
        REFERENCES m0v2_project(project_key) ON DELETE RESTRICT,
    manifest_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m1_plan (
    plan_key TEXT PRIMARY KEY,
    snapshot_key TEXT NOT NULL
        REFERENCES m0v2_snapshot(snapshot_key) ON DELETE RESTRICT,
    terminology_snapshot_id TEXT NOT NULL
        REFERENCES m1_terminology_snapshot(snapshot_id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m1_context (
    context_key TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key)
);

CREATE TABLE IF NOT EXISTS m1_candidate (
    candidate_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    lane_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key, lane_key)
);

CREATE TABLE IF NOT EXISTS m1_review (
    report_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key)
);

CREATE TABLE IF NOT EXISTS m1_adjudication (
    adjudication_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key)
);

CREATE TABLE IF NOT EXISTS m1_edit (
    edit_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key)
);

CREATE TABLE IF NOT EXISTS m1_verification (
    report_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key)
);

CREATE TABLE IF NOT EXISTS m1_outcome (
    outcome_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    unit_key TEXT NOT NULL REFERENCES m0v2_unit(unit_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(plan_key, unit_key)
);

CREATE TABLE IF NOT EXISTS m1_global_report (
    report_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL UNIQUE
        REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m1_release (
    release_id TEXT PRIMARY KEY,
    plan_key TEXT NOT NULL UNIQUE
        REFERENCES m1_plan(plan_key) ON DELETE RESTRICT,
    global_report_id TEXT NOT NULL UNIQUE
        REFERENCES m1_global_report(report_id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m1_active_release (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    release_id TEXT NOT NULL UNIQUE
        REFERENCES m1_release(release_id) ON DELETE RESTRICT,
    activated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m1_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


T = TypeVar("T")


class KernelStore(ProjectStore):
    """One database authority for M0 preparation and the M1 quality bus."""

    def __init__(
        self,
        database_path: str | Path,
        artifact_store: PreparedArtifactStore,
    ) -> None:
        super().__init__(database_path, artifact_store)
        self._initialize_m1_schema()

    def _initialize_m1_schema(self) -> None:
        current = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current == M0_SCHEMA_VERSION:
            try:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + M1_SCHEMA
                    + f"\nPRAGMA user_version = {M1_SCHEMA_VERSION};\nCOMMIT;"
                )
            except BaseException:
                self.connection.rollback()
                raise
        elif current == M1_SCHEMA_VERSION:
            self.connection.executescript(M1_SCHEMA)
        else:
            raise StageConflictError(
                f"cannot initialize M1 on database schema {current}"
            )

    def register_terminology(
        self,
        document: PreparedDocument,
        terminology: TerminologySnapshot,
    ) -> None:
        terminology.validate_against(document)
        payload = canonical_json(terminology.as_payload())
        with self._transaction():
            self._assert_registered(document)
            inserted = self._insert_immutable(
                table="m1_terminology_snapshot",
                key_column="snapshot_id",
                key=terminology.snapshot_id,
                payload=payload,
                slot_where="snapshot_id = ?",
                slot_values=(terminology.snapshot_id,),
                insert_sql=(
                    "INSERT INTO m1_terminology_snapshot("
                    "snapshot_id, project_key, manifest_sha256, payload_json) "
                    "VALUES (?, ?, ?, ?)"
                ),
                insert_values=(
                    terminology.snapshot_id,
                    terminology.project_key,
                    terminology.manifest_sha256,
                    payload,
                ),
            )
            if inserted:
                self._m1_event(
                    "terminology_registered",
                    terminology.snapshot_id,
                    {"terms": len(terminology.terms)},
                )

    def register_plan(
        self,
        document: PreparedDocument,
        terminology: TerminologySnapshot,
        plan: KernelPlan,
    ) -> None:
        plan.validate_against(document=document, terminology=terminology)
        payload = canonical_json(plan.as_payload())
        with self._transaction():
            self._assert_registered(document)
            self._require_payload(
                "m1_terminology_snapshot",
                "snapshot_id",
                terminology.snapshot_id,
                canonical_json(terminology.as_payload()),
            )
            inserted = self._insert_immutable(
                table="m1_plan",
                key_column="plan_key",
                key=plan.plan_key,
                payload=payload,
                slot_where="plan_key = ?",
                slot_values=(plan.plan_key,),
                insert_sql=(
                    "INSERT INTO m1_plan("
                    "plan_key, snapshot_key, terminology_snapshot_id, payload_json) "
                    "VALUES (?, ?, ?, ?)"
                ),
                insert_values=(
                    plan.plan_key,
                    plan.snapshot_key,
                    plan.terminology_snapshot_id,
                    payload,
                ),
            )
            if inserted:
                self._m1_event(
                    "plan_registered",
                    plan.plan_key,
                    {"units": len(plan.unit_revisions)},
                )

    def record_context(self, context: ContextPackage) -> None:
        self._record_unit_stage(
            table="m1_context",
            key_column="context_key",
            key=context.context_key,
            plan_key=context.plan_key,
            unit_key=context.unit_key,
            payload=context.as_payload(),
            event_type="context_recorded",
        )

    def record_candidate(self, candidate: Candidate) -> None:
        payload = canonical_json(candidate.as_payload())
        with self._transaction():
            inserted = self._insert_immutable(
                table="m1_candidate",
                key_column="candidate_id",
                key=candidate.candidate_id,
                payload=payload,
                slot_where="plan_key = ? AND unit_key = ? AND lane_key = ?",
                slot_values=(
                    candidate.plan_key,
                    candidate.unit_key,
                    candidate.lane_key,
                ),
                insert_sql=(
                    "INSERT INTO m1_candidate("
                    "candidate_id, plan_key, unit_key, lane_key, payload_json) "
                    "VALUES (?, ?, ?, ?, ?)"
                ),
                insert_values=(
                    candidate.candidate_id,
                    candidate.plan_key,
                    candidate.unit_key,
                    candidate.lane_key,
                    payload,
                ),
            )
            if inserted:
                self._m1_event(
                    "candidate_recorded",
                    candidate.candidate_id,
                    {
                        "unit_key": candidate.unit_key,
                        "lane_key": candidate.lane_key,
                    },
                )

    def record_review(self, review: ReviewReport) -> None:
        self._record_unit_stage(
            table="m1_review",
            key_column="report_id",
            key=review.report_id,
            plan_key=review.plan_key,
            unit_key=review.unit_key,
            payload=review.as_payload(),
            event_type="review_recorded",
        )

    def record_adjudication(self, adjudication: Adjudication) -> None:
        self._record_unit_stage(
            table="m1_adjudication",
            key_column="adjudication_id",
            key=adjudication.adjudication_id,
            plan_key=adjudication.plan_key,
            unit_key=adjudication.unit_key,
            payload=adjudication.as_payload(),
            event_type="adjudication_recorded",
        )

    def record_edit(self, edit: EditRevision) -> None:
        self._record_unit_stage(
            table="m1_edit",
            key_column="edit_id",
            key=edit.edit_id,
            plan_key=edit.plan_key,
            unit_key=edit.unit_key,
            payload=edit.as_payload(),
            event_type="edit_recorded",
        )

    def record_verification(self, verification: VerificationReport) -> None:
        self._record_unit_stage(
            table="m1_verification",
            key_column="report_id",
            key=verification.report_id,
            plan_key=verification.plan_key,
            unit_key=verification.unit_key,
            payload=verification.as_payload(),
            event_type="verification_recorded",
        )

    def record_outcome(self, outcome: UnitOutcome) -> None:
        self._record_unit_stage(
            table="m1_outcome",
            key_column="outcome_id",
            key=outcome.outcome_id,
            plan_key=outcome.plan_key,
            unit_key=outcome.unit_key,
            payload=outcome.as_payload(),
            event_type="outcome_recorded",
        )

    def record_global_report(self, report: GlobalReport) -> None:
        payload = canonical_json(report.as_payload())
        with self._transaction():
            inserted = self._insert_immutable(
                table="m1_global_report",
                key_column="report_id",
                key=report.report_id,
                payload=payload,
                slot_where="plan_key = ?",
                slot_values=(report.plan_key,),
                insert_sql=(
                    "INSERT INTO m1_global_report("
                    "report_id, plan_key, payload_json) VALUES (?, ?, ?)"
                ),
                insert_values=(report.report_id, report.plan_key, payload),
            )
            if inserted:
                self._m1_event(
                    "global_report_recorded",
                    report.report_id,
                    {"plan_key": report.plan_key},
                )

    def record_release(
        self,
        *,
        document: PreparedDocument,
        plan: KernelPlan,
        release: Release,
        activate: bool = True,
    ) -> None:
        if (
            release.plan_key != plan.plan_key
            or release.project_key != document.project.project_key
            or release.snapshot_key != document.snapshot.snapshot_key
            or release.manifest_sha256 != document.manifest_sha256
        ):
            raise ReleaseContractError("release binding differs from its document plan")
        approvals = self._validate_approvals(document, release.approvals)
        outcome_by_unit = {item.unit_key: item for item in release.outcomes}
        for approval in approvals:
            outcome = outcome_by_unit.get(approval.unit_key)
            if outcome is None or approval.target_text != outcome.rendered_target.target_text:
                raise ReleaseContractError("release approval differs from unit outcome")
        payload = canonical_json(release.as_payload())

        with self._transaction():
            inserted = self._insert_immutable(
                table="m1_release",
                key_column="release_id",
                key=release.release_id,
                payload=payload,
                slot_where="plan_key = ?",
                slot_values=(release.plan_key,),
                insert_sql=(
                    "INSERT INTO m1_release("
                    "release_id, plan_key, global_report_id, payload_json) "
                    "VALUES (?, ?, ?, ?)"
                ),
                insert_values=(
                    release.release_id,
                    release.plan_key,
                    release.global_report_id,
                    payload,
                ),
            )
            if inserted:
                self._m1_event(
                    "release_recorded",
                    release.release_id,
                    {"plan_key": release.plan_key},
                )
            if activate:
                self._record_approvals_in_transaction(
                    document,
                    approvals,
                    activate=True,
                )
                active = self.connection.execute(
                    "SELECT release_id FROM m1_active_release WHERE singleton = 1"
                ).fetchone()
                previous = active["release_id"] if active is not None else None
                if previous != release.release_id:
                    self.connection.execute(
                        "INSERT INTO m1_active_release(singleton, release_id) "
                        "VALUES (1, ?) ON CONFLICT(singleton) DO UPDATE SET "
                        "release_id = excluded.release_id, "
                        "activated_at = CURRENT_TIMESTAMP",
                        (release.release_id,),
                    )
                    self._m1_event(
                        "release_activated",
                        release.release_id,
                        {"supersedes": previous},
                    )

    def load_terminology(self, snapshot_id: str) -> TerminologySnapshot | None:
        return self._load_one(
            "SELECT payload_json FROM m1_terminology_snapshot WHERE snapshot_id = ?",
            (snapshot_id,),
            TerminologySnapshot.from_payload,
        )

    def load_plan(self, plan_key: str) -> KernelPlan | None:
        return self._load_one(
            "SELECT payload_json FROM m1_plan WHERE plan_key = ?",
            (plan_key,),
            KernelPlan.from_payload,
        )

    def load_context(self, plan_key: str, unit_key: str) -> ContextPackage | None:
        return self._load_one(
            "SELECT payload_json FROM m1_context WHERE plan_key = ? AND unit_key = ?",
            (plan_key, unit_key),
            ContextPackage.from_payload,
        )

    def load_candidate(
        self,
        plan_key: str,
        unit_key: str,
        lane_key: str,
    ) -> Candidate | None:
        return self._load_one(
            "SELECT payload_json FROM m1_candidate "
            "WHERE plan_key = ? AND unit_key = ? AND lane_key = ?",
            (plan_key, unit_key, lane_key),
            Candidate.from_payload,
        )

    def load_review(self, plan_key: str, unit_key: str) -> ReviewReport | None:
        return self._load_one(
            "SELECT payload_json FROM m1_review WHERE plan_key = ? AND unit_key = ?",
            (plan_key, unit_key),
            ReviewReport.from_payload,
        )

    def load_adjudication(self, plan_key: str, unit_key: str) -> Adjudication | None:
        return self._load_one(
            "SELECT payload_json FROM m1_adjudication "
            "WHERE plan_key = ? AND unit_key = ?",
            (plan_key, unit_key),
            Adjudication.from_payload,
        )

    def load_edit(self, plan_key: str, unit_key: str) -> EditRevision | None:
        return self._load_one(
            "SELECT payload_json FROM m1_edit WHERE plan_key = ? AND unit_key = ?",
            (plan_key, unit_key),
            EditRevision.from_payload,
        )

    def load_verification(
        self,
        plan_key: str,
        unit_key: str,
    ) -> VerificationReport | None:
        return self._load_one(
            "SELECT payload_json FROM m1_verification "
            "WHERE plan_key = ? AND unit_key = ?",
            (plan_key, unit_key),
            VerificationReport.from_payload,
        )

    def load_outcome(self, plan_key: str, unit_key: str) -> UnitOutcome | None:
        return self._load_one(
            "SELECT payload_json FROM m1_outcome WHERE plan_key = ? AND unit_key = ?",
            (plan_key, unit_key),
            UnitOutcome.from_payload,
        )

    def load_outcomes(self, plan: KernelPlan) -> tuple[UnitOutcome, ...]:
        result: list[UnitOutcome] = []
        for unit_key, _revision in plan.unit_revisions:
            outcome = self.load_outcome(plan.plan_key, unit_key)
            if outcome is not None:
                result.append(outcome)
        return tuple(result)

    def load_global_report(self, plan_key: str) -> GlobalReport | None:
        return self._load_one(
            "SELECT payload_json FROM m1_global_report WHERE plan_key = ?",
            (plan_key,),
            GlobalReport.from_payload,
        )

    def load_release(self, plan_key: str) -> Release | None:
        return self._load_one(
            "SELECT payload_json FROM m1_release WHERE plan_key = ?",
            (plan_key,),
            Release.from_payload,
        )

    def load_active_release(self) -> Release | None:
        return self._load_one(
            "SELECT release.payload_json FROM m1_active_release AS active "
            "JOIN m1_release AS release ON release.release_id = active.release_id "
            "WHERE active.singleton = 1",
            (),
            Release.from_payload,
        )

    def m1_status(self, plan_key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for label, table in (
            ("contexts", "m1_context"),
            ("candidates", "m1_candidate"),
            ("reviews", "m1_review"),
            ("adjudications", "m1_adjudication"),
            ("edits", "m1_edit"),
            ("verifications", "m1_verification"),
            ("outcomes", "m1_outcome"),
            ("global_reports", "m1_global_report"),
            ("releases", "m1_release"),
        ):
            result[label] = int(
                self.connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE plan_key = ?",  # noqa: S608
                    (plan_key,),
                ).fetchone()[0]
            )
        return result

    def _record_unit_stage(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        plan_key: str,
        unit_key: str,
        payload: dict[str, object],
        event_type: str,
    ) -> None:
        payload_json = canonical_json(payload)
        sql = (
            f"INSERT INTO {table}("  # noqa: S608
            f"{key_column}, plan_key, unit_key, payload_json) VALUES (?, ?, ?, ?)"
        )
        with self._transaction():
            inserted = self._insert_immutable(
                table=table,
                key_column=key_column,
                key=key,
                payload=payload_json,
                slot_where="plan_key = ? AND unit_key = ?",
                slot_values=(plan_key, unit_key),
                insert_sql=sql,
                insert_values=(key, plan_key, unit_key, payload_json),
            )
            if inserted:
                self._m1_event(event_type, key, {"unit_key": unit_key})

    def _insert_immutable(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        payload: str,
        slot_where: str,
        slot_values: tuple[object, ...],
        insert_sql: str,
        insert_values: tuple[object, ...],
    ) -> bool:
        row = self.connection.execute(
            f"SELECT {key_column}, payload_json FROM {table} "  # noqa: S608
            f"WHERE {slot_where}",
            slot_values,
        ).fetchone()
        if row is not None:
            if row[key_column] == key and row["payload_json"] == payload:
                return False
            raise StageConflictError(
                f"immutable stage slot in {table} already contains different data"
            )
        collision = self.connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column} = ?",  # noqa: S608
            (key,),
        ).fetchone()
        if collision is not None:
            if collision["payload_json"] == payload:
                return False
            raise StageConflictError(f"immutable identity collision in {table}")
        self.connection.execute(insert_sql, insert_values)
        return True

    def _require_payload(
        self,
        table: str,
        key_column: str,
        key: str,
        payload: str,
    ) -> None:
        row = self.connection.execute(
            f"SELECT payload_json FROM {table} WHERE {key_column} = ?",  # noqa: S608
            (key,),
        ).fetchone()
        if row is None or row["payload_json"] != payload:
            raise StageConflictError(f"required immutable dependency is absent in {table}")

    def _load_one(
        self,
        query: str,
        values: tuple[object, ...],
        factory: Callable[[dict[str, object]], T],
    ) -> T | None:
        row = self.connection.execute(query, values).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise StageConflictError("stored M1 payload is not an object")
        return factory(payload)

    def _m1_event(
        self,
        event_type: str,
        subject_key: str | None,
        payload: object,
    ) -> None:
        self.connection.execute(
            "INSERT INTO m1_event(event_type, subject_key, payload_json) "
            "VALUES (?, ?, ?)",
            (event_type, subject_key, canonical_json(payload)),
        )
