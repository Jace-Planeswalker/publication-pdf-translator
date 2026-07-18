"""Content-addressed, atomic storage for verified final PDF artifacts."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.canonical import sha256_bytes

from .errors import ArtifactStoreConflictError


@dataclass(frozen=True, slots=True)
class FinalPDFRef:
    sha256: str
    size: int
    relative_path: str

    def __post_init__(self) -> None:
        require_sha256("final PDF sha256", self.sha256)
        if self.size < 0:
            raise ValueError("final PDF size must be non-negative")
        expected = f"objects/{self.sha256[:2]}/{self.sha256}.pdf"
        if self.relative_path != expected:
            raise ValueError("final PDF path is not content-addressed")

    def as_payload(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "relative_path": self.relative_path,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "FinalPDFRef":
        return cls(
            sha256=str(payload["sha256"]),
            size=int(payload["size"]),
            relative_path=str(payload["relative_path"]),
        )


class FinalPDFStore:
    """Persist immutable PDFs using a durable same-directory rename."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def reference_for(payload: bytes) -> FinalPDFRef:
        if not isinstance(payload, bytes):
            raise TypeError("final PDF payload must be bytes")
        sha = sha256_bytes(payload)
        return FinalPDFRef(
            sha256=sha,
            size=len(payload),
            relative_path=f"objects/{sha[:2]}/{sha}.pdf",
        )

    def path_for(self, reference: FinalPDFRef) -> Path:
        return self.root / reference.relative_path

    def put(self, payload: bytes) -> FinalPDFRef:
        reference = self.reference_for(payload)
        destination = self.path_for(reference)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self.verify(reference)
            return reference

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{reference.sha256}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            self._fsync_directory(destination.parent)
        finally:
            temporary.unlink(missing_ok=True)
        self.verify(reference)
        return reference

    def get(self, reference: FinalPDFRef) -> bytes:
        try:
            payload = self.path_for(reference).read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactStoreConflictError("verified final PDF is missing") from exc
        if len(payload) != reference.size:
            raise ArtifactStoreConflictError("verified final PDF size differs")
        if sha256_bytes(payload) != reference.sha256:
            raise ArtifactStoreConflictError("verified final PDF digest differs")
        return payload

    def verify(self, reference: FinalPDFRef) -> None:
        self.get(reference)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
