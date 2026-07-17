"""M5 provider, planning, and product errors."""


class M5Error(RuntimeError):
    """Base class for product-layer failures."""


class ProductConfigError(M5Error):
    """The product configuration is invalid or incomplete."""


class ModelResponseError(M5Error):
    """A provider response is malformed, refused, or contract-incompatible."""


class TerminologyPlanningError(M5Error):
    """Terminology research cannot produce a safe deterministic plan."""
