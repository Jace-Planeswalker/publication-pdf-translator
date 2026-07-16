"""Resumable, quality-first M1 translation kernel."""

from __future__ import annotations

from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedUnit

from .context import build_context_packages
from .errors import ServiceContractError
from .errors import StageConflictError
from .errors import VerificationContractError
from .plan import ContextPackage
from .plan import KernelPlan
from .plan import LaneSpec
from .services import AdjudicationDraft
from .services import AdjudicationRequest
from .services import ApplicationDraft
from .services import EditDraft
from .services import EditRequest
from .services import FindingDraft
from .services import GenerationRequest
from .services import GlobalReviewRequest
from .services import RenderedTargetDraft
from .services import ReviewRequest
from .services import ServiceBundle
from .services import UnitStageInput
from .services import VerificationRequest
from .store import KernelStore
from .terminology import RenderedTarget
from .terminology import TermApplication
from .terminology import TerminologySnapshot
from .workflow import Adjudication
from .workflow import AnonymousOption
from .workflow import Candidate
from .workflow import EditRevision
from .workflow import FindingResolution
from .workflow import GlobalFinding
from .workflow import GlobalReport
from .workflow import Release
from .workflow import ReviewFinding
from .workflow import ReviewReport
from .workflow import TextSpan
from .workflow import UnitOutcome
from .workflow import VerificationFinding
from .workflow import VerificationReport
from .workflow import VerificationVerdict


