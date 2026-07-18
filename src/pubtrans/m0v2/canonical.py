"""Canonical text, JSON, digest, and geometry helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal
from decimal import ROUND_HALF_EVEN

from .errors import IdentityError


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
POINT_QUANTUM = Decimal("0.001")


def normalize_text(value: str) -> str:
    """Return the contract's NFC/LF text representation."""
    if not isinstance(value, str):
        raise TypeError("contract text must be a string")
    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    )


def canonical_json(payload: object) -> str:
    """Serialize JSON deterministically and reject NaN/infinity."""
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest(namespace: str, payload: object) -> str:
    if not namespace or not namespace.strip():
        raise ValueError("digest namespace must not be empty")
    envelope = {"namespace": namespace, "payload": payload}
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise IdentityError(f"{name} must be a lowercase SHA-256 digest")


def quantize_point(value: int | float | str | Decimal) -> str:
    """Canonicalize one PDF point coordinate to 1/1000 point."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("PDF geometry must be finite")
    number = Decimal(str(value)).quantize(POINT_QUANTUM, rounding=ROUND_HALF_EVEN)
    if number == 0:
        number = Decimal(0)
    return format(number, ".3f")
