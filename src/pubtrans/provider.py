"""Approved-map provider backed by the project state database."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .models import ApprovedTranslation
from .models import PreparedUnit
from .state import ProjectState
from .validation import validate_approved_translations


class ApprovedMapProvider:
    """Return only previously approved, current, structurally valid targets."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def resolve(
        self,
        units: Iterable[PreparedUnit],
    ) -> list[ApprovedTranslation]:
        units = list(units)
        with ProjectState(self.database_path) as state:
            approvals = state.load_approvals()
        approval_by_id = validate_approved_translations(units, approvals)
        return [approval_by_id[unit.unit_id] for unit in units]
