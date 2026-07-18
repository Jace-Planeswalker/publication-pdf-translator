"""Fail-closed M0 v2 error taxonomy."""


class M0ContractError(RuntimeError):
    """Base class for a prepared-document contract violation."""


class IdentityError(M0ContractError):
    """A digest or derived identity does not match its immutable fields."""


class ProjectBindingError(M0ContractError):
    """A database is being opened for a different source/profile project."""


class SnapshotConflictError(M0ContractError):
    """A prepared snapshot conflicts with the registered immutable snapshot."""


class LegacyDatabaseError(M0ContractError):
    """A v1/unversioned database cannot be silently treated as M0 v2."""


class UnsupportedSchemaError(M0ContractError):
    """The database schema is newer than this runtime understands."""


class ArtifactIntegrityError(M0ContractError):
    """A prepared artifact is missing, truncated, or content-mismatched."""


class DocumentBlockedError(M0ContractError):
    """The prepared document contains unsupported meaningful content."""


class ApprovalSetError(M0ContractError):
    """Active approvals do not exactly cover the current document."""


class StaleApprovalError(ApprovalSetError):
    """An approval points to a different unit revision."""


class PlaceholderContractError(ApprovalSetError):
    """Protected formula/style structure is not conserved exactly."""


class StateConflictError(M0ContractError):
    """An immutable persistent record was presented with different content."""
