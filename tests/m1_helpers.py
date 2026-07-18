from __future__ import annotations

from collections import Counter

from m0v2_helpers import target_for
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import ActorSpec
from pubtrans.m1.plan import ContextPolicy
from pubtrans.m1.plan import KernelPlan
from pubtrans.m1.plan import LaneSpec
from pubtrans.m1.plan import RiskLevel
from pubtrans.m1.plan import UnitRoute
from pubtrans.m1.services import AdjudicationDraft
from pubtrans.m1.services import AdjudicationRequest
from pubtrans.m1.services import ApplicationDraft
from pubtrans.m1.services import CandidateDraft
from pubtrans.m1.services import EditDraft
from pubtrans.m1.services import EditRequest
from pubtrans.m1.services import GenerationRequest
from pubtrans.m1.services import GlobalReviewDraft
from pubtrans.m1.services import GlobalReviewRequest
from pubtrans.m1.services import RenderedTargetDraft
from pubtrans.m1.services import ReviewDraft
from pubtrans.m1.services import ReviewRequest
from pubtrans.m1.services import ServiceBundle
from pubtrans.m1.services import VerificationDraft
from pubtrans.m1.services import VerificationRequest
from pubtrans.m1.terminology import Conventionality
from pubtrans.m1.terminology import DecisionConfidence
from pubtrans.m1.terminology import EvidenceKind
from pubtrans.m1.terminology import EvidenceStance
from pubtrans.m1.terminology import EvidenceTier
from pubtrans.m1.terminology import SemanticFit
from pubtrans.m1.terminology import RenderedTarget
from pubtrans.m1.terminology import TargetTermCandidate
from pubtrans.m1.terminology import TermDecision
from pubtrans.m1.terminology import TermEvidence
from pubtrans.m1.terminology import TermOccurrence
from pubtrans.m1.terminology import TermRevision
from pubtrans.m1.terminology import TermTreatment
from pubtrans.m1.terminology import TerminologySnapshot
from pubtrans.m1.workflow import AdjudicationMode
from pubtrans.m1.workflow import EditImpactVerdict
from pubtrans.m1.workflow import EditRevision
from pubtrans.m1.workflow import VerificationVerdict


def actor(role: ActorRole, name: str) -> ActorSpec:
    return ActorSpec.create(
        role=role,
        provider=f"provider-{name}",
        model=f"model-{name}",
        prompt_revision="test-v1",
        settings={"temperature": 0},
    )


def evidence(
    target: str,
    source: str,
    *,
    tier: EvidenceTier,
    stance: EvidenceStance = EvidenceStance.SUPPORTS,
) -> TermEvidence:
    return TermEvidence.create(
        target_form=target,
        stance=stance,
        kind=(
            EvidenceKind.AUTHORITY_TERMBANK
            if tier is EvidenceTier.A_AUTHORITY
            else EvidenceKind.CORPUS_ATTESTATION
        ),
        tier=tier,
        source_key=source,
        source_uri=f"https://example.test/{source}",
        source_title=f"Source {source}",
        excerpt=f"world 对应{target}",
        retrieved_on="2026-07-17",
        sense_match=True,
        domain_match=True,
    )


def term_candidate(
    target: str = "世界",
    *,
    conventionality: Conventionality = Conventionality.ESTABLISHED,
    semantic_fit: SemanticFit = SemanticFit.CONFIRMED,
    evidence_items: tuple[TermEvidence, ...] | None = None,
) -> TargetTermCandidate:
    if evidence_items is None and semantic_fit is SemanticFit.CONFIRMED:
        evidence_items = (
            evidence(target, "authority", tier=EvidenceTier.A_AUTHORITY),
            evidence(target, "corpus", tier=EvidenceTier.C_CORPUS),
        )
    return TargetTermCandidate.create(
        target_form=target,
        semantic_fit=semantic_fit,
        conventionality=conventionality,
        rationale="Matches the source concept in this domain.",
        evidence=evidence_items or (),
    )


def make_terminology(
    document: PreparedDocument,
    *,
    treatment: TermTreatment = TermTreatment.TRANSLATE_ONLY,
    confidence: DecisionConfidence = DecisionConfidence.VERIFIED,
    target: str = "世界",
) -> TerminologySnapshot:
    researcher = actor(ActorRole.TERMINOLOGY_RESEARCHER, "term-research")
    reviewer = actor(ActorRole.BILINGUAL_REVIEWER, "term-review")
    if confidence is DecisionConfidence.RETAINED_UNRESOLVED:
        candidate = term_candidate(
            "world",
            conventionality=Conventionality.UNATTESTED,
            semantic_fit=SemanticFit.UNRESOLVED,
            evidence_items=(),
        )
        target = "world"
        treatment = TermTreatment.RETAIN_SOURCE
    else:
        candidate = term_candidate(target)
    decision = TermDecision.create(
        project_key=document.project.project_key,
        source_term="world",
        sense_id="world.general.v1",
        concept_definition="The inhabited world or a general totality.",
        domain="general",
        candidates=(candidate,),
        selected_candidate_id=candidate.candidate_id,
        confidence=confidence,
        rationale="Evidence and sense fit were independently reviewed.",
        researcher_actor_key=researcher.actor_key,
        reviewer_actor_key=reviewer.actor_key,
    )
    occurrences = []
    for unit in document.units:
        start = unit.source_text.index("world")
        occurrences.append(
            TermOccurrence.create(
                term_key=decision.term_key,
                unit=unit,
                source_start=start,
                source_end=start + len("world"),
            )
        )
    revision = TermRevision.create(
        decision=decision,
        treatment=treatment,
        rationale="Publication rendering rule.",
        occurrences=occurrences,
    )
    return TerminologySnapshot.create(document, (revision,))