class TranslationKernel:
    """Run or resume the isolated-candidate, sequential-review quality bus."""

    def __init__(self, store: KernelStore, services: ServiceBundle):
        self.store = store
        self.services = services

    def run(
        self,
        *,
        document: PreparedDocument,
        terminology: TerminologySnapshot,
        plan: KernelPlan,
        activate: bool = True,
    ) -> Release:
        plan.validate_against(document=document, terminology=terminology)
        self.store.register_terminology(document, terminology)
        self.store.register_plan(document, terminology, plan)

        contexts = build_context_packages(
            document=document,
            terminology=terminology,
            plan=plan,
        )
        context_by_unit = {item.unit_key: item for item in contexts}
        unit_by_key = {item.unit_key: item for item in document.units}
        lane_by_key = {item.lane_key: item for item in plan.lanes}
        route_by_unit = {item.unit_key: item for item in plan.routes}

        for context in contexts:
            stored = self.store.load_context(plan.plan_key, context.unit_key)
            if stored is None:
                self.store.record_context(context)
            elif stored != context:
                raise StageConflictError("stored source context differs from the plan")

        outcomes: list[UnitOutcome] = []
        for unit in document.units:
            context = context_by_unit[unit.unit_key]
            stage = UnitStageInput.create(
                document=document,
                plan=plan,
                unit=unit,
                context=context,
                terminology=terminology,
            )
            route = route_by_unit[unit.unit_key]
            candidates = tuple(
                self._candidate(
                    plan=plan,
                    unit=unit,
                    context=context,
                    stage=stage,
                    terminology=terminology,
                    lane=lane_by_key[lane_key],
                )
                for lane_key in route.lane_keys
            )
            options = tuple(
                sorted(
                    (AnonymousOption.from_candidate(item) for item in candidates),
                    key=lambda item: item.option_key,
                )
            )
            review = self._review(
                plan=plan,
                unit=unit,
                stage=stage,
                options=options,
            )
            adjudication = self._adjudication(
                plan=plan,
                unit=unit,
                stage=stage,
                terminology=terminology,
                options=options,
                review=review,
            )
            edit = self._edit(
                plan=plan,
                unit=unit,
                stage=stage,
                terminology=terminology,
                adjudication=adjudication,
            )
            verification = self._verification(
                plan=plan,
                unit=unit,
                stage=stage,
                edit=edit,
                adjudication=adjudication,
            )
            if verification.verdict is not VerificationVerdict.PASS:
                raise VerificationContractError(
                    f"unit {unit.unit_key} remains blocked after independent verification"
                )
            outcomes.append(
                self._outcome(
                    plan=plan,
                    unit=unit,
                    edit=edit,
                    verification=verification,
                )
            )

        outcomes_tuple = tuple(outcomes)
        global_report = self._global_review(
            document=document,
            plan=plan,
            terminology=terminology,
            contexts=context_by_unit,
            units=unit_by_key,
            outcomes=outcomes_tuple,
        )
        if global_report.verdict is not VerificationVerdict.PASS:
            raise VerificationContractError("whole-document review blocked the release")

        expected_release = Release.create(
            document=document,
            plan=plan,
            terminology=terminology,
            outcomes=outcomes_tuple,
            global_report=global_report,
        )
        release = self.store.load_release(plan.plan_key)
        if release is None:
            release = expected_release
        elif release != expected_release:
            raise StageConflictError("stored release differs from the verified outcome set")
        self.store.record_release(
            document=document,
            plan=plan,
            release=release,
            activate=activate,
        )
        return release

    def _candidate(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        context: ContextPackage,
        stage: UnitStageInput,
        terminology: TerminologySnapshot,
        lane: LaneSpec,
    ) -> Candidate:
        stored = self.store.load_candidate(
            plan.plan_key,
            unit.unit_key,
            lane.lane_key,
        )
        if stored is not None:
            rebuilt = Candidate.create(
                plan=plan,
                unit=unit,
                lane=lane,
                context=context,
                terminology=terminology,
                rendered_target=stored.rendered_target,
                translator_note=stored.translator_note,
            )
            if rebuilt != stored:
                raise StageConflictError("stored candidate fails plan replay")
            return stored
        draft = self.services.translation.generate(
            GenerationRequest(actor=lane.actor, lane=lane, stage=stage)
        )
        rendered = _render_target(unit, terminology, draft.rendered_target)
        candidate = Candidate.create(
            plan=plan,
            unit=unit,
            lane=lane,
            context=context,
            terminology=terminology,
            rendered_target=rendered,
            translator_note=draft.translator_note,
        )
        self.store.record_candidate(candidate)
        return candidate

    def _review(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        stage: UnitStageInput,
        options: tuple[AnonymousOption, ...],
    ) -> ReviewReport:
        stored = self.store.load_review(plan.plan_key, unit.unit_key)
        if stored is not None:
            stored.validate(unit=unit, options=options, plan=plan)
            return stored
        draft = self.services.bilingual_review.review(
            ReviewRequest(actor=plan.reviewer, stage=stage, options=options)
        )
        option_by_key = {item.option_key: item for item in options}
        findings: list[ReviewFinding] = []
        for item in draft.findings:
            if item.option_key is None or item.option_key not in option_by_key:
                raise ServiceContractError("review finding cites an unknown option")
            option = option_by_key[item.option_key]
            findings.append(
                ReviewFinding.create(
                    plan=plan,
                    unit=unit,
                    reviewer=plan.reviewer,
                    option=option,
                    category=item.category,
                    severity=item.severity,
                    message=item.message,
                    source_evidence=_source_span(unit, item),
                    target_evidence=_target_span(
                        option.rendered_target.target_text,
                        item,
                    ),
                )
            )
        report = ReviewReport.create(
            plan=plan,
            unit=unit,
            options=options,
            findings=findings,
            recommended_option_keys=draft.recommended_option_keys,
            summary=draft.summary,
        )
        self.store.record_review(report)
        return report

    def _adjudication(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        stage: UnitStageInput,
        terminology: TerminologySnapshot,
        options: tuple[AnonymousOption, ...],
        review: ReviewReport,
    ) -> Adjudication:
        stored = self.store.load_adjudication(plan.plan_key, unit.unit_key)
        if stored is not None:
            if stored.adjudicator_actor_key != plan.adjudicator.actor_key:
                raise StageConflictError("stored adjudication uses another actor")
            stored.rendered_target.validate(unit=unit, terminology=terminology)
            stored.validate(options=options, review=review)
            return stored
        draft: AdjudicationDraft = self.services.adjudication.adjudicate(
            AdjudicationRequest(
                actor=plan.adjudicator,
                stage=stage,
                options=options,
                review_payload=review.as_payload(),
            )
        )
        finding_by_id = {item.finding_id: item for item in review.findings}
        resolutions: list[FindingResolution] = []
        for item in draft.resolutions:
            finding = finding_by_id.get(item.finding_id)
            if finding is None:
                raise ServiceContractError("adjudicator resolved an unknown finding")
            resolutions.append(
                FindingResolution.create(
                    finding=finding,
                    action=item.action,
                    explanation=item.explanation,
                )
            )
        rendered = _render_target(unit, terminology, draft.rendered_target)
        adjudication = Adjudication.create(
            plan=plan,
            unit=unit,
            terminology=terminology,
            options=options,
            review=review,
            mode=draft.mode,
            selected_option_key=draft.selected_option_key,
            rendered_target=rendered,
            resolutions=resolutions,
            rationale=draft.rationale,
        )
        self.store.record_adjudication(adjudication)
        return adjudication

    def _edit(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        stage: UnitStageInput,
        terminology: TerminologySnapshot,
        adjudication: Adjudication,
    ) -> EditRevision:
        stored = self.store.load_edit(plan.plan_key, unit.unit_key)
        if stored is not None:
            if (
                stored.editor_actor_key != plan.editor.actor_key
                or stored.adjudication_id != adjudication.adjudication_id
            ):
                raise StageConflictError("stored edit binding differs")
            stored.rendered_target.validate(unit=unit, terminology=terminology)
            return stored
        draft: EditDraft = self.services.chinese_edit.edit(
            EditRequest(
                actor=plan.editor,
                stage=stage,
                adjudication_payload=adjudication.as_payload(),
            )
        )
        edit = EditRevision.create(
            plan=plan,
            unit=unit,
            terminology=terminology,
            adjudication=adjudication,
            rendered_target=_render_target(unit, terminology, draft.rendered_target),
            summary=draft.summary,
        )
        self.store.record_edit(edit)
        return edit

    def _verification(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        stage: UnitStageInput,
        edit: EditRevision,
        adjudication: Adjudication,
    ) -> VerificationReport:
        stored = self.store.load_verification(plan.plan_key, unit.unit_key)
        if stored is not None:
            stored.validate(plan=plan, unit=unit, edit=edit)
            return stored
        draft = self.services.verification.verify(
            VerificationRequest(
                actor=plan.verifier,
                stage=stage,
                adjudication_payload=adjudication.as_payload(),
                edit_payload=edit.as_payload(),
            )
        )
        findings = tuple(
            VerificationFinding.create(
                plan=plan,
                unit=unit,
                edit=edit,
                category=item.category,
                severity=item.severity,
                message=item.message,
                source_evidence=_source_span(unit, item),
                target_evidence=_target_span(edit.rendered_target.target_text, item),
            )
            for item in draft.findings
        )
        report = VerificationReport.create(
            plan=plan,
            unit=unit,
            edit=edit,
            verdict=draft.verdict,
            edit_impact=draft.edit_impact,
            findings=findings,
            summary=draft.summary,
        )
        self.store.record_verification(report)
        return report

    def _outcome(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        edit: EditRevision,
        verification: VerificationReport,
    ) -> UnitOutcome:
        expected = UnitOutcome.create(
            plan=plan,
            unit=unit,
            edit=edit,
            verification=verification,
        )
        stored = self.store.load_outcome(plan.plan_key, unit.unit_key)
        if stored is None:
            self.store.record_outcome(expected)
            return expected
        if stored != expected:
            raise StageConflictError("stored outcome differs from its verified edit")
        return stored

    def _global_review(
        self,
        *,
        document: PreparedDocument,
        plan: KernelPlan,
        terminology: TerminologySnapshot,
        contexts: dict[str, ContextPackage],
        units: dict[str, PreparedUnit],
        outcomes: tuple[UnitOutcome, ...],
    ) -> GlobalReport:
        stored = self.store.load_global_report(plan.plan_key)
        if stored is not None:
            stored.validate(plan=plan, outcomes=outcomes)
            return stored
        outcome_by_unit = {item.unit_key: item for item in outcomes}
        payloads: list[dict[str, object]] = []
        for unit_key, _revision in plan.unit_revisions:
            stage = UnitStageInput.create(
                document=document,
                plan=plan,
                unit=units[unit_key],
                context=contexts[unit_key],
                terminology=terminology,
            )
            payloads.append(
                {
                    "stage": stage.as_payload(),
                    "outcome": outcome_by_unit[unit_key].as_payload(),
                }
            )
        draft = self.services.global_review.review_document(
            GlobalReviewRequest(
                actor=plan.global_reviewer,
                plan_key=plan.plan_key,
                source_language=document.project.source_language,
                target_language=document.project.target_language,
                source_brief=(
                    plan.source_brief.brief_text
                    if plan.source_brief is not None
                    else None
                ),
                unit_payloads=tuple(payloads),
            )
        )
        findings = tuple(
            GlobalFinding.create(
                plan=plan,
                category=item.category,
                severity=item.severity,
                unit_keys=item.unit_keys,
                message=item.message,
            )
            for item in draft.findings
        )
        report = GlobalReport.create(
            plan=plan,
            outcomes=outcomes,
            verdict=draft.verdict,
            findings=findings,
            summary=draft.summary,
        )
        self.store.record_global_report(report)
        return report


