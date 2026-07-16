"""M1 service adapters backed by the durable M2 executor."""

from __future__ import annotations

from dataclasses import dataclass

from pubtrans.m1.services import AdjudicationDraft
from pubtrans.m1.services import AdjudicationRequest
from pubtrans.m1.services import ApplicationDraft
from pubtrans.m1.services import CandidateDraft
from pubtrans.m1.services import EditDraft
from pubtrans.m1.services import EditRequest
from pubtrans.m1.services import FindingDraft
from pubtrans.m1.services import GenerationRequest
from pubtrans.m1.services import GlobalFindingDraft
from pubtrans.m1.services import GlobalReviewDraft
from pubtrans.m1.services import GlobalReviewRequest
from pubtrans.m1.services import RenderedTargetDraft
from pubtrans.m1.services import ResolutionDraft
from pubtrans.m1.services import ReviewDraft
from pubtrans.m1.services import ReviewRequest
from pubtrans.m1.services import ServiceBundle
from pubtrans.m1.services import SpanDraft
from pubtrans.m1.services import UnitStageInput
from pubtrans.m1.services import VerificationDraft
from pubtrans.m1.services import VerificationRequest
from pubtrans.m1.workflow import AdjudicationMode
from pubtrans.m1.workflow import EditImpactVerdict
from pubtrans.m1.workflow import FindingCategory
from pubtrans.m1.workflow import FindingSeverity
from pubtrans.m1.workflow import ResolutionAction
from pubtrans.m1.workflow import VerificationVerdict

from .errors import RecoveryConflictError
from .executor import ResilientExecutor
from .model import BudgetPolicy
from .model import CallDescriptor
from .model import CallEstimate
from .model import CallStage


@dataclass(frozen=True, slots=True)
class EstimateSchedule:
    generation: CallEstimate = CallEstimate(estimated_tokens=8000, estimated_microusd=0)
    review: CallEstimate = CallEstimate(estimated_tokens=10000, estimated_microusd=0)
    adjudication: CallEstimate = CallEstimate(
        estimated_tokens=10000,
        estimated_microusd=0,
    )
    edit: CallEstimate = CallEstimate(estimated_tokens=8000, estimated_microusd=0)
    verification: CallEstimate = CallEstimate(
        estimated_tokens=10000,
        estimated_microusd=0,
    )
    global_review: CallEstimate = CallEstimate(
        estimated_tokens=30000,
        estimated_microusd=0,
    )

    def for_stage(self, stage: CallStage) -> CallEstimate:
        return {
            CallStage.GENERATION: self.generation,
            CallStage.REVIEW: self.review,
            CallStage.ADJUDICATION: self.adjudication,
            CallStage.EDIT: self.edit,
            CallStage.VERIFICATION: self.verification,
            CallStage.GLOBAL_REVIEW: self.global_review,
        }[stage]


class ResilientServices:
    """Wrap a complete M1 bundle without changing kernel semantics."""

    def __init__(
        self,
        underlying: ServiceBundle,
        executor: ResilientExecutor,
        budget: BudgetPolicy,
        *,
        estimates: EstimateSchedule | None = None,
    ) -> None:
        self.underlying = underlying
        self.executor = executor
        self.budget = budget
        self.estimates = estimates or EstimateSchedule()

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
        self._require_scope(request.stage.plan_key)
        descriptor = CallDescriptor.create(
            stage=CallStage.GENERATION,
            dependency_payload={
                "actor": request.actor.as_payload(),
                "lane_key": request.lane.lane_key,
                "stage": _source_stage_dependency(request.stage),
            },
            slot_hint=f"{request.stage.context.unit_key}:{request.lane.lane_key}",
        )
        return self.executor.execute(
            descriptor=descriptor,
            budget=self.budget,
            estimate=self.estimates.for_stage(CallStage.GENERATION),
            operation=lambda: self.underlying.translation.generate(request),
            encode=_candidate_payload,
            decode=_candidate_from_payload,
        )

    def review(self, request: ReviewRequest) -> ReviewDraft:
        self._require_scope(request.stage.plan_key)
        return self.executor.execute(
            descriptor=CallDescriptor.create(
                stage=CallStage.REVIEW,
                dependency_payload=request.as_payload(),
                slot_hint=request.stage.context.unit_key,
            ),
            budget=self.budget,
            estimate=self.estimates.for_stage(CallStage.REVIEW),
            operation=lambda: self.underlying.bilingual_review.review(request),
            encode=_review_payload,
            decode=_review_from_payload,
        )

    def adjudicate(self, request: AdjudicationRequest) -> AdjudicationDraft:
        self._require_scope(request.stage.plan_key)
        return self.executor.execute(
            descriptor=CallDescriptor.create(
                stage=CallStage.ADJUDICATION,
                dependency_payload=request.as_payload(),
                slot_hint=request.stage.context.unit_key,
            ),
            budget=self.budget,
            estimate=self.estimates.for_stage(CallStage.ADJUDICATION),
            operation=lambda: self.underlying.adjudication.adjudicate(request),
            encode=_adjudication_payload,
            decode=_adjudication_from_payload,
        )

    def edit(self, request: EditRequest) -> EditDraft:
        self._require_scope(request.stage.plan_key)
        return self.executor.execute(
            descriptor=CallDescriptor.create(
                stage=CallStage.EDIT,
                dependency_payload=request.as_payload(),
                slot_hint=request.stage.context.unit_key,
            ),
            budget=self.budget,
            estimate=self.estimates.for_stage(CallStage.EDIT),
            operation=lambda: self.underlying.chinese_edit.edit(request),
            encode=_edit_payload,
            decode=_edit_from_payload,
        )

    def verify(self, request: VerificationRequest) -> VerificationDraft:
        self._require_scope(request.stage.plan_key)
        return self.executor.execute(
            descriptor=CallDescriptor.create(
                stage=CallStage.VERIFICATION,
                dependency_payload=request.as_payload(),
                slot_hint=request.stage.context.unit_key,
            ),
            budget=self.budget,
            estimate=self.estimates.for_stage(CallStage.VERIFICATION),
            operation=lambda: self.underlying.verification.verify(request),
            encode=_verification_payload,
            decode=_verification_from_payload,
        )

    def review_document(self, request: GlobalReviewRequest) -> GlobalReviewDraft:
        self._require_scope(request.plan_key)
        return self.executor.execute(
            descriptor=CallDescriptor.create(
                stage=CallStage.GLOBAL_REVIEW,
                dependency_payload=request.as_payload(),
                slot_hint="whole-document",
            ),
            budget=self.budget,
            estimate=self.estimates.for_stage(CallStage.GLOBAL_REVIEW),
            operation=lambda: self.underlying.global_review.review_document(request),
            encode=_global_payload,
            decode=_global_from_payload,
        )

    def _require_scope(self, plan_key: str) -> None:
        if self.budget.scope_key != plan_key:
            raise RecoveryConflictError(
                "resilient service budget belongs to another kernel plan"
            )


