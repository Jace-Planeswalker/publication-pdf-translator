"""Deterministic source-only context construction for M1."""

from __future__ import annotations

from pubtrans.m0v2.model import PreparedDocument

from .errors import PlanBindingError
from .plan import ContextFragment
from .plan import ContextPackage
from .plan import KernelPlan
from .terminology import TerminologySnapshot


def build_context_packages(
    *,
    document: PreparedDocument,
    terminology: TerminologySnapshot,
    plan: KernelPlan,
) -> tuple[ContextPackage, ...]:
    """Build one immutable package per unit without target-language leakage."""
    plan.validate_against(document=document, terminology=terminology)
    records = document.records
    record_index_by_unit = {
        record.unit.unit_key: index
        for index, record in enumerate(records)
        if record.unit is not None
    }
    packages: list[ContextPackage] = []

    for unit in document.units:
        current_index = record_index_by_unit.get(unit.unit_key)
        if current_index is None:
            raise PlanBindingError("kernel unit has no prepared paragraph record")
        current_record = records[current_index]
        if current_record.unit != unit:
            raise PlanBindingError("prepared unit and paragraph record differ")

        before_start = max(0, current_index - plan.context_policy.before_records)
        after_end = min(
            len(records),
            current_index + 1 + plan.context_policy.after_records,
        )
        nearby: list[tuple[int, int, int]] = []
        for index in range(before_start, current_index):
            nearby.append((current_index - index, 0, index))
        for index in range(current_index + 1, after_end):
            nearby.append((index - current_index, 1, index))
        nearby.sort()

        selected: set[int] = set()
        used_characters = 0
        for _distance, _side, index in nearby:
            length = len(records[index].source_text)
            if used_characters + length > plan.context_policy.max_neighbor_characters:
                continue
            selected.add(index)
            used_characters += length

        before = tuple(
            ContextFragment.from_record(records[index])
            for index in sorted(selected)
            if index < current_index
        )
        after = tuple(
            ContextFragment.from_record(records[index])
            for index in sorted(selected)
            if index > current_index
        )
        directive_ids = tuple(
            directive.directive_id
            for directive in terminology.directives_for_unit(unit.unit_key)
        )
        packages.append(
            ContextPackage.create(
                plan_key=plan.plan_key,
                unit_key=unit.unit_key,
                unit_revision=unit.unit_revision,
                current=ContextFragment.from_record(current_record),
                before=before,
                after=after,
                relevant_directive_ids=directive_ids,
            )
        )

    if tuple(package.unit_key for package in packages) != tuple(
        unit.unit_key for unit in document.units
    ):
        raise PlanBindingError("context packages do not cover the document unit set")
    return tuple(packages)

