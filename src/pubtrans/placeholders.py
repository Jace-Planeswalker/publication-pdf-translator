"""Protected-token signatures and conservation checks."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable

from .errors import PlaceholderMismatchError


def token_multiset(tokens: Iterable[str]) -> Counter[str]:
    """Return a normalized multiset and reject empty protected tokens."""
    result: Counter[str] = Counter()
    for token in tokens:
        if not token:
            raise ValueError("protected placeholder tokens must not be empty")
        result[token] += 1
    return result


def placeholder_signature(
    tokens: Iterable[str],
    pairs: Iterable[tuple[str, str]] = (),
) -> str:
    """Hash the token multiset and rich-text pairing contract."""
    counts = token_multiset(tokens)
    normalized_pairs = tuple(pairs)
    for left, right in normalized_pairs:
        if not left or not right or left == right:
            raise ValueError("placeholder pairs require distinct non-empty tokens")
        if counts[left] != 1 or counts[right] != 1:
            raise ValueError("paired placeholder tokens must each occur exactly once")
    payload = json.dumps(
        {
            "counts": sorted(counts.items()),
            "pairs": sorted(normalized_pairs),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def assert_placeholders_conserved(
    tokens: Iterable[str],
    target_text: str,
    pairs: Iterable[tuple[str, str]] = (),
) -> None:
    """Require exact tokens and valid, non-empty rich-text spans."""
    expected = token_multiset(tokens)
    if expected:
        alternatives = sorted(expected, key=lambda token: (-len(token), token))
        pattern = re.compile("|".join(re.escape(token) for token in alternatives))
        actual = Counter(match.group(0) for match in pattern.finditer(target_text))
    else:
        actual = Counter()
    missing_or_duplicated = {
        token: {"expected": count, "actual": actual[token]}
        for token, count in expected.items()
        if actual[token] != count
    }
    if missing_or_duplicated:
        raise PlaceholderMismatchError(
            f"placeholder multiset mismatch: {missing_or_duplicated}"
        )

    intervals: list[tuple[int, int]] = []
    for left, right in pairs:
        left_start = target_text.find(left)
        right_start = target_text.find(right)
        if left_start < 0 or right_start < left_start + len(left):
            raise PlaceholderMismatchError(
                f"invalid placeholder pair order: {left!r}, {right!r}"
            )
        if not target_text[left_start + len(left) : right_start].strip():
            raise PlaceholderMismatchError(
                f"empty protected style span: {left!r}, {right!r}"
            )
        intervals.append((left_start, right_start + len(right)))

    intervals.sort()
    for previous, current in zip(intervals, intervals[1:], strict=False):
        if current[0] < previous[1]:
            raise PlaceholderMismatchError(
                "rich-text placeholder spans overlap or cross"
            )
