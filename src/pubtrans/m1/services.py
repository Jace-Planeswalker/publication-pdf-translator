"""Typed, provenance-safe service boundary for the M1 quality bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedUnit

from .errors import ServiceContractError
from .plan import ActorSpec
from .plan import ContextPackage
from .plan import KernelPlan
from .plan import LaneSpec
from .terminology import TermDirective
from .terminology import TermRevision
from .terminology import TerminologySnapshot
from .workflow import AdjudicationMode
from .workflow import AnonymousOption
from .workflow import EditImpactVerdict
from .workflow import FindingCategory
from .workflow import FindingSeverity
from .workflow import ResolutionAction
from .workflow import VerificationVerdict


@dataclass(frozen=True, slots=True)
class TermGuidance:
    """One approved concept decision injected into every semantic stage."""

    directive: TermDirective
    term: TermRevision

    def __post_init__(self) -> None:
        if self.directive.term_revision_id != self.term.revision_id:
            raise ServiceContractError("term guidance combines unrelated revisions")

    def as_payload(self) -> dict[str, object]:
        return {
            "directive": self.directive.as_payload(),
            "term": self.term.as_payload(),
        }


@dataclass(frozen=True, slots=True)
class UnitStageInput:
    """Source-only document context plus the evidence-backed term dossier."""

    plan_key: str
    source_language: str
    target_language: str
    source_brief: str | None
    context: ContextPackage
    terminology: tuple[TermGuidance, ...]

    @classmethod
    def create(
        cls,
        *,
        document: PreparedDocument,
        plan: KernelPlan,
        unit: PreparedUnit,
        context: ContextPackage,
        terminology: TerminologySnapshot,
    ) -> "UnitStageInput":
        if context.plan_key != plan.plan_key:
            raise ServiceContractError("stage context belongs to another plan")
        if context.unit_key != unit.unit_key or context.unit_revision != unit.unit_revision:
            raise ServiceContractError("stage context belongs to another unit")
        term_by_revision = {item.revision_id: item for item in terminology.terms}
        directives = terminology.directives_for_unit(unit.unit_key)
        if tuple(sorted(item.directive_id for item in directives)) != tuple(
            sorted(context.relevant_directive_ids)
        ):
            raise ServiceContractError("stage terminology differs from its context")
        guidance: list[TermGuidance] = []
        for directive in directives:
            term = term_by_revision.get(directive.term_revision_id)
            if term is None:
                raise ServiceContractError("directive references an unknown term revision")
            guidance.append(TermGuidance(directive=directive, term=term))
        guidance.sort(key=lambda item: item.directive.directive_id)
        return cls(
            plan_key=plan.plan_key,
            source_language=document.project.source_language,
            target_language=document.project.target_language,
            source_brief=(
                plan.source_brief.brief_text if plan.source_brief is not None else None
            ),
            context=context,
            terminology=tuple(guidance),
        )

    def as_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "source_brief": self.source_brief,
            "context": self.context.as_payload(),
            "terminology": [item.as_payload() for item in self.terminology],
        }


@dataclass(frozen=True, slots=True)
class ApplicationDraft:
    occurrence_key: str
    target_start: int
    target_end: int


@dataclass(frozen=True, slots=True)
class RenderedTargetDraft:
    target_text: str
    term_applications: tuple[ApplicationDraft, ...] = ()


@dataclass(frozen=True, slots=True)
class SpanDraft:
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class FindingDraft:
    category: FindingCategory
    severity: FindingSeverity
    message: str
    option_key: str | None = None
    source_evidence: SpanDraft | None = None
    target_evidence: SpanDraft | None = None


@dataclass(frozen=True, slots=True)
class ResolutionDraft:
    finding_id: str
    action: ResolutionAction
    explanation: str


@dataclass(frozen=True, slots=True)
class CandidateDraft:
    rendered_target: RenderedTargetDraft
    translator_note: str = ""


@dataclass(frozen=True, slots=True)
class ReviewDraft:
    findings: tuple[FindingDraft, ...]
    recommended_option_keys: tuple[str, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class AdjudicationDraft:
    mode: AdjudicationMode
    selected_option_key: str | None
    rendered_target: RenderedTargetDraft
    resolutions: tuple[ResolutionDraft, ...]
    rationale: str


@dataclass(frozen=True, slots=True)
class EditDraft:
    rendered_target: RenderedTargetDraft
    summary: str


@dataclass(frozen=True, slots=True)
class VerificationDraft:
    verdict: VerificationVerdict
    edit_impact: EditImpactVerdict
    findings: tuple[FindingDraft, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class GlobalFindingDraft:
    category: FindingCategory
    severity: FindingSeverity
    unit_keys: tuple[str, ...]
    message: str


@dataclass(frozen=True, slots=True)
class GlobalReviewDraft:
    verdict: VerificationVerdict
    findings: tuple[GlobalFindingDraft, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    actor: ActorSpec
    lane: LaneSpec
    stage: UnitStageInput

    def as_payload(self) -> dict[str, object]:
        return {
            "actor": self.actor.as_payload(),
            "lane_key": self.lane.lane_key,
            "stage": self.stage.as_payload(),
        }


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    actor: ActorSpec
    stage: UnitStageInput
    options: tuple[AnonymousOption, ...]

    def __post_init__(self) -> None:
        if not self.options:
            raise ServiceContractError("blind review requires at least one option")
        keys = [item.option_key for item in self.options]
        if len(keys) != len(set(keys)):
            raise ServiceContractError("blind review options are duplicated")

    def as_payload(self) -> dict[str, object]:
        """Intentionally excludes candidate, lane, provider, and model provenance."""
        return {
            "actor": self.actor.as_payload(),
            "stage": self.stage.as_payload(),
            "options": [item.as_payload() for item in self.options],
        }


@dataclass(frozen=True, slots=True)
class AdjudicationRequest:
    actor: ActorSpec
    stage: UnitStageInput
    options: tuple[AnonymousOption, ...]
    review_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class EditRequest:
    actor: ActorSpec
    stage: UnitStageInput
    adjudication_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    actor: ActorSpec
    stage: UnitStageInput
    adjudication_payload: dict[str, object]
    edit_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class GlobalReviewRequest:
    actor: ActorSpec
    plan_key: str
    source_language: str
    target_language: str
    source_brief: str | None
    unit_payloads: tuple[dict[str, object], ...]


class TranslationService(Protocol):
    def generate(self, request: GenerationRequest) -> CandidateDraft: ...


class BilingualReviewService(Protocol):
    def review(self, request: ReviewRequest) -> ReviewDraft: ...


class AdjudicationService(Protocol):
    def adjudicate(self, request: AdjudicationRequest) -> AdjudicationDraft: ...


class ChineseEditService(Protocol):
    def edit(self, request: EditRequest) -> EditDraft: ...


class VerificationService(Protocol):
    def verify(self, request: VerificationRequest) -> VerificationDraft: ...


class GlobalReviewService(Protocol):
    def review_document(self, request: GlobalReviewRequest) -> GlobalReviewDraft: ...


@dataclass(frozen=True, slots=True)
class ServiceBundle:
    translation: TranslationService
    bilingual_review: BilingualReviewService
    adjudication: AdjudicationService
    chinese_edit: ChineseEditService
    verification: VerificationService
    global_review: GlobalReviewService
