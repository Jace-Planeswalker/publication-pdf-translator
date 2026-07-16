"""Fail-closed contract errors."""


class TranslationContractError(RuntimeError):
    """Base error for an invalid translation handoff."""


class DuplicateUnitError(TranslationContractError):
    """A stable unit identifier appeared more than once."""


class UnitSetMismatchError(TranslationContractError):
    """The approved result does not cover the exact requested unit set."""


class StaleApprovalError(TranslationContractError):
    """An approval was created for different source content or structure."""


class PlaceholderMismatchError(TranslationContractError):
    """Protected placeholder tokens were lost, added, or duplicated."""


class StateConflictError(TranslationContractError):
    """Persistent state conflicts with an immutable unit identity."""
