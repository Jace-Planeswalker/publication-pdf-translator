"""Synchronous durable executor with bounded retries and no duplicate billing."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import TypeVar

from pubtrans.m1.errors import M1ContractError

from .errors import BudgetExceededError
from .errors import PermanentServiceError
from .errors import PreviouslyFailedCallError
from .errors import RateLimitServiceError
from .errors import RetryExhaustedError
from .errors import TransientServiceError
from .model import BudgetPolicy
from .model import CallDescriptor
from .model import CallEstimate
from .model import CallStatus
from .store import RecoveryStore


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RetryDecision:
    retryable: bool
    retry_after_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("retry policy requires at least one attempt")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays must be non-negative")
        if self.base_delay_seconds > self.max_delay_seconds:
            raise ValueError("base retry delay exceeds maximum delay")

    def classify(self, error: Exception) -> RetryDecision:
        if isinstance(error, RateLimitServiceError):
            return RetryDecision(True, error.retry_after_seconds)
        if isinstance(
            error,
            (TransientServiceError, TimeoutError, ConnectionError, OSError),
        ):
            return RetryDecision(True)
        if isinstance(error, (PermanentServiceError, M1ContractError)):
            return RetryDecision(False)
        return RetryDecision(False)

    def delay_for(self, ordinal: int, decision: RetryDecision) -> float:
        exponential = min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** max(0, ordinal - 1)),
        )
        if decision.retry_after_seconds is None:
            return exponential
        return min(
            self.max_delay_seconds,
            max(exponential, max(0.0, decision.retry_after_seconds)),
        )


class ResilientExecutor:
    """Cache successful responses and make each paid attempt auditable."""

    def __init__(
        self,
        store: RecoveryStore,
        *,
        owner_id: str,
        retry_policy: RetryPolicy | None = None,
        lease_ttl_seconds: float = 300.0,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        owner_id = owner_id.strip()
        if not owner_id:
            raise ValueError("executor owner_id must not be empty")
        if lease_ttl_seconds <= 0:
            raise ValueError("executor lease TTL must be positive")
        self.store = store
        self.owner_id = owner_id
        self.retry_policy = retry_policy or RetryPolicy()
        self.lease_ttl_seconds = lease_ttl_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper or time.sleep

    def execute(
        self,
        *,
        descriptor: CallDescriptor,
        budget: BudgetPolicy,
        estimate: CallEstimate,
        operation: Callable[[], T],
        encode: Callable[[T], object],
        decode: Callable[[object], T],
    ) -> T:
        self.store.register_budget(budget)
        self.store.register_call(descriptor)
        cached = self.store.cached_result(descriptor)
        if cached is not None:
            return decode(json.loads(cached))

        status = self.store.call_status(descriptor.call_key)
        if status in {CallStatus.FAILED_PERMANENT, CallStatus.EXHAUSTED}:
            raise PreviouslyFailedCallError(
                f"identical {descriptor.stage.value} call previously ended as "
                f"{status.value}"
            )

        while True:
            used_attempts = self.store.attempt_count(descriptor.call_key)
            if used_attempts >= self.retry_policy.max_attempts:
                error_payload = {
                    "type": "RetryExhausted",
                    "message": "attempt limit reached before a new invocation",
                }
                self.store.mark_exhausted(descriptor, error_payload)
                raise RetryExhaustedError(error_payload["message"])

            lease = self.store.acquire_lease(
                descriptor,
                owner_id=self.owner_id,
                now=self.clock(),
                ttl_seconds=self.lease_ttl_seconds,
            )
            try:
                attempt = self.store.begin_attempt(
                    descriptor=descriptor,
                    lease=lease,
                    policy=budget,
                    estimate=estimate,
                    now=self.clock(),
                )
            except BudgetExceededError:
                self.store.cancel_lease(lease)
                raise

            try:
                value = operation()
                result_payload = encode(value)
                self.store.complete_success(
                    lease=lease,
                    attempt=attempt,
                    result_payload=result_payload,
                    now=self.clock(),
                )
                return value
            except Exception as error:
                decision = self.retry_policy.classify(error)
                exhausted = (
                    decision.retryable
                    and attempt.ordinal >= self.retry_policy.max_attempts
                )
                self.store.complete_failure(
                    lease=lease,
                    attempt=attempt,
                    error_payload=_error_payload(error),
                    retryable=decision.retryable,
                    exhausted=exhausted,
                    now=self.clock(),
                )
                if not decision.retryable:
                    raise
                if exhausted:
                    raise RetryExhaustedError(
                        f"{descriptor.stage.value} failed after "
                        f"{attempt.ordinal} attempts"
                    ) from error
                self.sleeper(
                    self.retry_policy.delay_for(attempt.ordinal, decision)
                )


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def _error_payload(error: Exception) -> dict[str, str]:
    message = str(error)[:2000]
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub("[REDACTED]", message)
    return {
        "type": f"{type(error).__module__}.{type(error).__qualname__}",
        "message": message,
    }