def make_plan(
    document: PreparedDocument,
    terminology: TerminologySnapshot,
) -> KernelPlan:
    lane_one = LaneSpec.create(
        label="baseline",
        actor=actor(ActorRole.TRANSLATOR, "translator-a"),
    )
    lane_two = LaneSpec.create(
        label="independent-high-risk",
        actor=actor(ActorRole.TRANSLATOR, "translator-b"),
    )
    routes = []
    for index, unit in enumerate(document.units):
        if index == 0:
            routes.append(
                UnitRoute.create(
                    unit_key=unit.unit_key,
                    unit_revision=unit.unit_revision,
                    risk_level=RiskLevel.R1,
                    lanes=(lane_one,),
                )
            )
        else:
            routes.append(
                UnitRoute.create(
                    unit_key=unit.unit_key,
                    unit_revision=unit.unit_revision,
                    risk_level=RiskLevel.R3,
                    lanes=(lane_one, lane_two),
                    reasons=("repeated high-impact terminology",),
                )
            )
    return KernelPlan.create(
        document=document,
        terminology=terminology,
        context_policy=ContextPolicy.create(),
        source_brief=None,
        lanes=(lane_one, lane_two),
        routes=routes,
        reviewer=actor(ActorRole.BILINGUAL_REVIEWER, "review"),
        adjudicator=actor(ActorRole.ADJUDICATOR, "adjudication"),
        editor=actor(ActorRole.CHINESE_EDITOR, "edit"),
        verifier=actor(ActorRole.FINAL_VERIFIER, "verify"),
        global_reviewer=actor(ActorRole.GLOBAL_REVIEWER, "global"),
    )


def target_draft_from_rendered(target: RenderedTarget) -> RenderedTargetDraft:
    return RenderedTargetDraft(
        target_text=target.target_text,
        term_applications=tuple(
            ApplicationDraft(
                occurrence_key=item.occurrence_key,
                target_start=item.target_start,
                target_end=item.target_end,
            )
            for item in target.term_applications
        ),
    )


class PassingServices:
    def __init__(self, document: PreparedDocument):
        self.document = document
        self.units = {item.unit_key: item for item in document.units}
        self.calls: Counter[str] = Counter()

    @property
    def bundle(self) -> ServiceBundle:
        return ServiceBundle(
            translation=self,
            bilingual_review=self,
            adjudication=self,
            chinese_edit=self,
            verification=self,
            global_review=self,
        )

    def generate(self, request: GenerationRequest) -> CandidateDraft:
        self.calls["generate"] += 1
        unit = self.units[request.stage.context.unit_key]
        if request.stage.terminology:
            guidance = request.stage.terminology[0]
            rendering = guidance.directive.required_rendering
            occurrence_key = guidance.directive.occurrence_key
        else:
            rendering = "世界"
            occurrence_key = ""
        text = target_for(unit.placeholders, text=rendering)
        applications = ()
        if occurrence_key:
            start = text.index(rendering)
            applications = (
                ApplicationDraft(
                    occurrence_key=occurrence_key,
                    target_start=start,
                    target_end=start + len(rendering),
                ),
            )
        return CandidateDraft(
            rendered_target=RenderedTargetDraft(text, applications),
            translator_note="baseline",
        )

    def review(self, request: ReviewRequest) -> ReviewDraft:
        self.calls["review"] += 1
        return ReviewDraft(
            findings=(),
            recommended_option_keys=(request.options[0].option_key,),
            summary="No material issue found in the blind comparison.",
        )

    def adjudicate(self, request: AdjudicationRequest) -> AdjudicationDraft:
        self.calls["adjudicate"] += 1
        option = request.options[0]
        return AdjudicationDraft(
            mode=AdjudicationMode.SELECT,
            selected_option_key=option.option_key,
            rendered_target=target_draft_from_rendered(option.rendered_target),
            resolutions=(),
            rationale="The first anonymous option is accurate and complete.",
        )

    def edit(self, request: EditRequest) -> EditDraft:
        self.calls["edit"] += 1
        raw = request.adjudication_payload["rendered_target"]
        assert isinstance(raw, dict)
        target = RenderedTarget.from_payload(raw)
        return EditDraft(
            rendered_target=target_draft_from_rendered(target),
            summary="No Chinese copy-edit was necessary.",
        )

    def verify(self, request: VerificationRequest) -> VerificationDraft:
        self.calls["verify"] += 1
        raw = request.edit_payload
        edit = EditRevision.from_payload(raw)
        return VerificationDraft(
            verdict=VerificationVerdict.PASS,
            edit_impact=(
                EditImpactVerdict.IMPROVES
                if edit.changed
                else EditImpactVerdict.EQUIVALENT
            ),
            findings=(),
            summary="Source, terminology, structure, and target all pass.",
        )

    def review_document(self, request: GlobalReviewRequest) -> GlobalReviewDraft:
        self.calls["global"] += 1
        return GlobalReviewDraft(
            verdict=VerificationVerdict.PASS,
            findings=(),
            summary="Whole-document terminology and discourse are consistent.",
        )
