"""Content-addressed, atomic prepared-IL artifact storage."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .canonical import require_sha256
from .canonical import sha256_bytes
from .errors import ArtifactIntegrityError


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    size: int
    relative_path: str

    def __post_init__(self) -> None:
        require_sha256("artifact sha256", self.sha256)
        if self.size < 0:
            raise ValueError("artifact size must be non-negative")
        expected = f"objects/{self.sha256[:2]}/{self.sha256}.prepared.xml"
        if self.relative_path != expected:
            raise ValueError("artifact path is not the content-addressed path")

    def as_payload(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "relative_path": self.relative_path,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ArtifactRef":
        return cls(
            sha256=str(payload["sha256"]),
            size=int(payload["size"]),
            relative_path=str(payload["relative_path"]),
        )


class PreparedArtifactStore:
    """Store immutable prepared IL only after a durable same-directory rename."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def reference_for(payload: bytes) -> ArtifactRef:
        sha = sha256_bytes(payload)
        return ArtifactRef(
            sha256=sha,
            size=len(payload),
            relative_path=f"objects/{sha[:2]}/{sha}.prepared.xml",
        )

    def path_for(self, reference: ArtifactRef) -> Path:
        return self.root / reference.relative_path

    def put(self, payload: bytes) -> ArtifactRef:
        if not isinstance(payload, bytes):
            raise TypeError("prepared artifact payload must be bytes")
        reference = self.reference_for(payload)
        destination = self.path_for(reference)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            self.verify(reference)
            return reference

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{reference.sha256}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, destination)
            self._fsync_directory(destination.parent)
        finally:
            temporary_path.unlink(missing_ok=True)

        self.verify(reference)
        return reference

    def get(self, reference: ArtifactRef) -> bytes:
        path = self.path_for(reference)
        try:
            payload = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError(
                f"prepared artifact is missing: {reference.relative_path}"
            ) from exc
        if len(payload) != reference.size:
            raise ArtifactIntegrityError(
                f"prepared artifact size mismatch: {reference.relative_path}"
            )
        if sha256_bytes(payload) != reference.sha256:
            raise ArtifactIntegrityError(
                f"prepared artifact digest mismatch: {reference.relative_path}"
            )
        return payload

    def verify(self, reference: ArtifactRef) -> None:
        self.get(reference)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
