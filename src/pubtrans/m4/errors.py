"""M4 artifact verification failures."""


class M4Error(RuntimeError):
    """Base class for rendered-artifact failures."""


class ArtifactVerificationError(M4Error):
    """The source, release, profile, or target artifact cannot be verified."""


class ArtifactStoreConflictError(M4Error):
    """Stored report/artifact state differs from immutable M4 data."""