def _source_stage_dependency(stage: UnitStageInput) -> dict[str, object]:
    """Exclude plan-derived IDs so unchanged generation survives precise replans."""
    context = stage.context
    return {
        "source_language": stage.source_language,
        "target_language": stage.target_language,
        "source_brief": stage.source_brief,
        "context": {
            "unit_key": context.unit_key,
            "unit_revision": context.unit_revision,
            "current": context.current.as_payload(),
            "before": [item.as_payload() for item in context.before],
            "after": [item.as_payload() for item in context.after],
            "relevant_directive_ids": list(context.relevant_directive_ids),
        },
        "terminology": [item.as_payload() for item in stage.terminology],
    }


def _application_payload(item: ApplicationDraft) -> dict[str, object]:
    return {
        "occurrence_key": item.occurrence_key,
        "target_start": item.target_start,
        "target_end": item.target_end,
    }


def _rendered_payload(item: RenderedTargetDraft) -> dict[str, object]:
    return {
        "target_text": item.target_text,
        "term_applications": [
            _application_payload(application)
            for application in item.term_applications
        ],
    }


def _rendered_from_payload(payload: object) -> RenderedTargetDraft:
    value = _dict(payload, "rendered target")
    applications = _list(value.get("term_applications"), "term applications")
    return RenderedTargetDraft(
        target_text=str(value["target_text"]),
        term_applications=tuple(
            ApplicationDraft(
                occurrence_key=str(_dict(item, "term application")["occurrence_key"]),
                target_start=int(_dict(item, "term application")["target_start"]),
                target_end=int(_dict(item, "term application")["target_end"]),
            )
            for item in applications
        ),
    )


def _span_payload(item: SpanDraft | None) -> dict[str, int] | None:
    return None if item is None else {"start": item.start, "end": item.end}


def _span_from_payload(payload: object) -> SpanDraft | None:
    if payload is None:
        return None
    value = _dict(payload, "span")
    return SpanDraft(start=int(value["start"]), end=int(value["end"]))


def _finding_payload(item: FindingDraft) -> dict[str, object]:
    return {
        "category": item.category.value,
        "severity": item.severity.value,
        "message": item.message,
        "option_key": item.option_key,
        "source_evidence": _span_payload(item.source_evidence),
        "target_evidence": _span_payload(item.target_evidence),
    }


def _finding_from_payload(payload: object) -> FindingDraft:
    value = _dict(payload, "finding")
    option = value.get("option_key")
    return FindingDraft(
        category=FindingCategory(str(value["category"])),
        severity=FindingSeverity(str(value["severity"])),
        message=str(value["message"]),
        option_key=str(option) if option is not None else None,
        source_evidence=_span_from_payload(value.get("source_evidence")),
        target_evidence=_span_from_payload(value.get("target_evidence")),
    )


