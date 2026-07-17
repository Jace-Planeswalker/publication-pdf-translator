"""M3 end-to-end PDF-loop failures."""


class M3Error(RuntimeError):
    """Base class for application-loop failures."""


class PDFLoopContractError(M3Error):
    """Preparation, kernel release, and BabelDOC render state disagree."""
