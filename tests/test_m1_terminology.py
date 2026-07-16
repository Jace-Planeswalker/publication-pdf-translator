from __future__ import annotations

import unittest

from m0v2_helpers import make_document
from m0v2_helpers import make_snapshot
from m0v2_helpers import target_for
from m1_helpers import actor
from m1_helpers import evidence
from m1_helpers import make_terminology
from m1_helpers import term_candidate
from pubtrans.m1.errors import TerminologyError
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.terminology import Conventionality
from pubtrans.m1.terminology import DecisionConfidence
from pubtrans.m1.terminology import EvidenceTier
from pubtrans.m1.terminology import RenderedTarget
from pubtrans.m1.terminology import SemanticFit
from pubtrans.m1.terminology import TargetTermCandidate
from pubtrans.m1.terminology import TermApplication
from pubtrans.m1.terminology import TermDecision
from pubtrans.m1.terminology import TermOccurrence
from pubtrans.m1.terminology import TermRevision
from pubtrans.m1.terminology import TermTreatment
from pubtrans.m1.terminology import TerminologySnapshot


class EvidenceDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = make_document()
        self.researcher = actor(ActorRole.TERMINOLOGY_RESEARCHER, "r")
        self.reviewer = actor(ActorRole.BILINGUAL_REVIEWER, "v")

    def decision(
        self,
        candidates: tuple[TargetTermCandidate, ...],
        selected: TargetTermCandidate,
        *,
        confidence: DecisionConfidence = DecisionConfidence.VERIFIED,
        override: str = "",
    ) -> TermDecision:
        return TermDecision.create(
            project_key=self.document.project.project_key,
            source_term="world",
            sense_id="world.general.v1",
            concept_definition="The general inhabited world.",
            domain="general",
            candidates=candidates,
            selected_candidate_id=selected.candidate_id,
            confidence=confidence,
            rationale="Independent evidence review.",
            researcher_actor_key=self.researcher.actor_key,
            reviewer_actor_key=self.reviewer.actor_key,
            mainstream_override_reason=override,
        )

    def test_verified_requires_independent_corroboration(self) -> None:
        one_source = term_candidate(
            evidence_items=(
                evidence("世界", "only", tier=EvidenceTier.A_AUTHORITY),
            )
        )
        with self.assertRaises(TerminologyError):
            self.decision((one_source,), one_source)

    def test_less_conventional_selection_requires_accuracy_override(self) -> None:
        established = term_candidate("世界")
        rare = term_candidate("寰界", conventionality=Conventionality.RARE)
        with self.assertRaises(TerminologyError):
            self.decision((established, rare), rare)
        accepted = self.decision(
            (established, rare),
            rare,
            confidence=DecisionConfidence.SUPPORTED,
            override="The established form is a false friend in this defined sense.",
        )
        self.assertEqual(accepted.target_term, "寰界")

    def test_unresolved_term_is_retained_in_source(self) -> None:
        terminology = make_terminology(
            self.document,
            confidence=DecisionConfidence.RETAINED_UNRESOLVED,
        )
        term = terminology.terms[0]
        self.assertEqual(term.treatment, TermTreatment.RETAIN_SOURCE)
        self.assertEqual(term.target_term, "world")
        self.assertTrue(
            all(item.required_rendering == "world" for item in terminology.directives)
        )

    def test_confirmed_candidate_cannot_be_evidence_free(self) -> None:
        with self.assertRaises(TerminologyError):
            term_candidate(
                "臆造译名",
                semantic_fit=SemanticFit.CONFIRMED,
                evidence_items=(),
            )


class OccurrenceAndRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = make_document()

    def test_all_four_treatments_and_first_occurrence(self) -> None:
        expected = {
            TermTreatment.TRANSLATE_ONLY: ("世界", "世界"),
            TermTreatment.TRANSLATE_WITH_SOURCE_FIRST: (
                "世界（world）",
                "世界",
            ),
            TermTreatment.TRANSLATE_WITH_SOURCE_ALWAYS: (
                "世界（world）",
                "世界（world）",
            ),
            TermTreatment.RETAIN_SOURCE: ("world", "world"),
        }
        for treatment, renderings in expected.items():
            target = "world" if treatment is TermTreatment.RETAIN_SOURCE else "世界"
            terminology = make_terminology(
                self.document,
                treatment=treatment,
                target=target,
                confidence=(
                    DecisionConfidence.RETAINED_UNRESOLVED
                    if treatment is TermTreatment.RETAIN_SOURCE
                    else DecisionConfidence.VERIFIED
                ),
            )
            self.assertEqual(
                tuple(item.required_rendering for item in terminology.directives),
                renderings,
            )
            self.assertEqual(
                tuple(item.first_for_term for item in terminology.directives),
                (True, False),
            )

    def test_occurrence_from_another_snapshot_is_rejected(self) -> None:
        terminology = make_terminology(self.document)
        term = terminology.terms[0]
        other_snapshot = make_snapshot(
            self.document.project,
            profile={"pages": "1", "rich_text": True},
        )
        other = make_document(
            project=self.document.project,
            snapshot=other_snapshot,
            repeated=False,
        )
        start = other.units[0].source_text.index("world")
        foreign = TermOccurrence.create(
            term_key=term.term_key,
            unit=other.units[0],
            source_start=start,
            source_end=start + 5,
        )
        changed = TermRevision.create(
            decision=term.decision,
            treatment=term.treatment,
            rationale="Foreign stale occurrence.",
            occurrences=(foreign,),
        )
        with self.assertRaises(TerminologyError):
            TerminologySnapshot.create(self.document, (changed,))

    def test_overlapping_active_concepts_are_rejected(self) -> None:
        first = make_terminology(self.document).terms[0]
        candidate = term_candidate("世间")
        decision = TermDecision.create(
            project_key=self.document.project.project_key,
            source_term="world",
            sense_id="world.overlap-test.v1",
            concept_definition="An intentionally overlapping test sense.",
            domain="testing",
            candidates=(candidate,),
            selected_candidate_id=candidate.candidate_id,
            confidence=DecisionConfidence.VERIFIED,
            rationale="Overlap test.",
            researcher_actor_key=actor(
                ActorRole.TERMINOLOGY_RESEARCHER,
                "overlap-r",
            ).actor_key,
            reviewer_actor_key=actor(
                ActorRole.BILINGUAL_REVIEWER,
                "overlap-v",
            ).actor_key,
        )
        occurrences = []
        for unit in self.document.units:
            start = unit.source_text.index("world")
            occurrences.append(
                TermOccurrence.create(
                    term_key=decision.term_key,
                    unit=unit,
                    source_start=start,
                    source_end=start + 5,
                )
            )
        second = TermRevision.create(
            decision=decision,
            treatment=TermTreatment.TRANSLATE_ONLY,
            rationale="Overlap test.",
            occurrences=occurrences,
        )
        with self.assertRaises(TerminologyError):
            TerminologySnapshot.create(self.document, (first, second))

    def test_target_application_requires_exact_coverage_and_rendering(self) -> None:
        terminology = make_terminology(self.document)
        unit = self.document.units[0]
        text = target_for(unit.placeholders, text="世界")
        with self.assertRaises(TerminologyError):
            RenderedTarget.create(
                unit=unit,
                terminology=terminology,
                target_text=text,
                term_applications=(),
            )
        wrong_text = target_for(unit.placeholders, text="宇宙")
        start = wrong_text.index("宇宙")
        wrong = TermApplication.create(
            occurrence_key=terminology.directives[0].occurrence_key,
            target_text=wrong_text,
            target_start=start,
            target_end=start + 2,
        )
        with self.assertRaises(TerminologyError):
            RenderedTarget.create(
                unit=unit,
                terminology=terminology,
                target_text=wrong_text,
                term_applications=(wrong,),
            )


if __name__ == "__main__":
    unittest.main()
