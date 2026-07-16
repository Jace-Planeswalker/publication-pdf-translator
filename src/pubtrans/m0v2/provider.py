"""Whole-document approval validation independent of any model or database."""

from __future__ import annotations

from collections.abc import Iterable

from .errors import ApprovalSetError
from .errors import StaleApprovalError
from .model import ApprovalRevision
from .model import PreparedDocument


def validate_approval_set(
    document: PreparedDocument,
    approvals: Iterable[ApprovalRevision],
) -> tuple[ApprovalRevision, ...]:
    """Return approvals in unit order after an all-or-nothing validation."""
    document.require_unblocked()
    approvals = tuple(approvals)

    approval_by_id: dict[str, ApprovalRevision] = {}
    approval_by_unit: dict[str, ApprovalRevision] = {}
    for approval in approvals:
        if approval.approval_id in approval_by_id:
            raise ApprovalSetError(
                f"duplicate approval id: {approval.approval_id}"
            )
        if approval.unit_key in approval_by_unit:
            raise ApprovalSetError(
                f"multiple active approvals for unit: {approval.unit_key}"
            )
        approval_by_id[approval.approval_id] = approval
        approval_by_unit[approval.unit_key] = approval

    unit_by_key = {unit.unit_key: unit for unit in document.units}
    expected = set(unit_by_key)
    actual = set(approval_by_unit)
    if expected != actual:
        raise ApprovalSetError(
            "active approval coverage mismatch; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )

    for unit_key, unit in unit_by_key.items():
        approval = approval_by_unit[unit_key]
        if approval.unit_revision != unit.unit_revision:
            raise StaleApprovalError(
                f"approval revision is stale for unit: {unit_key}"
            )
        unit.placeholders.validate(
            approval.target_text,
            require_nonempty_styles=True,
        )

    return tuple(approval_by_unit[unit.unit_key] for unit in document.units)
