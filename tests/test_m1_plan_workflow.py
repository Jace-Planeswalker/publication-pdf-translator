from __future__ import annotations

import json
import unittest

from m0v2_helpers import make_document
from m0v2_helpers import target_for
from m1_helpers import actor
from m1_helpers import make_plan
from m1_helpers import make_terminology
from pubtrans.m1.context import build_context_packages
from pubtrans.m1.errors import PlanBindingError
from pubtrans.m1.errors import ReviewContractError
from pubtrans.m1.errors import VerificationContractError
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import ActorSpec
from pubtrans.m1.plan import LaneSpec
from pubtrans.m1.plan import RiskLevel
from pubtrans.m1.plan import UnitRoute
from pubtrans.m1.services import ReviewRequest
from pubtrans.m1.services import UnitStageInput
from pubtrans.m1.terminology import RenderedTarget
from pubtrans.m1.terminology import TermApplication
from pubtrans.m1.workflow import Adjudication
from pubtrans.m1.workflow import AdjudicationMode
from pubtrans.m1.workflow import AnonymousOption
from pubtrans.m1.workflow import Candidate
from pubtrans.m1.workflow import EditImpactVerdict
from pubtrans.m1.workflow import EditRevision
from pubtrans.m1.workflow import FindingCategory
from pubtrans.m1.workflow import FindingSeverity
from pubtrans.m1.workflow import ReviewFinding
from pubtrans.m1.workflow import ReviewReport
from pubtrans.m1.workflow import TextSpan
from pubtrans.m1.workflow import VerificationFinding
from pubtrans.m1.workflow import VerificationReport
from pubtrans.m1.workflow import VerificationVerdict


def rendered(document, terminology, unit_index: int, suffix: str = "") -> RenderedTarget:
    unit = document.units[unit_index]
    directive = terminology.directives_for_unit(unit.unit_key)[0]
    text = target_for(unit.placeholders, text=directive.required_rendering) + suffix
    start = text.index(directive.required_rendering)
    application = TermApplication.create(
        occurrence_key=directive.occurrence_key,
        target_text=text,
        target_start=start,
        target_end=start + len(directive.required_rendering),
    )
    return RenderedTarget.create(
        unit=unit,
        terminology=terminology,
        target_text=text,
        term_applications=(application,),
    )


class PlanAndContextTests(unittest.TestCase):
    def test_actor_creation_keeps_enum_and_rejects_secrets(self) -> None:
        spec = actor(ActorRole.TRANSLATOR, "safe")
        self.assertIs(spec.role, ActorRole.TRANSLATOR)
        with self.assertRaises(ValueError):
            ActorSpec.create(
                role=ActorRole.TRANSLATOR,
                provider="provider",
                model="model",
                prompt_revision="v1",
                settings={"api_key": "must-not-persist"},
            )

    def test_risk_routes_enforce_adaptive_candidate_counts(self) -> None:
        document = make_document(repeated=False)
        unit = document.units[0]
        lane = LaneSpec.create(
            label="one",
            actor=actor(ActorRole.TRANSLATOR, "one"),
        )
        with self.assertRaises(PlanBindingError):
            UnitRoute.create(
                unit_key=unit.unit_key,
                unit_revision=unit.unit_revision,
                risk_level=RiskLevel.R3,
                lanes=(lane,),
                reasons=("ambiguous source",),
            )
        with self.assertRaises(PlanBindingError):
            UnitRoute.create(
                unit_key=unit.unit_key,
                unit_revision=unit.unit_revision,
                risk_level=RiskLevel.R1,
                lanes=(lane, lane),
            )

    def test_context_is_source_only_and_includes_safe_exclusion(self) -> None:
        document = make_document()
        terminology = make_terminology(document)
        plan = make_plan(document, terminology)
        contexts = build_context_packages(
            document=document,
            terminology=terminology,
            plan=plan,
        )
        combined = json.dumps(
            [item.as_payload() for item in contexts],
            ensure_ascii=False,
        )
        self.assertIn("2026", combined)
        self.assertNotIn("你好", combined)
        self.assertNotIn("target_text", combined)


class BlindReviewAndWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = make_document()
        self.terminology = make_terminology(self.document)
        self.plan = make_plan(self.document, self.terminology)
        self.contexts = build_context_packages(
            document=self.document,
            terminology=self.terminology,
            plan=self.plan,
        )
        self.unit = self.document.units[0]
        self.lane = self.plan.lanes[0]
        self.candidate = Candidate.create(
            plan=self.plan,
            unit=self.unit,
            lane=self.lane,
            context=self.contexts[0],
            terminology=self.terminology,
            rendered_target=rendered(self.document, self.terminology, 0),
            translator_note="sensitive lane note",
        )
        self.option = AnonymousOption.from_candidate(self.candidate)

    def review(self, findings=()) -> ReviewReport:
        return ReviewReport.create(
            plan=self.plan,
            unit=self.unit,
            options=(self.option,),
            findings=findings,
            recommended_option_keys=(self.option.option_key,),
            summary="Blind review completed.",
        )

    def test_review_request_hides_candidate_provenance(self) -> None:
        stage = UnitStageInput.create(
            document=self.document,
            plan=self.plan,
            unit=self.unit,
            context=self.contexts[0],
            terminology=self.terminology,
        )
        payload = json.dumps(
            ReviewRequest(
                actor=self.plan.reviewer,
                stage=stage,
                options=(self.option,),
            ).as_payload(),
            ensure_ascii=False,
        )
        self.assertNotIn(self.candidate.candidate_id, payload)
        self.assertNotIn(self.lane.lane_key, payload)
        self.assertNotIn(self.lane.actor.provider, payload)
        self.assertNotIn(self.lane.actor.model, payload)
        self.assertNotIn("sensitive lane note", payload)

    def test_serious_review_finding_requires_exact_citation(self) -> None:
        with self.assertRaises(ReviewContractError):
            ReviewFinding.create(
                plan=self.plan,
                unit=self.unit,
                reviewer=self.plan.reviewer,
                option=self.option,
                category=FindingCategory.OMISSION,
                severity=FindingSeverity.MAJOR,
                message="A source proposition is absent.",
            )
        cited = ReviewFinding.create(
            plan=self.plan,
            unit=self.unit,
            reviewer=self.plan.reviewer,
            option=self.option,
            category=FindingCategory.ACCURACY,
            severity=FindingSeverity.MAJOR,
            message="The cited source word was mistranslated.",
            source_evidence=TextSpan.create(
                full_text=self.unit.source_text,
                start=self.unit.source_text.index("world"),
                end=self.unit.source_text.index("world") + 5,
            ),
        )
        self.assertEqual(cited.source_evidence.text, "world")

    def test_text_citation_is_position_bound_not_substring_only(self) -> None:
        citation = TextSpan.create(full_text="term term", start=5, end=9)
        citation.validate("term term")
        with self.assertRaises(ValueError):
            citation.validate("term xxxx")

    def test_declared_selection_cannot_hide_edits(self) -> None:
        review = self.review()
        altered = rendered(self.document, self.terminology, 0, suffix="附")
        with self.assertRaises(ReviewContractError):
            Adjudication.create(
                plan=self.plan,
                unit=self.unit,
                terminology=self.terminology,
                options=(self.option,),
                review=review,
                mode=AdjudicationMode.SELECT,
                selected_option_key=self.option.option_key,
                rendered_target=altered,
                resolutions=(),
                rationale="Invalid disguised edit.",
            )

    def test_false_verification_pass_is_rejected(self) -> None:
        review = self.review()
        adjudication = Adjudication.create(
            plan=self.plan,
            unit=self.unit,
            terminology=self.terminology,
            options=(self.option,),
            review=review,
            mode=AdjudicationMode.SELECT,
            selected_option_key=self.option.option_key,
            rendered_target=self.option.rendered_target,
            resolutions=(),
            rationale="Exact accurate selection.",
        )
        edit = EditRevision.create(
            plan=self.plan,
            unit=self.unit,
            terminology=self.terminology,
            adjudication=adjudication,
            rendered_target=adjudication.rendered_target,
            summary="No edit.",
        )
        finding = VerificationFinding.create(
            plan=self.plan,
            unit=self.unit,
            edit=edit,
            category=FindingCategory.MISTRANSLATION,
            severity=FindingSeverity.CRITICAL,
            message="Critical source meaning mismatch.",
            source_evidence=TextSpan.create(
                full_text=self.unit.source_text,
                start=0,
                end=5,
            ),
        )
        with self.assertRaises(VerificationContractError):
            VerificationReport.create(
                plan=self.plan,
                unit=self.unit,
                edit=edit,
                verdict=VerificationVerdict.PASS,
                edit_impact=EditImpactVerdict.EQUIVALENT,
                findings=(finding,),
                summary="Contradictory pass.",
            )


if __name__ == "__main__":
    unittest.main()
