"""Exact validation for the approved translation map."""

from __future__ import annotations

from collections.abc import Iterable

from .errors import DuplicateUnitError
from .errors import StaleApprovalError
from .errors import UnitSetMismatchError
from .models import ApprovedTranslation
from .models import PreparedUnit
from .placeholders import assert_placeholders_conserved


def _unique_by_id(items: Iterable[object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        unit_id = getattr(item, "unit_id")
        if unit_id in result:
            raise DuplicateUnitError(f"duplicate unit id: {unit_id}")
        result[unit_id] = item
    return result


def validate_approved_translations(
    units: Iterable[PreparedUnit],
    approvals: Iterable[ApprovedTranslation],
) -> dict[str, ApprovedTranslation]:
    """Validate and index an all-or-nothing translation handoff."""
    unit_by_id = _unique_by_id(units)
    approval_by_id = _unique_by_id(approvals)

    expected_ids = set(unit_by_id)
    actual_ids = set(approval_by_id)
    if expected_ids != actual_ids:
        raise UnitSetMismatchError(
            "approved unit set mismatch; "
            f"missing={sorted(expected_ids - actual_ids)}, "
            f"extra={sorted(actual_ids - expected_ids)}"
        )

    for unit_id, raw_unit in unit_by_id.items():
        unit = raw_unit
        approval = approval_by_id[unit_id]
        if approval.source_sha256 != unit.source_sha256:
            raise StaleApprovalError(f"source hash mismatch for {unit_id}")
        if approval.placeholder_signature != unit.placeholder_signature:
            raise StaleApprovalError(f"placeholder signature mismatch for {unit_id}")
        if not approval.target_text.strip():
            raise StaleApprovalError(f"empty approved target for {unit_id}")
        assert_placeholders_conserved(
            unit.placeholder_tokens,
            approval.target_text,
            unit.placeholder_pairs,
        )

    return approval_by_id
