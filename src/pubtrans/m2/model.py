"""Immutable M2 call, budget, lease, and attempt values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.errors import IdentityError


CALL_NAMESPACE = "pubtrans.service-call/v1"
BUDGET_NAMESPACE = "pubtrans.call-budget/v1"
ATTEMPT_NAMESPACE = "pubtrans.call-attempt/v1"


def _nonempty(name: str, value: str) -> str:
    result = normalize_text(value).strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("M2 timestamps must be timezone-aware")
    return value.isoformat(timespec="microseconds")


class CallStage(str, Enum):
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"
    DOCUMENT_SYNTHESIS = "DOCUMENT_SYNTHESIS"
    TERMINOLOGY_RESEARCH = "TERMINOLOGY_RESEARCH"
    EVIDENCE_HARVEST = "EVIDENCE_HARVEST"
    TERMINOLOGY_REVIEW = "TERMINOLOGY_REVIEW"
    GENERATION = "GENERATION"
    REVIEW = "REVIEW"
    ADJUDICATION = "ADJUDICATION"
    EDIT = "EDIT"
    VERIFICATION = "VERIFICATION"
    GLOBAL_REVIEW = "GLOBAL_REVIEW"
    GLOBAL_REVIEW_CHUNK = "GLOBAL_REVIEW_CHUNK"
    GLOBAL_REVIEW_SYNTHESIS = "GLOBAL_REVIEW_SYNTHESIS"


class CallStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    EXHAUSTED = "EXHAUSTED"


class AttemptOutcome(str, Enum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    EXHAUSTED = "EXHAUSTED"
    ABANDONED = "ABANDONED"


@dataclass(frozen=True, slots=True)
class CallEstimate:
    estimated_tokens: int
    estimated_microusd: int

    def __post_init__(self) -> None:
        if self.estimated_tokens < 0 or self.estimated_microusd < 0:
            raise ValueError("call estimates must be non-negative")

    def as_payload(self) -> dict[str, int]:
        return {
            "estimated_tokens": self.estimated_tokens,
            "estimated_microusd": self.estimated_microusd,
        }


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    policy_id: str
    scope_key: str
    max_attempted_calls: int
    max_estimated_tokens: int
    max_estimated_microusd: int

    @classmethod
    def create(
        cls,
        *,
        scope_key: str,
        max_attempted_calls: int,
        max_estimated_tokens: int,
        max_estimated_microusd: int,
    ) -> "BudgetPolicy":
        payload = {
            "scope_key": scope_key,
            "max_attempted_calls": int(max_attempted_calls),
            "max_estimated_tokens": int(max_estimated_tokens),
            "max_estimated_microusd": int(max_estimated_microusd),
        }
        return cls(policy_id=digest(BUDGET_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        require_sha256("policy_id", self.policy_id)
        require_sha256("scope_key", self.scope_key)
        if self.max_attempted_calls < 0:
            raise ValueError("call budget must be non-negative")
        if self.max_estimated_tokens < 0 or self.max_estimated_microusd < 0:
            raise ValueError("token and cost budgets must be non-negative")
        if self.policy_id != digest(BUDGET_NAMESPACE, self._identity_payload()):
            raise IdentityError("budget policy id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "scope_key": self.scope_key,
            "max_attempted_calls": self.max_attempted_calls,
            "max_estimated_tokens": self.max_estimated_tokens,
            "max_estimated_microusd": self.max_estimated_microusd,
        }

    def as_payload(self) -> dict[str, object]:
        return {"policy_id": self.policy_id, **self._identity_payload()}


@dataclass(frozen=True, slots=True)
class CallDescriptor:
    call_key: str
    stage: CallStage
    dependency_digest: str
    dependency_json: str
    slot_hint: str

    @classmethod
    def create(
        cls,
        *,
        stage: CallStage,
        dependency_payload: object,
        slot_hint: str,
    ) -> "CallDescriptor":
        dependency_json = canonical_json(dependency_payload)
        dependency_digest = digest("pubtrans.call-dependencies/v1", dependency_payload)
        slot_hint = _nonempty("call slot hint", slot_hint)
        identity = {
            "stage": stage.value,
            "dependency_digest": dependency_digest,
        }
        return cls(
            call_key=digest(CALL_NAMESPACE, identity),
            stage=stage,
            dependency_digest=dependency_digest,
            dependency_json=dependency_json,
            slot_hint=slot_hint,
        )

    def __post_init__(self) -> None:
        require_sha256("call_key", self.call_key)
        require_sha256("dependency_digest", self.dependency_digest)
        if self.slot_hint != _nonempty("call slot hint", self.slot_hint):
            raise ValueError("call slot hint is not canonical")
        if canonical_json_from_text(self.dependency_json) != self.dependency_json:
            raise ValueError("call dependency JSON is not canonical")
        if digest(
            "pubtrans.call-dependencies/v1",
            json_value(self.dependency_json),
        ) != self.dependency_digest:
            raise IdentityError("call dependency digest mismatch")
        if self.call_key != digest(CALL_NAMESPACE, self._identity_payload()):
            raise IdentityError("call key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "dependency_digest": self.dependency_digest,
        }

    def as_payload(self) -> dict[str, object]:
        return {
            "call_key": self.call_key,
            **self._identity_payload(),
            "dependency_json": self.dependency_json,
            "slot_hint": self.slot_hint,
        }


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    call_key: str
    owner_id: str
    lease_token: str
    expires_at: str

    def __post_init__(self) -> None:
        require_sha256("call_key", self.call_key)
        require_sha256("lease_token", self.lease_token)
        if self.owner_id != _nonempty("lease owner", self.owner_id):
            raise ValueError("lease owner is not canonical")
        datetime.fromisoformat(self.expires_at)


@dataclass(frozen=True, slots=True)
class AttemptReceipt:
    attempt_id: str
    call_key: str
    ordinal: int
    lease_token: str

    def __post_init__(self) -> None:
        for name in ("attempt_id", "call_key", "lease_token"):
            require_sha256(name, getattr(self, name))
        if self.ordinal < 1:
            raise ValueError("attempt ordinal must be positive")


def attempt_id_for(call_key: str, ordinal: int) -> str:
    return digest(ATTEMPT_NAMESPACE, {"call_key": call_key, "ordinal": ordinal})


def json_value(value: str) -> object:
    import json

    return json.loads(value)


def canonical_json_from_text(value: str) -> str:
    return canonical_json(json_value(value))