def _render_target(
    unit: PreparedUnit,
    terminology: TerminologySnapshot,
    draft: RenderedTargetDraft,
) -> RenderedTarget:
    try:
        target_text = normalize_text(draft.target_text)
        applications = tuple(
            _application(target_text, item) for item in draft.term_applications
        )
        return RenderedTarget.create(
            unit=unit,
            terminology=terminology,
            target_text=target_text,
            term_applications=applications,
        )
    except (TypeError, ValueError, RuntimeError) as exc:
        raise ServiceContractError(f"invalid rendered target draft: {exc}") from exc


def _application(target_text: str, item: ApplicationDraft) -> TermApplication:
    return TermApplication.create(
        occurrence_key=item.occurrence_key,
        target_text=target_text,
        target_start=item.target_start,
        target_end=item.target_end,
    )


def _source_span(unit: PreparedUnit, finding: FindingDraft) -> TextSpan | None:
    if finding.source_evidence is None:
        return None
    try:
        return TextSpan.create(
            full_text=unit.source_text,
            start=finding.source_evidence.start,
            end=finding.source_evidence.end,
        )
    except ValueError as exc:
        raise ServiceContractError(f"invalid source evidence: {exc}") from exc


def _target_span(target_text: str, finding: FindingDraft) -> TextSpan | None:
    if finding.target_evidence is None:
        return None
    try:
        return TextSpan.create(
            full_text=target_text,
            start=finding.target_evidence.start,
            end=finding.target_evidence.end,
        )
    except ValueError as exc:
        raise ServiceContractError(f"invalid target evidence: {exc}") from exc
