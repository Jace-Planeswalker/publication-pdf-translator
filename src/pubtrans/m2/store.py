"""Schema-v4 recovery state, leases, attempts, and immutable budgets."""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m1.store import KernelStore
from pubtrans.schema import M1_SCHEMA_VERSION
from pubtrans.schema import M2_SCHEMA_VERSION

from .errors import BudgetExceededError
from .errors import LeaseBusyError
from .errors import LeaseLostError
from .errors import RecoveryConflictError
from .model import AttemptOutcome
from .model import AttemptReceipt
from .model import BudgetPolicy
from .model import CallDescriptor
from .model import CallEstimate
from .model import CallStatus
from .model import LeaseGrant
from .model import attempt_id_for
from .model import utc_text


M2_SCHEMA = """
CREATE TABLE IF NOT EXISTS m2_budget (
    scope_key TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL UNIQUE,
    policy_json TEXT NOT NULL,
    attempted_calls INTEGER NOT NULL DEFAULT 0,
    estimated_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_microusd INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m2_call (
    call_key TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    dependency_digest TEXT NOT NULL,
    dependency_json TEXT NOT NULL,
    slot_hint TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS m2_lease (
    call_key TEXT PRIMARY KEY REFERENCES m2_call(call_key) ON DELETE RESTRICT,
    owner_id TEXT NOT NULL,
    lease_token TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS m2_attempt (
    attempt_id TEXT PRIMARY KEY,
    call_key TEXT NOT NULL REFERENCES m2_call(call_key) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    scope_key TEXT NOT NULL REFERENCES m2_budget(scope_key) ON DELETE RESTRICT,
    lease_token TEXT NOT NULL,
    outcome TEXT NOT NULL,
    estimate_json TEXT NOT NULL,
    error_json TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(call_key, ordinal)
);

CREATE TABLE IF NOT EXISTS m2_event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    subject_key TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class RecoveryStore(KernelStore):
    """Kernel store extended with durable remote-call control state."""

    def __init__(
        self,
        database_path: str | Path,
        artifact_store: PreparedArtifactStore,
    ) -> None:
        super().__init__(database_path, artifact_store)
        self._initialize_m2_schema()

    def _initialize_m2_schema(self) -> None:
        current = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current == M1_SCHEMA_VERSION:
            try:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + M2_SCHEMA
                    + f"\nPRAGMA user_version = {M2_SCHEMA_VERSION};\nCOMMIT;"
                )
            except BaseException:
                self.connection.rollback()
                raise
        elif current >= M2_SCHEMA_VERSION:
            self.connection.executescript(M2_SCHEMA)
        else:
            raise RecoveryConflictError(
                f"cannot initialize M2 on database schema {current}"
            )

    def register_budget(self, policy: BudgetPolicy) -> None:
        payload = canonical_json(policy.as_payload())
        with self._transaction():
            row = self.connection.execute(
                "SELECT policy_id, policy_json FROM m2_budget WHERE scope_key = ?",
                (policy.scope_key,),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO m2_budget(scope_key, policy_id, policy_json) "
                    "VALUES (?, ?, ?)",
                    (policy.scope_key, policy.policy_id, payload),
                )
                self._event_m2(
                    "budget_registered",
                    policy.scope_key,
                    policy.as_payload(),
                )
            elif row["policy_id"] != policy.policy_id or row["policy_json"] != payload:
                raise RecoveryConflictError(
                    "budget policy for this scope is immutable and already differs"
                )

    def register_call(self, descriptor: CallDescriptor) -> None:
        with self._transaction():
            row = self.connection.execute(
                "SELECT stage, dependency_digest, dependency_json, slot_hint "
                "FROM m2_call WHERE call_key = ?",
                (descriptor.call_key,),
            ).fetchone()
            incoming = (
                descriptor.stage.value,
                descriptor.dependency_digest,
                descriptor.dependency_json,
                descriptor.slot_hint,
            )
            if row is None:
                self.connection.execute(
                    "INSERT INTO m2_call("
                    "call_key, stage, dependency_digest, dependency_json, "
                    "slot_hint, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        descriptor.call_key,
                        *incoming,
                        CallStatus.PENDING.value,
                    ),
                )
            else:
                stored = (
                    row["stage"],
                    row["dependency_digest"],
                    row["dependency_json"],
                    row["slot_hint"],
                )
                if stored != incoming:
                    raise RecoveryConflictError("service call identity collision")

    def cached_result(self, descriptor: CallDescriptor) -> str | None:
        row = self.connection.execute(
            "SELECT status, result_json FROM m2_call WHERE call_key = ?",
            (descriptor.call_key,),
        ).fetchone()
        if row is None or row["status"] != CallStatus.SUCCEEDED.value:
            return None
        if row["result_json"] is None:
            raise RecoveryConflictError("successful call has no cached result")
        return str(row["result_json"])

    def call_status(self, call_key: str) -> CallStatus | None:
        row = self.connection.execute(
            "SELECT status FROM m2_call WHERE call_key = ?",
            (call_key,),
        ).fetchone()
        return CallStatus(str(row["status"])) if row is not None else None

    def attempt_count(self, call_key: str) -> int:
        row = self.connection.execute(
            "SELECT attempt_count FROM m2_call WHERE call_key = ?",
            (call_key,),
        ).fetchone()
        if row is None:
            raise RecoveryConflictError("service call is not registered")
        return int(row["attempt_count"])

    def acquire_lease(
        self,
        descriptor: CallDescriptor,
        *,
        owner_id: str,
        now: datetime,
        ttl_seconds: float,
    ) -> LeaseGrant:
        if ttl_seconds <= 0:
            raise ValueError("lease TTL must be positive")
        owner_id = owner_id.strip()
        if not owner_id:
            raise ValueError("lease owner must not be empty")
        now_text = utc_text(now)
        expires_text = utc_text(now + timedelta(seconds=ttl_seconds))
        with self._transaction():
            call = self.connection.execute(
                "SELECT status FROM m2_call WHERE call_key = ?",
                (descriptor.call_key,),
            ).fetchone()
            if call is None:
                raise RecoveryConflictError("cannot lease an unregistered call")
            if call["status"] == CallStatus.SUCCEEDED.value:
                raise RecoveryConflictError("successful cached call needs no lease")
            lease = self.connection.execute(
                "SELECT owner_id, lease_token, expires_at FROM m2_lease "
                "WHERE call_key = ?",
                (descriptor.call_key,),
            ).fetchone()
            if lease is not None and str(lease["expires_at"]) > now_text:
                raise LeaseBusyError(
                    f"call is leased by {lease['owner_id']} until {lease['expires_at']}"
                )
            if lease is not None:
                self.connection.execute(
                    "UPDATE m2_attempt SET outcome = ?, finished_at = ? "
                    "WHERE call_key = ? AND outcome = ?",
                    (
                        AttemptOutcome.ABANDONED.value,
                        now_text,
                        descriptor.call_key,
                        AttemptOutcome.RUNNING.value,
                    ),
                )
                self.connection.execute(
                    "UPDATE m2_call SET status = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE call_key = ? AND status = ?",
                    (
                        CallStatus.PENDING.value,
                        descriptor.call_key,
                        CallStatus.RUNNING.value,
                    ),
                )
                self.connection.execute(
                    "DELETE FROM m2_lease WHERE call_key = ?",
                    (descriptor.call_key,),
                )
                self._event_m2(
                    "lease_expired",
                    descriptor.call_key,
                    {"previous_owner": lease["owner_id"]},
                )
            token = secrets.token_hex(32)
            self.connection.execute(
                "INSERT INTO m2_lease("
                "call_key, owner_id, lease_token, expires_at, acquired_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    descriptor.call_key,
                    owner_id,
                    token,
                    expires_text,
                    now_text,
                ),
            )
            self._event_m2(
                "lease_acquired",
                descriptor.call_key,
                {"owner_id": owner_id, "expires_at": expires_text},
            )
        return LeaseGrant(
            call_key=descriptor.call_key,
            owner_id=owner_id,
            lease_token=token,
            expires_at=expires_text,
        )

    def begin_attempt(
        self,
        *,
        descriptor: CallDescriptor,
        lease: LeaseGrant,
        policy: BudgetPolicy,
        estimate: CallEstimate,
        now: datetime,
    ) -> AttemptReceipt:
        now_text = utc_text(now)
        with self._transaction():
            self._require_lease(lease, now_text)
            budget = self.connection.execute(
                "SELECT policy_id, attempted_calls, estimated_tokens, "
                "estimated_microusd FROM m2_budget WHERE scope_key = ?",
                (policy.scope_key,),
            ).fetchone()
            if budget is None or budget["policy_id"] != policy.policy_id:
                raise RecoveryConflictError("attempt budget is absent or differs")
            next_calls = int(budget["attempted_calls"]) + 1
            next_tokens = int(budget["estimated_tokens"]) + estimate.estimated_tokens
            next_cost = (
                int(budget["estimated_microusd"]) + estimate.estimated_microusd
            )
            if (
                next_calls > policy.max_attempted_calls
                or next_tokens > policy.max_estimated_tokens
                or next_cost > policy.max_estimated_microusd
            ):
                raise BudgetExceededError(
                    "remote attempt would exceed call, token, or cost budget"
                )
            row = self.connection.execute(
                "SELECT status, attempt_count FROM m2_call WHERE call_key = ?",
                (descriptor.call_key,),
            ).fetchone()
            if row is None or row["status"] in {
                CallStatus.SUCCEEDED.value,
                CallStatus.FAILED_PERMANENT.value,
                CallStatus.EXHAUSTED.value,
            }:
                raise RecoveryConflictError("call cannot begin another attempt")
            ordinal = int(row["attempt_count"]) + 1
            attempt_id = attempt_id_for(descriptor.call_key, ordinal)
            self.connection.execute(
                "UPDATE m2_budget SET attempted_calls = ?, estimated_tokens = ?, "
                "estimated_microusd = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE scope_key = ?",
                (next_calls, next_tokens, next_cost, policy.scope_key),
            )
            self.connection.execute(
                "UPDATE m2_call SET status = ?, attempt_count = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE call_key = ?",
                (CallStatus.RUNNING.value, ordinal, descriptor.call_key),
            )
            self.connection.execute(
                "INSERT INTO m2_attempt("
                "attempt_id, call_key, ordinal, scope_key, lease_token, outcome, "
                "estimate_json, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attempt_id,
                    descriptor.call_key,
                    ordinal,
                    policy.scope_key,
                    lease.lease_token,
                    AttemptOutcome.RUNNING.value,
                    canonical_json(estimate.as_payload()),
                    now_text,
                ),
            )
            self._event_m2(
                "attempt_started",
                attempt_id,
                {"call_key": descriptor.call_key, "ordinal": ordinal},
            )
        return AttemptReceipt(
            attempt_id=attempt_id,
            call_key=descriptor.call_key,
            ordinal=ordinal,
            lease_token=lease.lease_token,
        )

    def complete_success(
        self,
        *,
        lease: LeaseGrant,
        attempt: AttemptReceipt,
        result_payload: object,
        now: datetime,
    ) -> None:
        now_text = utc_text(now)
        result_json = canonical_json(result_payload)
        with self._transaction():
            self._require_lease(lease, now_text)
            self._require_attempt(attempt, lease)
            self.connection.execute(
                "UPDATE m2_attempt SET outcome = ?, finished_at = ? "
                "WHERE attempt_id = ?",
                (AttemptOutcome.SUCCEEDED.value, now_text, attempt.attempt_id),
            )
            self.connection.execute(
                "UPDATE m2_call SET status = ?, result_json = ?, "
                "last_error_json = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE call_key = ?",
                (CallStatus.SUCCEEDED.value, result_json, lease.call_key),
            )
            self.connection.execute(
                "DELETE FROM m2_lease WHERE call_key = ?",
                (lease.call_key,),
            )
            self._event_m2(
                "call_succeeded",
                lease.call_key,
                {"attempt_id": attempt.attempt_id},
            )

    def complete_failure(
        self,
        *,
        lease: LeaseGrant,
        attempt: AttemptReceipt,
        error_payload: object,
        retryable: bool,
        exhausted: bool,
        now: datetime,
    ) -> None:
        now_text = utc_text(now)
        error_json = canonical_json(error_payload)
        if retryable and not exhausted:
            status = CallStatus.PENDING
            outcome = AttemptOutcome.RETRYABLE_FAILURE
        elif retryable:
            status = CallStatus.EXHAUSTED
            outcome = AttemptOutcome.EXHAUSTED
        else:
            status = CallStatus.FAILED_PERMANENT
            outcome = AttemptOutcome.PERMANENT_FAILURE
        with self._transaction():
            self._require_lease(lease, now_text)
            self._require_attempt(attempt, lease)
            self.connection.execute(
                "UPDATE m2_attempt SET outcome = ?, error_json = ?, finished_at = ? "
                "WHERE attempt_id = ?",
                (outcome.value, error_json, now_text, attempt.attempt_id),
            )
            self.connection.execute(
                "UPDATE m2_call SET status = ?, last_error_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE call_key = ?",
                (status.value, error_json, lease.call_key),
            )
            self.connection.execute(
                "DELETE FROM m2_lease WHERE call_key = ?",
                (lease.call_key,),
            )
            self._event_m2(
                "call_attempt_failed",
                lease.call_key,
                {
                    "attempt_id": attempt.attempt_id,
                    "outcome": outcome.value,
                },
            )

    def cancel_lease(self, lease: LeaseGrant) -> None:
        with self._transaction():
            row = self.connection.execute(
                "SELECT lease_token FROM m2_lease WHERE call_key = ?",
                (lease.call_key,),
            ).fetchone()
            if row is not None and row["lease_token"] == lease.lease_token:
                self.connection.execute(
                    "DELETE FROM m2_lease WHERE call_key = ?",
                    (lease.call_key,),
                )

    def mark_exhausted(self, descriptor: CallDescriptor, error_payload: object) -> None:
        with self._transaction():
            row = self.connection.execute(
                "SELECT status FROM m2_call WHERE call_key = ?",
                (descriptor.call_key,),
            ).fetchone()
            if row is None:
                raise RecoveryConflictError("cannot exhaust an unknown call")
            if row["status"] == CallStatus.SUCCEEDED.value:
                return
            self.connection.execute(
                "UPDATE m2_call SET status = ?, last_error_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE call_key = ?",
                (
                    CallStatus.EXHAUSTED.value,
                    canonical_json(error_payload),
                    descriptor.call_key,
                ),
            )

    def budget_usage(self, scope_key: str) -> dict[str, int]:
        row = self.connection.execute(
            "SELECT attempted_calls, estimated_tokens, estimated_microusd "
            "FROM m2_budget WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()
        if row is None:
            raise RecoveryConflictError("budget is not registered")
        return {
            "attempted_calls": int(row["attempted_calls"]),
            "estimated_tokens": int(row["estimated_tokens"]),
            "estimated_microusd": int(row["estimated_microusd"]),
        }

    def attempts(self, call_key: str) -> tuple[dict[str, object], ...]:
        rows = self.connection.execute(
            "SELECT attempt_id, ordinal, outcome, error_json FROM m2_attempt "
            "WHERE call_key = ? ORDER BY ordinal",
            (call_key,),
        ).fetchall()
        return tuple(
            {
                "attempt_id": str(row["attempt_id"]),
                "ordinal": int(row["ordinal"]),
                "outcome": str(row["outcome"]),
                "error": (
                    json.loads(row["error_json"])
                    if row["error_json"] is not None
                    else None
                ),
            }
            for row in rows
        )

    def _require_lease(self, lease: LeaseGrant, now_text: str) -> None:
        row = self.connection.execute(
            "SELECT owner_id, lease_token, expires_at FROM m2_lease "
            "WHERE call_key = ?",
            (lease.call_key,),
        ).fetchone()
        if (
            row is None
            or row["owner_id"] != lease.owner_id
            or row["lease_token"] != lease.lease_token
            or str(row["expires_at"]) <= now_text
        ):
            raise LeaseLostError("call lease expired, changed owner, or disappeared")

    def _require_attempt(
        self,
        attempt: AttemptReceipt,
        lease: LeaseGrant,
    ) -> None:
        row = self.connection.execute(
            "SELECT call_key, lease_token, outcome FROM m2_attempt "
            "WHERE attempt_id = ?",
            (attempt.attempt_id,),
        ).fetchone()
        if (
            row is None
            or row["call_key"] != lease.call_key
            or row["lease_token"] != lease.lease_token
            or row["outcome"] != AttemptOutcome.RUNNING.value
        ):
            raise LeaseLostError("attempt no longer belongs to the live lease")

    def _event_m2(
        self,
        event_type: str,
        subject_key: str | None,
        payload: object,
    ) -> None:
        self.connection.execute(
            "INSERT INTO m2_event(event_type, subject_key, payload_json) "
            "VALUES (?, ?, ?)",
            (event_type, subject_key, canonical_json(payload)),
        )