def _candidate_payload(item: CandidateDraft) -> dict[str, object]:
    return {
        "rendered_target": _rendered_payload(item.rendered_target),
        "translator_note": item.translator_note,
    }


def _candidate_from_payload(payload: object) -> CandidateDraft:
    value = _dict(payload, "candidate")
    return CandidateDraft(
        rendered_target=_rendered_from_payload(value["rendered_target"]),
        translator_note=str(value["translator_note"]),
    )


def _review_payload(item: ReviewDraft) -> dict[str, object]:
    return {
        "findings": [_finding_payload(finding) for finding in item.findings],
        "recommended_option_keys": list(item.recommended_option_keys),
        "summary": item.summary,
    }


def _review_from_payload(payload: object) -> ReviewDraft:
    value = _dict(payload, "review")
    return ReviewDraft(
        findings=tuple(
            _finding_from_payload(item)
            for item in _list(value["findings"], "review findings")
        ),
        recommended_option_keys=tuple(
            str(item)
            for item in _list(
                value["recommended_option_keys"],
                "review recommendations",
            )
        ),
        summary=str(value["summary"]),
    )


def _resolution_payload(item: ResolutionDraft) -> dict[str, object]:
    return {
        "finding_id": item.finding_id,
        "action": item.action.value,
        "explanation": item.explanation,
    }


def _resolution_from_payload(payload: object) -> ResolutionDraft:
    value = _dict(payload, "resolution")
    return ResolutionDraft(
        finding_id=str(value["finding_id"]),
        action=ResolutionAction(str(value["action"])),
        explanation=str(value["explanation"]),
    )


def _adjudication_payload(item: AdjudicationDraft) -> dict[str, object]:
    return {
        "mode": item.mode.value,
        "selected_option_key": item.selected_option_key,
        "rendered_target": _rendered_payload(item.rendered_target),
        "resolutions": [
            _resolution_payload(resolution) for resolution in item.resolutions
        ],
        "rationale": item.rationale,
    }


def _adjudication_from_payload(payload: object) -> AdjudicationDraft:
    value = _dict(payload, "adjudication")
    selected = value.get("selected_option_key")
    return AdjudicationDraft(
        mode=AdjudicationMode(str(value["mode"])),
        selected_option_key=str(selected) if selected is not None else None,
        rendered_target=_rendered_from_payload(value["rendered_target"]),
        resolutions=tuple(
            _resolution_from_payload(item)
            for item in _list(value["resolutions"], "resolutions")
        ),
        rationale=str(value["rationale"]),
    )


def _edit_payload(item: EditDraft) -> dict[str, object]:
    return {
        "rendered_target": _rendered_payload(item.rendered_target),
        "summary": item.summary,
    }


def _edit_from_payload(payload: object) -> EditDraft:
    value = _dict(payload, "edit")
    return EditDraft(
        rendered_target=_rendered_from_payload(value["rendered_target"]),
        summary=str(value["summary"]),
    )


def _verification_payload(item: VerificationDraft) -> dict[str, object]:
    return {
        "verdict": item.verdict.value,
        "edit_impact": item.edit_impact.value,
        "findings": [_finding_payload(finding) for finding in item.findings],
        "summary": item.summary,
    }


def _verification_from_payload(payload: object) -> VerificationDraft:
    value = _dict(payload, "verification")
    return VerificationDraft(
        verdict=VerificationVerdict(str(value["verdict"])),
        edit_impact=EditImpactVerdict(str(value["edit_impact"])),
        findings=tuple(
            _finding_from_payload(item)
            for item in _list(value["findings"], "verification findings")
        ),
        summary=str(value["summary"]),
    )


def _global_finding_payload(item: GlobalFindingDraft) -> dict[str, object]:
    return {
        "category": item.category.value,
        "severity": item.severity.value,
        "unit_keys": list(item.unit_keys),
        "message": item.message,
    }


def _global_finding_from_payload(payload: object) -> GlobalFindingDraft:
    value = _dict(payload, "global finding")
    return GlobalFindingDraft(
        category=FindingCategory(str(value["category"])),
        severity=FindingSeverity(str(value["severity"])),
        unit_keys=tuple(
            str(item) for item in _list(value["unit_keys"], "global units")
        ),
        message=str(value["message"]),
    )


def _global_payload(item: GlobalReviewDraft) -> dict[str, object]:
    return {
        "verdict": item.verdict.value,
        "findings": [
            _global_finding_payload(finding) for finding in item.findings
        ],
        "summary": item.summary,
    }


def _global_from_payload(payload: object) -> GlobalReviewDraft:
    value = _dict(payload, "global review")
    return GlobalReviewDraft(
        verdict=VerificationVerdict(str(value["verdict"])),
        findings=tuple(
            _global_finding_from_payload(item)
            for item in _list(value["findings"], "global findings")
        ),
        summary=str(value["summary"]),
    )


def _dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RecoveryConflictError(f"cached {name} payload is not an object")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise RecoveryConflictError(f"cached {name} payload is not a list")
    return value
