"""Fail-closed M2 recovery and remote-call errors."""


class M2Error(RuntimeError):
    """Base class for recovery-controller failures."""


class RecoveryConflictError(M2Error):
    """Persisted recovery state differs from the immutable request."""


class LeaseBusyError(M2Error):
    """Another live worker owns the call lease."""


class LeaseLostError(M2Error):
    """A worker tried to commit after its lease expired or was replaced."""


class BudgetExceededError(M2Error):
    """The next remote attempt would exceed its immutable project budget."""


class RetryExhaustedError(M2Error):
    """A retryable call used every allowed attempt without succeeding."""


class PreviouslyFailedCallError(M2Error):
    """An identical call is already known to fail permanently."""


class TransientServiceError(RuntimeError):
    """A provider/network failure that may succeed when retried."""


class RateLimitServiceError(TransientServiceError):
    """A retryable provider throttle with an optional server delay."""

    def __init__(self, message: str, *, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PermanentServiceError(RuntimeError):
    """A request/auth/model error that retrying unchanged cannot repair."""
