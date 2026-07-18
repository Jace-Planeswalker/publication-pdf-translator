"""Deterministically recover term spans and evidence spans from model text."""

from __future__ import annotations

import re

from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m1.errors import ServiceContractError
from pubtrans.m1.services import ApplicationDraft
from pubtrans.m1.services import RenderedTargetDraft
from pubtrans.m1.services import SpanDraft
from pubtrans.m1.services import TermGuidance


_MARKER_RE = re.compile(r"⟪(/?)T:([0-9a-f]{64})⟫")


def marker_open(occurrence_key: str) -> str:
    return f"⟪T:{occurrence_key}⟫"


def marker_close(occurrence_key: str) -> str:
    return f"⟪/T:{occurrence_key}⟫"


def marker_contract(guidance: tuple[TermGuidance, ...]) -> list[dict[str, str]]:
    return [
        {
            "occurrence_key": item.directive.occurrence_key,
            "required_rendering": item.directive.required_rendering,
            "open_marker": marker_open(item.directive.occurrence_key),
            "close_marker": marker_close(item.directive.occurrence_key),
        }
        for item in guidance
    ]


def decode_marked_target(
    marked_text: str,
    guidance: tuple[TermGuidance, ...],
) -> RenderedTargetDraft:
    marked_text = normalize_text(marked_text)
    required = {
        item.directive.occurrence_key: item.directive.required_rendering
        for item in guidance
    }
    output: list[str] = []
    applications: list[ApplicationDraft] = []
    stack: tuple[str, int] | None = None
    seen: set[str] = set()
    cursor = 0
    output_length = 0
    for match in _MARKER_RE.finditer(marked_text):
        literal = marked_text[cursor : match.start()]
        output.append(literal)
        output_length += len(literal)
        closing = bool(match.group(1))
        key = match.group(2)
        if key not in required:
            raise ServiceContractError("model emitted an unknown terminology marker")
        if not closing:
            if stack is not None or key in seen:
                raise ServiceContractError("terminology markers are nested or duplicated")
            stack = (key, output_length)
        else:
            if stack is None or stack[0] != key:
                raise ServiceContractError("terminology marker pair is unbalanced")
            start = stack[1]
            rendered = "".join(output)[start:output_length]
            if rendered != required[key]:
                raise ServiceContractError(
                    "governed term differs from its required rendering"
                )
            applications.append(
                ApplicationDraft(
                    occurrence_key=key,
                    target_start=start,
                    target_end=output_length,
                )
            )
            seen.add(key)
            stack = None
        cursor = match.end()
    trailing = marked_text[cursor:]
    output.append(trailing)
    if stack is not None:
        raise ServiceContractError("terminology marker is not closed")
    if seen != set(required):
        raise ServiceContractError("model omitted one or more governed term markers")
    final_text = "".join(output)
    if _MARKER_RE.search(final_text) or "⟪T:" in final_text or "⟪/T:" in final_text:
        raise ServiceContractError("terminology marker leaked into target text")
    return RenderedTargetDraft(
        target_text=final_text,
        term_applications=tuple(
            sorted(applications, key=lambda item: item.occurrence_key)
        ),
    )


def quote_span(
    full_text: str,
    quote: str,
    declared_start: object,
) -> SpanDraft | None:
    full_text = normalize_text(full_text)
    quote = normalize_text(quote).strip()
    if not quote:
        return None
    try:
        start = int(declared_start)
    except (TypeError, ValueError):
        start = -1
    if 0 <= start <= len(full_text) - len(quote):
        if full_text[start : start + len(quote)] == quote:
            return SpanDraft(start=start, end=start + len(quote))
    positions = [
        match.start() for match in re.finditer(re.escape(quote), full_text)
    ]
    if len(positions) != 1:
        raise ServiceContractError(
            "model evidence quote is absent or ambiguous in the cited text"
        )
    return SpanDraft(start=positions[0], end=positions[0] + len(quote))
