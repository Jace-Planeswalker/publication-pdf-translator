from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pubtrans.errors import DuplicateUnitError
from pubtrans.errors import PlaceholderMismatchError
from pubtrans.errors import StaleApprovalError
from pubtrans.errors import StateConflictError
from pubtrans.errors import UnitSetMismatchError
from pubtrans.models import ApprovedTranslation
from pubtrans.models import PreparedUnit
from pubtrans.provider import ApprovedMapProvider
from pubtrans.state import ProjectState
from pubtrans.validation import validate_approved_translations


DOCUMENT_HASH = "d" * 64


def make_unit(
    order: int,
    text: str,
    tokens: tuple[str, ...] = (),
    pairs: tuple[tuple[str, str], ...] = (),
) -> PreparedUnit:
    return PreparedUnit.create(
        document_sha256=DOCUMENT_HASH,
        page_number=1,
        paragraph_debug_id=f"p{order}",
        reading_order=order,
        source_text=text,
        placeholder_tokens=tokens,
        placeholder_pairs=pairs,
        layout_label="text",
    )


def approve(unit: PreparedUnit, target: str) -> ApprovedTranslation:
    return ApprovedTranslation(
        unit_id=unit.unit_id,
        source_sha256=unit.source_sha256,
        placeholder_signature=unit.placeholder_signature,
        target_text=target,
    )


class ApprovalContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.unit_a = make_unit(0, "Hello <f1>world", ("<f1>",))
        self.unit_b = make_unit(1, "Repeated source")

    def test_exact_valid_map_passes(self) -> None:
        result = validate_approved_translations(
            [self.unit_a, self.unit_b],
            [approve(self.unit_a, "你好 <f1>世界"), approve(self.unit_b, "重复原文")],
        )
        self.assertEqual(set(result), {self.unit_a.unit_id, self.unit_b.unit_id})

    def test_missing_unit_fails(self) -> None:
        with self.assertRaises(UnitSetMismatchError):
            validate_approved_translations(
                [self.unit_a, self.unit_b],
                [approve(self.unit_a, "你好 <f1>世界")],
            )

    def test_extra_unit_fails(self) -> None:
        extra = make_unit(2, "Extra")
        with self.assertRaises(UnitSetMismatchError):
            validate_approved_translations(
                [self.unit_a],
                [approve(self.unit_a, "你好 <f1>世界"), approve(extra, "额外")],
            )

    def test_duplicate_approval_fails(self) -> None:
        approval = approve(self.unit_a, "你好 <f1>世界")
        with self.assertRaises(DuplicateUnitError):
            validate_approved_translations([self.unit_a], [approval, approval])

    def test_stale_source_hash_fails(self) -> None:
        stale = ApprovedTranslation(
            unit_id=self.unit_a.unit_id,
            source_sha256="0" * 64,
            placeholder_signature=self.unit_a.placeholder_signature,
            target_text="你好 <f1>世界",
        )
        with self.assertRaises(StaleApprovalError):
            validate_approved_translations([self.unit_a], [stale])

    def test_missing_placeholder_fails(self) -> None:
        with self.assertRaises(PlaceholderMismatchError):
            validate_approved_translations(
                [self.unit_a],
                [approve(self.unit_a, "你好世界")],
            )

    def test_duplicated_placeholder_fails(self) -> None:
        with self.assertRaises(PlaceholderMismatchError):
            validate_approved_translations(
                [self.unit_a],
                [approve(self.unit_a, "<f1>你好 <f1>世界")],
            )

    def test_repeated_source_text_has_distinct_unit_ids(self) -> None:
        other = make_unit(9, self.unit_b.source_text)
        self.assertNotEqual(self.unit_b.unit_id, other.unit_id)

    def test_reversed_style_pair_fails(self) -> None:
        unit = make_unit(
            3,
            "Hello <b1>world</b1>",
            ("<b1>", "</b1>"),
            (("<b1>", "</b1>"),),
        )
        with self.assertRaises(PlaceholderMismatchError):
            validate_approved_translations(
                [unit],
                [approve(unit, "</b1>世界<b1>")],
            )


class StateTests(unittest.TestCase):
    def test_database_provider_round_trip(self) -> None:
        units = [make_unit(0, "Hello <f1>world", ("<f1>",)), make_unit(1, "Bye")]
        approvals = [approve(units[0], "你好 <f1>世界"), approve(units[1], "再见")]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            with ProjectState(database) as state:
                state.register_units(units)
                state.record_approvals(units, approvals)
                self.assertEqual(
                    state.status(),
                    {"units": 2, "approved": 2, "pending": 0},
                )

            resolved = ApprovedMapProvider(database).resolve(units)
            self.assertEqual(resolved, approvals)

    def test_provider_refuses_partial_database(self) -> None:
        units = [make_unit(0, "One"), make_unit(1, "Two")]
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            with ProjectState(database) as state:
                state.register_units(units)
                state.record_approvals([units[0]], [approve(units[0], "一")])
            with self.assertRaises(UnitSetMismatchError):
                ApprovedMapProvider(database).resolve(units)

    def test_registered_unit_snapshot_is_immutable(self) -> None:
        first = make_unit(0, "One")
        replacement = make_unit(0, "Changed")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            with ProjectState(database) as state:
                state.register_units([first])
                with self.assertRaisesRegex(StateConflictError, "unit set changed"):
                    state.register_units([replacement])

    def test_approved_text_cannot_be_silently_overwritten(self) -> None:
        unit = make_unit(0, "One")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "project.sqlite3"
            with ProjectState(database) as state:
                state.register_units([unit])
                state.record_approvals([unit], [approve(unit, "一")])
                with self.assertRaisesRegex(StateConflictError, "immutable"):
                    state.record_approvals([unit], [approve(unit, "一个")])


if __name__ == "__main__":
    unittest.main()
