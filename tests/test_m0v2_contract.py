from __future__ import annotations

import dataclasses
import unittest

from m0v2_helpers import make_contract
from m0v2_helpers import make_document
from m0v2_helpers import make_project
from m0v2_helpers import make_snapshot
from m0v2_helpers import source_for
from m0v2_helpers import target_for
from pubtrans.m0v2.errors import ApprovalSetError
from pubtrans.m0v2.errors import DocumentBlockedError
from pubtrans.m0v2.errors import IdentityError
from pubtrans.m0v2.errors import PlaceholderContractError
from pubtrans.m0v2.errors import StaleApprovalError
from pubtrans.m0v2.model import ApprovalRevision
from pubtrans.m0v2.model import BoxFingerprint
from pubtrans.m0v2.model import Disposition
from pubtrans.m0v2.model import ParagraphReason
from pubtrans.m0v2.model import ParagraphRecord
from pubtrans.m0v2.model import PlaceholderContract
from pubtrans.m0v2.model import PlaceholderKind
from pubtrans.m0v2.model import PlaceholderSpec
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedUnit
from pubtrans.m0v2.model import UnitLocator
from pubtrans.m0v2.provider import validate_approval_set


class IdentityTests(unittest.TestCase):
    def test_identity_does_not_accept_random_debug_id_input(self) -> None:
        first = make_document()
        second = make_document()
        self.assertEqual(first.snapshot.snapshot_key, second.snapshot.snapshot_key)
        self.assertEqual(
            [unit.unit_key for unit in first.units],
            [unit.unit_key for unit in second.units],
        )

    def test_repeated_source_has_distinct_locator_identity(self) -> None:
        document = make_document(repeated=True)
        self.assertEqual(document.units[0].source_text, document.units[1].source_text)
        self.assertNotEqual(document.units[0].unit_key, document.units[1].unit_key)

    def test_profile_change_changes_snapshot_and_unit_identity(self) -> None:
        project = make_project()
        first = make_document(project=project)
        changed_snapshot = make_snapshot(
            project,
            profile={"pages": "all", "rich_text": False},
        )
        changed = make_document(project=project, snapshot=changed_snapshot)
        self.assertNotEqual(first.snapshot.snapshot_key, changed.snapshot.snapshot_key)
        self.assertNotEqual(first.units[0].unit_key, changed.units[0].unit_key)

    def test_manifest_round_trip_preserves_identity(self) -> None:
        document = make_document()
        restored = PreparedDocument.from_payload(document.as_payload())
        self.assertEqual(restored, document)

    def test_tampered_manifest_digest_is_rejected(self) -> None:
        document = make_document()
        with self.assertRaises(IdentityError):
            dataclasses.replace(document, manifest_sha256="0" * 64)

    def test_manifest_cannot_omit_a_prepared_paragraph(self) -> None:
        document = make_document()
        with self.assertRaisesRegex(ValueError, "exactly classify"):
            PreparedDocument.create(
                project=document.project,
                snapshot=document.snapshot,
                page_paragraph_counts=(len(document.records) + 1,),
                records=document.records,
            )

    def test_missing_geometry_is_represented_without_a_fake_box(self) -> None:
        document = make_document(repeated=False)
        record = ParagraphRecord.create(
            snapshot_key=document.snapshot.snapshot_key,
            locator=UnitLocator(1, 0),
            disposition=Disposition.BLOCKER,
            reason=ParagraphReason.MISSING_GEOMETRY,
            source_text="Visible text",
            layout_label="text",
            vertical=False,
            box=None,
        )
        self.assertIsNone(record.box)

    def test_missing_box_cannot_be_disguised_as_safe_exclusion(self) -> None:
        document = make_document(repeated=False)
        with self.assertRaisesRegex(ValueError, "missing-geometry"):
            ParagraphRecord.create(
                snapshot_key=document.snapshot.snapshot_key,
                locator=UnitLocator(1, 0),
                disposition=Disposition.SAFE_EXCLUSION,
                reason=ParagraphReason.EMPTY,
                source_text="",
                layout_label=None,
                vertical=False,
                box=None,
            )


