"""Fail-closed M1 translation-kernel errors."""


class M1ContractError(RuntimeError):
    """Base class for translation-kernel contract failures."""


class PlanBindingError(M1ContractError):
    """A stage belongs to another immutable kernel plan."""


class TerminologyError(M1ContractError):
    """A terminology revision, occurrence, or target application is invalid."""


class StageConflictError(M1ContractError):
    """An immutable logical stage slot already contains different data."""


class ReviewContractError(M1ContractError):
    """Review or adjudication evidence does not match the offered options."""


class VerificationContractError(M1ContractError):
    """A verification verdict contradicts its findings."""


class ReleaseContractError(M1ContractError):
    """A whole-document release is incomplete, mixed, or otherwise unsafe."""


class ServiceContractError(M1ContractError):
    """A model-facing service returned a response outside its typed contract."""

