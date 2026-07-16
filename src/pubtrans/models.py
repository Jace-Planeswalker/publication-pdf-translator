"""Stable translation-unit and approval models."""

from __future__ import annotations

import hashlib
import string
from dataclasses import dataclass

from .placeholders import placeholder_signature


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(
        character not in string.hexdigits for character in value
    ):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256")


def make_unit_id(
    *,
    document_sha256: str,
    page_number: int,
    paragraph_debug_id: str,
    reading_order: int,
    source_sha256: str,
) -> str:
    """Build an identity that cannot collide on repeated source strings."""
    if page_number < 0 or reading_order < 0:
        raise ValueError("page_number and reading_order must be non-negative")
    validate_sha256("document_sha256", document_sha256)
    validate_sha256("source_sha256", source_sha256)
    if not paragraph_debug_id:
        raise ValueError("paragraph identity is required")
    return (
        f"{document_sha256}:p{page_number}:"
        f"{paragraph_debug_id}:r{reading_order}:{source_sha256}"
    )


@dataclass(frozen=True, slots=True)
class PreparedUnit:
    unit_id: str
    document_sha256: str
    page_number: int
    paragraph_debug_id: str
    reading_order: int
    source_text: str
    source_sha256: str
    placeholder_tokens: tuple[str, ...]
    placeholder_pairs: tuple[tuple[str, str], ...]
    placeholder_signature: str
    layout_label: str | None = None

    @classmethod
    def create(
        cls,
        *,
        document_sha256: str,
        page_number: int,
        paragraph_debug_id: str,
        reading_order: int,
        source_text: str,
        placeholder_tokens: tuple[str, ...] = (),
        placeholder_pairs: tuple[tuple[str, str], ...] = (),
        layout_label: str | None = None,
    ) -> "PreparedUnit":
        source_hash = sha256_text(source_text)
        return cls(
            unit_id=make_unit_id(
                document_sha256=document_sha256,
                page_number=page_number,
                paragraph_debug_id=paragraph_debug_id,
                reading_order=reading_order,
                source_sha256=source_hash,
            ),
            document_sha256=document_sha256,
            page_number=page_number,
            paragraph_debug_id=paragraph_debug_id,
            reading_order=reading_order,
            source_text=source_text,
            source_sha256=source_hash,
            placeholder_tokens=placeholder_tokens,
            placeholder_pairs=placeholder_pairs,
            placeholder_signature=placeholder_signature(
                placeholder_tokens,
                placeholder_pairs,
            ),
            layout_label=layout_label,
        )

    def __post_init__(self) -> None:
        expected_source_sha256 = sha256_text(self.source_text)
        if self.source_sha256 != expected_source_sha256:
            raise ValueError("source_sha256 does not match source_text")
        expected_signature = placeholder_signature(
            self.placeholder_tokens,
            self.placeholder_pairs,
        )
        if self.placeholder_signature != expected_signature:
            raise ValueError("placeholder_signature does not match its contract")
        expected_unit_id = make_unit_id(
            document_sha256=self.document_sha256,
            page_number=self.page_number,
            paragraph_debug_id=self.paragraph_debug_id,
            reading_order=self.reading_order,
            source_sha256=self.source_sha256,
        )
        if self.unit_id != expected_unit_id:
            raise ValueError("unit_id does not match immutable unit fields")


@dataclass(frozen=True, slots=True)
class ApprovedTranslation:
    unit_id: str
    source_sha256: str
    placeholder_signature: str
    target_text: str