class PlaceholderTests(unittest.TestCase):
    def setUp(self) -> None:
        snapshot = make_snapshot(make_project())
        self.contract = make_contract(snapshot)
        self.valid = target_for(self.contract)

    def test_valid_target_passes(self) -> None:
        self.contract.validate(self.valid, require_nonempty_styles=True)

    def test_missing_token_fails(self) -> None:
        target = self.valid.replace(self.contract.tokens[-1], "")
        with self.assertRaises(PlaceholderContractError):
            self.contract.validate(target, require_nonempty_styles=True)

    def test_duplicate_token_fails(self) -> None:
        target = self.valid + self.contract.tokens[-1]
        with self.assertRaises(PlaceholderContractError):
            self.contract.validate(target, require_nonempty_styles=True)

    def test_invented_reserved_token_fails(self) -> None:
        invented = f"[[{self.contract.namespace}:F:9999]]"
        with self.assertRaises(PlaceholderContractError):
            self.contract.validate(self.valid + invented, require_nonempty_styles=True)

    def test_reordered_tokens_fail(self) -> None:
        open_token, close_token, formula = self.contract.tokens
        target = f"{formula}{open_token}世界{close_token}"
        with self.assertRaises(PlaceholderContractError):
            self.contract.validate(target, require_nonempty_styles=True)

    def test_empty_style_span_fails(self) -> None:
        style = self.contract.specs[0]
        target = self.valid.replace(
            f"{style.open_token}世界{style.close_token}",
            f"{style.open_token} {style.close_token}",
        )
        with self.assertRaises(PlaceholderContractError):
            self.contract.validate(target, require_nonempty_styles=True)

    def test_crossing_style_pairs_fail(self) -> None:
        namespace = self.contract.namespace
        first = PlaceholderSpec(
            PlaceholderKind.RICH_STYLE,
            f"[[{namespace}:S:0100:OPEN]]",
            f"[[{namespace}:S:0100:CLOSE]]",
        )
        second = PlaceholderSpec(
            PlaceholderKind.RICH_STYLE,
            f"[[{namespace}:S:0101:OPEN]]",
            f"[[{namespace}:S:0101:CLOSE]]",
        )
        contract = PlaceholderContract.create(namespace, (first, second))
        crossing = (
            f"{first.open_token}甲{second.open_token}乙"
            f"{first.close_token}丙{second.close_token}"
        )
        with self.assertRaises(PlaceholderContractError):
            contract.validate(crossing, require_nonempty_styles=True)

    def test_mismatched_style_pair_ids_are_rejected(self) -> None:
        namespace = self.contract.namespace
        with self.assertRaisesRegex(ValueError, "ids do not match"):
            PlaceholderContract.create(
                namespace,
                (
                    PlaceholderSpec(
                        PlaceholderKind.RICH_STYLE,
                        f"[[{namespace}:S:0100:OPEN]]",
                        f"[[{namespace}:S:0101:CLOSE]]",
                    ),
                ),
            )

    def test_source_with_undeclared_namespace_token_cannot_form_unit(self) -> None:
        document = make_document(repeated=False)
        unit = document.units[0]
        invented = f"[[{self.contract.namespace}:F:9999]]"
        with self.assertRaises(PlaceholderContractError):
            PreparedUnit.create(
                snapshot_key=unit.snapshot_key,
                locator=UnitLocator(9, 9),
                source_text=source_for(self.contract) + invented,
                placeholders=self.contract,
                layout_label="text",
                vertical=False,
                box=BoxFingerprint.create(0, 0, 1, 1),
            )


class ApprovalSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = make_document(repeated=True)
        self.approvals = tuple(
            ApprovalRevision.create(
                unit=unit,
                target_text=target_for(unit.placeholders, text=f"世界{index}"),
                origin="test-adjudicator",
            )
            for index, unit in enumerate(self.document.units)
        )

    def test_exact_set_passes_in_document_order(self) -> None:
        reversed_input = tuple(reversed(self.approvals))
        result = validate_approval_set(self.document, reversed_input)
        self.assertEqual(result, self.approvals)

    def test_missing_approval_fails(self) -> None:
        with self.assertRaises(ApprovalSetError):
            validate_approval_set(self.document, self.approvals[:-1])

    def test_duplicate_approval_fails(self) -> None:
        with self.assertRaises(ApprovalSetError):
            validate_approval_set(
                self.document,
                self.approvals + (self.approvals[0],),
            )

    def test_extra_approval_fails(self) -> None:
        other = make_document(
            project=make_project(pdf_sha="c" * 64),
            repeated=False,
        )
        extra = ApprovalRevision.create(
            unit=other.units[0],
            target_text=target_for(other.units[0].placeholders),
            origin="other",
        )
        with self.assertRaises(ApprovalSetError):
            validate_approval_set(self.document, self.approvals + (extra,))

    def test_stale_unit_revision_fails(self) -> None:
        current = self.document.units[0]
        stale_unit = PreparedUnit.create(
            snapshot_key=current.snapshot_key,
            locator=current.locator,
            source_text=source_for(current.placeholders, text="changed"),
            placeholders=current.placeholders,
            layout_label=current.layout_label,
            vertical=current.vertical,
            box=current.box,
        )
        stale = ApprovalRevision.create(
            unit=stale_unit,
            target_text=target_for(current.placeholders),
            origin="stale",
        )
        with self.assertRaises(StaleApprovalError):
            validate_approval_set(
                self.document,
                (stale,) + self.approvals[1:],
            )

    def test_blocker_prevents_resolution(self) -> None:
        blocked = make_document(blocker=True)
        approvals = [
            ApprovalRevision.create(
                unit=unit,
                target_text=target_for(unit.placeholders),
                origin="test",
            )
            for unit in blocked.units
        ]
        with self.assertRaises(DocumentBlockedError):
            validate_approval_set(blocked, approvals)


if __name__ == "__main__":
    unittest.main()
