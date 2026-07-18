"""Immutable M1 candidate, review, edit, verification, and release values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.errors import IdentityError
from pubtrans.m0v2.model import ApprovalRevision
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedUnit
from pubtrans.m0v2.provider import validate_approval_set

from .errors import PlanBindingError
from .errors import ReleaseContractError
from .errors import ReviewContractError
from .errors import VerificationContractError
from .plan import ActorSpec
from .plan import ContextPackage
from .plan import KernelPlan
from .plan import LaneSpec
from .terminology import RenderedTarget
from .terminology import TerminologySnapshot


CANDIDATE_NAMESPACE = "pubtrans.candidate/v1"
OPTION_NAMESPACE = "pubtrans.anonymous-option/v1"
REVIEW_FINDING_NAMESPACE = "pubtrans.review-finding/v1"
REVIEW_NAMESPACE = "pubtrans.review-report/v1"
ADJUDICATION_NAMESPACE = "pubtrans.adjudication/v1"
EDIT_NAMESPACE = "pubtrans.edit/v1"
VERIFICATION_FINDING_NAMESPACE = "pubtrans.verification-finding/v1"
VERIFICATION_NAMESPACE = "pubtrans.verification/v1"
OUTCOME_NAMESPACE = "pubtrans.unit-outcome/v1"
RELEASE_NAMESPACE = "pubtrans.release/v1"
TEXT_SPAN_NAMESPACE = "pubtrans.text-span/v1"


def _nonempty(name: str, value: str) -> str:
    result = normalize_text(value).strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


class FindingSeverity(str, Enum):
    INFO = "INFO"
    MINOR = "MINOR"
    MAJOR = "MAJOR"
    CRITICAL = "CRITICAL"
    BLOCKING = "BLOCKING"


class FindingCategory(str, Enum):
    ACCURACY = "ACCURACY"
    MISTRANSLATION = "MISTRANSLATION"
    OMISSION = "OMISSION"
    ADDITION = "ADDITION"
    UNTRANSLATED = "UNTRANSLATED"
    TERMINOLOGY = "TERMINOLOGY"
    PROPER_NAME = "PROPER_NAME"
    NUMBER = "NUMBER"
    NEGATION_MODALITY = "NEGATION_MODALITY"
    LOGIC_RELATION = "LOGIC_RELATION"
    REFERENCE = "REFERENCE"
    SOURCE_RETENTION = "SOURCE_RETENTION"
    REGISTER = "REGISTER"
    STYLE = "STYLE"
    FLUENCY = "FLUENCY"
    CHINESE_EXPRESSION = "CHINESE_EXPRESSION"
    PUNCTUATION = "PUNCTUATION"
    PROTECTED_STRUCTURE = "PROTECTED_STRUCTURE"
    CONTEXT_COHESION = "CONTEXT_COHESION"
    OTHER = "OTHER"


class AdjudicationMode(str, Enum):
    SELECT = "SELECT"
    SYNTHESIZE = "SYNTHESIZE"


class VerificationVerdict(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class ResolutionAction(str, Enum):
    AVOIDED = "AVOIDED"
    CORRECTED = "CORRECTED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    PRESERVED_SOURCE_AMBIGUITY = "PRESERVED_SOURCE_AMBIGUITY"


class EditImpactVerdict(str, Enum):
    IMPROVES = "IMPROVES"
    EQUIVALENT = "EQUIVALENT"
    DEGRADES = "DEGRADES"


def _requires_resolution(severity: FindingSeverity) -> bool:
    return severity in {
        FindingSeverity.MAJOR,
        FindingSeverity.CRITICAL,
        FindingSeverity.BLOCKING,
    }


@dataclass(frozen=True, slots=True)
class TextSpan:
    """An exact, replay-safe citation into one canonical text value."""

    span_id: str
    start: int
    end: int
    text: str

    @classmethod
    def create(cls, *, full_text: str, start: int, end: int) -> "TextSpan":
        full_text = normalize_text(full_text)
        if not 0 <= start < end <= len(full_text):
            raise ValueError("text span is outside its cited text")
        cited = full_text[start:end]
        if not cited.strip():
            raise ValueError("text span cannot cite blank text")
        payload = {"start": start, "end": end, "text": cited}
        return cls(span_id=digest(TEXT_SPAN_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        require_sha256("span_id", self.span_id)
        if self.text != normalize_text(self.text) or not self.text.strip():
            raise ValueError("text span citation is not canonical")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("text span offsets are invalid")
        if self.end - self.start != len(self.text):
            raise ValueError("text span length does not match its citation")
        if self.span_id != digest(TEXT_SPAN_NAMESPACE, self._identity_payload()):
            raise IdentityError("text span id mismatch")

    def validate(self, full_text: str) -> None:
        full_text = normalize_text(full_text)
        if self.end > len(full_text) or full_text[self.start : self.end] != self.text:
            raise ValueError("text span citation is stale or ambiguous")

    def _identity_payload(self) -> dict[str, object]:
        return {"start": self.start, "end": self.end, "text": self.text}

    def as_payload(self) -> dict[str, object]:
        return {"span_id": self.span_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TextSpan":
        return cls(
            span_id=str(payload["span_id"]),
            start=int(payload["start"]),
            end=int(payload["end"]),
            text=str(payload["text"]),
        )


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    plan_key: str
    unit_key: str
    unit_revision: str
    lane_key: str
    context_key: str
    rendered_target: RenderedTarget
    translator_note: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        lane: LaneSpec,
        context: ContextPackage,
        terminology: TerminologySnapshot,
        rendered_target: RenderedTarget,
        translator_note: str,
    ) -> "Candidate":
        if lane.lane_key not in {item.lane_key for item in plan.lanes}:
            raise PlanBindingError("candidate lane is not in the kernel plan")
        route = next(
            (item for item in plan.routes if item.unit_key == unit.unit_key),
            None,
        )
        if route is None or lane.lane_key not in route.lane_keys:
            raise PlanBindingError("candidate lane is not assigned to this unit")
        if context.plan_key != plan.plan_key:
            raise PlanBindingError("candidate context belongs to another plan")
        if context.unit_key != unit.unit_key or context.unit_revision != unit.unit_revision:
            raise PlanBindingError("candidate context belongs to another unit")
        rendered_target.validate(unit=unit, terminology=terminology)
        translator_note = normalize_text(translator_note).strip()
        payload = {
            "plan_key": plan.plan_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "lane_key": lane.lane_key,
            "context_key": context.context_key,
            "rendered_target": rendered_target.as_payload(),
            "translator_note": translator_note,
        }
        return cls(
            candidate_id=digest(CANDIDATE_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            lane_key=lane.lane_key,
            context_key=context.context_key,
            rendered_target=rendered_target,
            translator_note=translator_note,
        )

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "plan_key",
            "unit_key",
            "unit_revision",
            "lane_key",
            "context_key",
        ):
            require_sha256(name, getattr(self, name))
        if self.translator_note != normalize_text(self.translator_note).strip():
            raise ValueError("translator note is not canonical")
        if self.rendered_target.unit_key != self.unit_key:
            raise PlanBindingError("candidate target belongs to another unit")
        if self.rendered_target.unit_revision != self.unit_revision:
            raise PlanBindingError("candidate target belongs to another unit revision")
        if self.candidate_id != digest(CANDIDATE_NAMESPACE, self._identity_payload()):
            raise IdentityError("candidate id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "lane_key": self.lane_key,
            "context_key": self.context_key,
            "rendered_target": self.rendered_target.as_payload(),
            "translator_note": self.translator_note,
        }

    def as_payload(self) -> dict[str, object]:
        return {"candidate_id": self.candidate_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "Candidate":
        target = payload["rendered_target"]
        if not isinstance(target, dict):
            raise ValueError("candidate target payload is malformed")
        return cls(
            candidate_id=str(payload["candidate_id"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            lane_key=str(payload["lane_key"]),
            context_key=str(payload["context_key"]),
            rendered_target=RenderedTarget.from_payload(target),
            translator_note=str(payload["translator_note"]),
        )


@dataclass(frozen=True, slots=True)
class AnonymousOption:
    option_key: str
    unit_key: str
    unit_revision: str
    rendered_target: RenderedTarget

    @classmethod
    def from_candidate(cls, candidate: Candidate) -> "AnonymousOption":
        payload = {
            "unit_key": candidate.unit_key,
            "unit_revision": candidate.unit_revision,
            "candidate_commitment": candidate.candidate_id,
        }
        return cls(
            option_key=digest(OPTION_NAMESPACE, payload),
            unit_key=candidate.unit_key,
            unit_revision=candidate.unit_revision,
            rendered_target=candidate.rendered_target,
        )

    def __post_init__(self) -> None:
        for name in ("option_key", "unit_key", "unit_revision"):
            require_sha256(name, getattr(self, name))
        if self.rendered_target.unit_key != self.unit_key:
            raise PlanBindingError("anonymous option belongs to another unit")
        if self.rendered_target.unit_revision != self.unit_revision:
            raise PlanBindingError("anonymous option belongs to another unit revision")

    def as_payload(self) -> dict[str, object]:
        """Return the blind-review payload; candidate and lane provenance is absent."""
        return {
            "option_key": self.option_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "rendered_target": self.rendered_target.as_payload(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AnonymousOption":
        target = payload["rendered_target"]
        if not isinstance(target, dict):
            raise ValueError("anonymous option target payload is malformed")
        return cls(
            option_key=str(payload["option_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            rendered_target=RenderedTarget.from_payload(target),
        )


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    finding_id: str
    plan_key: str
    unit_revision: str
    reviewer_actor_key: str
    option_key: str
    category: FindingCategory
    severity: FindingSeverity
    message: str
    source_evidence: TextSpan | None
    target_evidence: TextSpan | None

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        reviewer: ActorSpec,
        option: AnonymousOption,
        category: FindingCategory,
        severity: FindingSeverity,
        message: str,
        source_evidence: TextSpan | None = None,
        target_evidence: TextSpan | None = None,
    ) -> "ReviewFinding":
        if reviewer.actor_key != plan.reviewer.actor_key:
            raise ReviewContractError("review finding uses the wrong reviewer")
        if option.unit_key != unit.unit_key or option.unit_revision != unit.unit_revision:
            raise ReviewContractError("review finding option belongs to another unit")
        message = _nonempty("review finding message", message)
        try:
            if source_evidence is not None:
                source_evidence.validate(unit.source_text)
            if target_evidence is not None:
                target_evidence.validate(option.rendered_target.target_text)
        except ValueError as exc:
            raise ReviewContractError(str(exc)) from exc
        if _requires_resolution(severity) and (
            source_evidence is None and target_evidence is None
        ):
            raise ReviewContractError(
                "serious review finding requires an exact source or target citation"
            )
        payload = {
            "plan_key": plan.plan_key,
            "unit_revision": unit.unit_revision,
            "reviewer_actor_key": reviewer.actor_key,
            "option_key": option.option_key,
            "category": category.value,
            "severity": severity.value,
            "message": message,
            "source_evidence": (
                source_evidence.as_payload() if source_evidence is not None else None
            ),
            "target_evidence": (
                target_evidence.as_payload() if target_evidence is not None else None
            ),
        }
        return cls(
            finding_id=digest(REVIEW_FINDING_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_revision=unit.unit_revision,
            reviewer_actor_key=reviewer.actor_key,
            option_key=option.option_key,
            category=category,
            severity=severity,
            message=message,
            source_evidence=source_evidence,
            target_evidence=target_evidence,
        )

    def __post_init__(self) -> None:
        for name in (
            "finding_id",
            "plan_key",
            "unit_revision",
            "reviewer_actor_key",
            "option_key",
        ):
            require_sha256(name, getattr(self, name))
        if self.message != _nonempty("review finding message", self.message):
            raise ValueError("review finding message is not canonical")
        if self.finding_id != digest(
            REVIEW_FINDING_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("review finding id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_revision": self.unit_revision,
            "reviewer_actor_key": self.reviewer_actor_key,
            "option_key": self.option_key,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "source_evidence": (
                self.source_evidence.as_payload()
                if self.source_evidence is not None
                else None
            ),
            "target_evidence": (
                self.target_evidence.as_payload()
                if self.target_evidence is not None
                else None
            ),
        }

    def as_payload(self) -> dict[str, object]:
        return {"finding_id": self.finding_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ReviewFinding":
        raw_source = payload.get("source_evidence")
        raw_target = payload.get("target_evidence")
        if raw_source is not None and not isinstance(raw_source, dict):
            raise ValueError("review source evidence payload is malformed")
        if raw_target is not None and not isinstance(raw_target, dict):
            raise ValueError("review target evidence payload is malformed")
        return cls(
            finding_id=str(payload["finding_id"]),
            plan_key=str(payload["plan_key"]),
            unit_revision=str(payload["unit_revision"]),
            reviewer_actor_key=str(payload["reviewer_actor_key"]),
            option_key=str(payload["option_key"]),
            category=FindingCategory(str(payload["category"])),
            severity=FindingSeverity(str(payload["severity"])),
            message=str(payload["message"]),
            source_evidence=(
                TextSpan.from_payload(raw_source)
                if isinstance(raw_source, dict)
                else None
            ),
            target_evidence=(
                TextSpan.from_payload(raw_target)
                if isinstance(raw_target, dict)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ReviewReport:
    report_id: str
    plan_key: str
    unit_key: str
    unit_revision: str
    reviewer_actor_key: str
    option_set_id: str
    findings: tuple[ReviewFinding, ...]
    recommended_option_keys: tuple[str, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        options: tuple[AnonymousOption, ...] | list[AnonymousOption],
        findings: tuple[ReviewFinding, ...] | list[ReviewFinding],
        recommended_option_keys: tuple[str, ...] | list[str],
        summary: str,
    ) -> "ReviewReport":
        options = tuple(sorted(options, key=lambda item: item.option_key))
        findings = tuple(sorted(findings, key=lambda item: item.finding_id))
        recommended = tuple(recommended_option_keys)
        option_set_id = digest(
            "pubtrans.review-option-set/v1",
            [item.as_payload() for item in options],
        )
        summary = _nonempty("review summary", summary)
        payload = {
            "plan_key": plan.plan_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "reviewer_actor_key": plan.reviewer.actor_key,
            "option_set_id": option_set_id,
            "findings": [item.as_payload() for item in findings],
            "recommended_option_keys": list(recommended),
            "summary": summary,
        }
        result = cls(
            report_id=digest(REVIEW_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            reviewer_actor_key=plan.reviewer.actor_key,
            option_set_id=option_set_id,
            findings=findings,
            recommended_option_keys=recommended,
            summary=summary,
        )
        result.validate(unit=unit, options=options, plan=plan)
        return result

    def __post_init__(self) -> None:
        for name in (
            "report_id",
            "plan_key",
            "unit_key",
            "unit_revision",
            "reviewer_actor_key",
            "option_set_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.summary != _nonempty("review summary", self.summary):
            raise ValueError("review summary is not canonical")
        if self.findings != tuple(sorted(self.findings, key=lambda item: item.finding_id)):
            raise ValueError("review findings are not in canonical order")
        if self.report_id != digest(REVIEW_NAMESPACE, self._identity_payload()):
            raise IdentityError("review report id mismatch")

    def validate(
        self,
        *,
        unit: PreparedUnit,
        options: tuple[AnonymousOption, ...],
        plan: KernelPlan,
    ) -> None:
        if self.plan_key != plan.plan_key or self.reviewer_actor_key != plan.reviewer.actor_key:
            raise ReviewContractError("review report belongs to another plan or reviewer")
        if self.unit_key != unit.unit_key or self.unit_revision != unit.unit_revision:
            raise ReviewContractError("review report belongs to another unit")
        option_by_key = {item.option_key: item for item in options}
        if len(option_by_key) != len(options):
            raise ReviewContractError("review options contain duplicate keys")
        route = next(
            (item for item in plan.routes if item.unit_key == unit.unit_key),
            None,
        )
        if route is None or len(options) != len(route.lane_keys):
            raise ReviewContractError("review options do not match the unit risk route")
        expected_set_id = digest(
            "pubtrans.review-option-set/v1",
            [item.as_payload() for item in sorted(options, key=lambda item: item.option_key)],
        )
        if self.option_set_id != expected_set_id:
            raise ReviewContractError("review option set differs")
        if len(self.recommended_option_keys) != len(set(self.recommended_option_keys)):
            raise ReviewContractError("review recommendations contain duplicates")
        if any(key not in option_by_key for key in self.recommended_option_keys):
            raise ReviewContractError("review recommends an unknown option")
        finding_ids = [item.finding_id for item in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ReviewContractError("review report contains duplicate findings")
        for finding in self.findings:
            if (
                finding.plan_key != self.plan_key
                or finding.unit_revision != self.unit_revision
                or finding.reviewer_actor_key != self.reviewer_actor_key
            ):
                raise ReviewContractError("review finding binding differs")
            option = option_by_key.get(finding.option_key)
            if option is None:
                raise ReviewContractError("review finding cites an unknown option")
            try:
                if finding.source_evidence is not None:
                    finding.source_evidence.validate(unit.source_text)
                if finding.target_evidence is not None:
                    finding.target_evidence.validate(
                        option.rendered_target.target_text
                    )
            except ValueError as exc:
                raise ReviewContractError(str(exc)) from exc

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "reviewer_actor_key": self.reviewer_actor_key,
            "option_set_id": self.option_set_id,
            "findings": [item.as_payload() for item in self.findings],
            "recommended_option_keys": list(self.recommended_option_keys),
            "summary": self.summary,
        }

    def as_payload(self) -> dict[str, object]:
        return {"report_id": self.report_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ReviewReport":
        raw_findings = payload["findings"]
        raw_recommended = payload["recommended_option_keys"]
        if (
            not isinstance(raw_findings, list)
            or not isinstance(raw_recommended, list)
            or any(not isinstance(item, dict) for item in raw_findings)
        ):
            raise ValueError("review report payload is malformed")
        return cls(
            report_id=str(payload["report_id"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            reviewer_actor_key=str(payload["reviewer_actor_key"]),
            option_set_id=str(payload["option_set_id"]),
            findings=tuple(ReviewFinding.from_payload(item) for item in raw_findings),
            recommended_option_keys=tuple(str(item) for item in raw_recommended),
            summary=str(payload["summary"]),
        )


@dataclass(frozen=True, slots=True)
class FindingResolution:
    resolution_id: str
    finding_id: str
    action: ResolutionAction
    explanation: str

    @classmethod
    def create(
        cls,
        *,
        finding: ReviewFinding,
        action: ResolutionAction,
        explanation: str,
    ) -> "FindingResolution":
        explanation = _nonempty("finding resolution", explanation)
        payload = {
            "finding_id": finding.finding_id,
            "action": action.value,
            "explanation": explanation,
        }
        return cls(
            resolution_id=digest("pubtrans.finding-resolution/v1", payload),
            finding_id=finding.finding_id,
            action=action,
            explanation=explanation,
        )

    def __post_init__(self) -> None:
        require_sha256("resolution_id", self.resolution_id)
        require_sha256("finding_id", self.finding_id)
        if self.explanation != _nonempty("finding resolution", self.explanation):
            raise ValueError("finding resolution is not canonical")
        if self.resolution_id != digest(
            "pubtrans.finding-resolution/v1",
            self._identity_payload(),
        ):
            raise IdentityError("finding resolution id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "action": self.action.value,
            "explanation": self.explanation,
        }

    def as_payload(self) -> dict[str, object]:
        return {"resolution_id": self.resolution_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "FindingResolution":
        return cls(
            resolution_id=str(payload["resolution_id"]),
            finding_id=str(payload["finding_id"]),
            action=ResolutionAction(str(payload["action"])),
            explanation=str(payload["explanation"]),
        )


@dataclass(frozen=True, slots=True)
class Adjudication:
    adjudication_id: str
    plan_key: str
    unit_key: str
    unit_revision: str
    adjudicator_actor_key: str
    review_report_id: str
    mode: AdjudicationMode
    selected_option_key: str | None
    rendered_target: RenderedTarget
    resolutions: tuple[FindingResolution, ...]
    rationale: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        terminology: TerminologySnapshot,
        options: tuple[AnonymousOption, ...] | list[AnonymousOption],
        review: ReviewReport,
        mode: AdjudicationMode,
        selected_option_key: str | None,
        rendered_target: RenderedTarget,
        resolutions: tuple[FindingResolution, ...] | list[FindingResolution],
        rationale: str,
    ) -> "Adjudication":
        options = tuple(sorted(options, key=lambda item: item.option_key))
        review.validate(unit=unit, options=options, plan=plan)
        rendered_target.validate(unit=unit, terminology=terminology)
        resolutions = tuple(sorted(resolutions, key=lambda item: item.finding_id))
        rationale = _nonempty("adjudication rationale", rationale)
        payload = {
            "plan_key": plan.plan_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "adjudicator_actor_key": plan.adjudicator.actor_key,
            "review_report_id": review.report_id,
            "mode": mode.value,
            "selected_option_key": selected_option_key,
            "rendered_target": rendered_target.as_payload(),
            "resolutions": [item.as_payload() for item in resolutions],
            "rationale": rationale,
        }
        result = cls(
            adjudication_id=digest(ADJUDICATION_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            adjudicator_actor_key=plan.adjudicator.actor_key,
            review_report_id=review.report_id,
            mode=mode,
            selected_option_key=selected_option_key,
            rendered_target=rendered_target,
            resolutions=resolutions,
            rationale=rationale,
        )
        result.validate(options=options, review=review)
        return result

    def __post_init__(self) -> None:
        for name in (
            "adjudication_id",
            "plan_key",
            "unit_key",
            "unit_revision",
            "adjudicator_actor_key",
            "review_report_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.selected_option_key is not None:
            require_sha256("selected_option_key", self.selected_option_key)
        if self.rationale != _nonempty("adjudication rationale", self.rationale):
            raise ValueError("adjudication rationale is not canonical")
        if self.rendered_target.unit_key != self.unit_key:
            raise PlanBindingError("adjudication target belongs to another unit")
        finding_ids = [item.finding_id for item in self.resolutions]
        if finding_ids != sorted(finding_ids) or len(finding_ids) != len(
            set(finding_ids)
        ):
            raise ReviewContractError(
                "adjudication resolutions are not canonical and unique"
            )
        if self.adjudication_id != digest(
            ADJUDICATION_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("adjudication id mismatch")

    def validate(
        self,
        *,
        options: tuple[AnonymousOption, ...],
        review: ReviewReport,
    ) -> None:
        option_by_key = {item.option_key: item for item in options}
        if self.review_report_id != review.report_id:
            raise ReviewContractError("adjudication uses another review report")
        if self.mode is AdjudicationMode.SELECT:
            if self.selected_option_key not in option_by_key:
                raise ReviewContractError("selection requires a known option")
            selected = option_by_key[self.selected_option_key]
            if self.rendered_target != selected.rendered_target:
                raise ReviewContractError("declared selection contains hidden edits")
        elif self.selected_option_key is not None:
            raise ReviewContractError("synthesis must not masquerade as selection")

        finding_by_id = {item.finding_id: item for item in review.findings}
        resolution_by_id = {item.finding_id: item for item in self.resolutions}
        if not set(resolution_by_id).issubset(finding_by_id):
            raise ReviewContractError("adjudication resolves an unknown finding")
        required = {
            item.finding_id for item in review.findings if _requires_resolution(item.severity)
        }
        if not required.issubset(resolution_by_id):
            raise ReviewContractError("adjudication leaves a serious finding unresolved")
        for finding_id in required:
            finding = finding_by_id[finding_id]
            resolution = resolution_by_id[finding_id]
            selected_finding = finding.option_key == self.selected_option_key
            if self.mode is AdjudicationMode.SELECT:
                if not selected_finding and resolution.action not in {
                    ResolutionAction.AVOIDED,
                    ResolutionAction.FALSE_POSITIVE,
                }:
                    raise ReviewContractError(
                        "unselected-option finding must be avoided or disproved"
                    )
                if selected_finding and resolution.action not in {
                    ResolutionAction.FALSE_POSITIVE,
                    ResolutionAction.PRESERVED_SOURCE_AMBIGUITY,
                }:
                    raise ReviewContractError(
                        "an exact selection cannot claim to correct its own text"
                    )
                if selected_finding and finding.severity in {
                    FindingSeverity.CRITICAL,
                    FindingSeverity.BLOCKING,
                }:
                    if resolution.action is not ResolutionAction.FALSE_POSITIVE:
                        raise ReviewContractError(
                            "critical selected-option finding cannot remain"
                        )
            elif resolution.action is ResolutionAction.AVOIDED:
                raise ReviewContractError("synthesis cannot avoid a finding by selection")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "adjudicator_actor_key": self.adjudicator_actor_key,
            "review_report_id": self.review_report_id,
            "mode": self.mode.value,
            "selected_option_key": self.selected_option_key,
            "rendered_target": self.rendered_target.as_payload(),
            "resolutions": [item.as_payload() for item in self.resolutions],
            "rationale": self.rationale,
        }

    def as_payload(self) -> dict[str, object]:
        return {"adjudication_id": self.adjudication_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "Adjudication":
        raw_target = payload["rendered_target"]
        raw_resolutions = payload["resolutions"]
        if not isinstance(raw_target, dict) or not isinstance(
            raw_resolutions, list
        ) or any(not isinstance(item, dict) for item in raw_resolutions):
            raise ValueError("adjudication payload is malformed")
        selected = payload.get("selected_option_key")
        return cls(
            adjudication_id=str(payload["adjudication_id"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            adjudicator_actor_key=str(payload["adjudicator_actor_key"]),
            review_report_id=str(payload["review_report_id"]),
            mode=AdjudicationMode(str(payload["mode"])),
            selected_option_key=(str(selected) if selected is not None else None),
            rendered_target=RenderedTarget.from_payload(raw_target),
            resolutions=tuple(
                FindingResolution.from_payload(item) for item in raw_resolutions
            ),
            rationale=str(payload["rationale"]),
        )


@dataclass(frozen=True, slots=True)
class EditRevision:
    edit_id: str
    plan_key: str
    unit_key: str
    unit_revision: str
    editor_actor_key: str
    adjudication_id: str
    input_rendered_target_id: str
    rendered_target: RenderedTarget
    changed: bool
    summary: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        terminology: TerminologySnapshot,
        adjudication: Adjudication,
        rendered_target: RenderedTarget,
        summary: str,
    ) -> "EditRevision":
        if adjudication.plan_key != plan.plan_key:
            raise PlanBindingError("edit uses an adjudication from another plan")
        if (
            adjudication.unit_key != unit.unit_key
            or adjudication.unit_revision != unit.unit_revision
        ):
            raise PlanBindingError("edit uses an adjudication from another unit")
        rendered_target.validate(unit=unit, terminology=terminology)
        summary = _nonempty("edit summary", summary)
        changed = rendered_target != adjudication.rendered_target
        payload = {
            "plan_key": plan.plan_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "editor_actor_key": plan.editor.actor_key,
            "adjudication_id": adjudication.adjudication_id,
            "input_rendered_target_id": (
                adjudication.rendered_target.rendered_target_id
            ),
            "rendered_target": rendered_target.as_payload(),
            "changed": changed,
            "summary": summary,
        }
        return cls(
            edit_id=digest(EDIT_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            editor_actor_key=plan.editor.actor_key,
            adjudication_id=adjudication.adjudication_id,
            input_rendered_target_id=(
                adjudication.rendered_target.rendered_target_id
            ),
            rendered_target=rendered_target,
            changed=changed,
            summary=summary,
        )

    def __post_init__(self) -> None:
        for name in (
            "edit_id",
            "plan_key",
            "unit_key",
            "unit_revision",
            "editor_actor_key",
            "adjudication_id",
            "input_rendered_target_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.summary != _nonempty("edit summary", self.summary):
            raise ValueError("edit summary is not canonical")
        if self.rendered_target.unit_key != self.unit_key:
            raise PlanBindingError("edit target belongs to another unit")
        if self.edit_id != digest(EDIT_NAMESPACE, self._identity_payload()):
            raise IdentityError("edit id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "editor_actor_key": self.editor_actor_key,
            "adjudication_id": self.adjudication_id,
            "input_rendered_target_id": self.input_rendered_target_id,
            "rendered_target": self.rendered_target.as_payload(),
            "changed": self.changed,
            "summary": self.summary,
        }

    def as_payload(self) -> dict[str, object]:
        return {"edit_id": self.edit_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "EditRevision":
        raw_target = payload["rendered_target"]
        if not isinstance(raw_target, dict):
            raise ValueError("edit target payload is malformed")
        return cls(
            edit_id=str(payload["edit_id"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            editor_actor_key=str(payload["editor_actor_key"]),
            adjudication_id=str(payload["adjudication_id"]),
            input_rendered_target_id=str(payload["input_rendered_target_id"]),
            rendered_target=RenderedTarget.from_payload(raw_target),
            changed=bool(payload["changed"]),
            summary=str(payload["summary"]),
        )


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    finding_id: str
    plan_key: str
    unit_revision: str
    verifier_actor_key: str
    category: FindingCategory
    severity: FindingSeverity
    message: str
    source_evidence: TextSpan | None
    target_evidence: TextSpan | None

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        edit: EditRevision,
        category: FindingCategory,
        severity: FindingSeverity,
        message: str,
        source_evidence: TextSpan | None = None,
        target_evidence: TextSpan | None = None,
    ) -> "VerificationFinding":
        message = _nonempty("verification finding", message)
        try:
            if source_evidence is not None:
                source_evidence.validate(unit.source_text)
            if target_evidence is not None:
                target_evidence.validate(edit.rendered_target.target_text)
        except ValueError as exc:
            raise VerificationContractError(str(exc)) from exc
        if _requires_resolution(severity) and (
            source_evidence is None and target_evidence is None
        ):
            raise VerificationContractError(
                "serious verification finding requires source or target evidence"
            )
        payload = {
            "plan_key": plan.plan_key,
            "unit_revision": unit.unit_revision,
            "verifier_actor_key": plan.verifier.actor_key,
            "category": category.value,
            "severity": severity.value,
            "message": message,
            "source_evidence": (
                source_evidence.as_payload() if source_evidence is not None else None
            ),
            "target_evidence": (
                target_evidence.as_payload() if target_evidence is not None else None
            ),
        }
        return cls(
            finding_id=digest(VERIFICATION_FINDING_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_revision=unit.unit_revision,
            verifier_actor_key=plan.verifier.actor_key,
            category=category,
            severity=severity,
            message=message,
            source_evidence=source_evidence,
            target_evidence=target_evidence,
        )

    def __post_init__(self) -> None:
        for name in (
            "finding_id",
            "plan_key",
            "unit_revision",
            "verifier_actor_key",
        ):
            require_sha256(name, getattr(self, name))
        if self.message != _nonempty("verification finding", self.message):
            raise ValueError("verification finding message is not canonical")
        if self.finding_id != digest(
            VERIFICATION_FINDING_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("verification finding id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_revision": self.unit_revision,
            "verifier_actor_key": self.verifier_actor_key,
            "category": self.category.value,
            "severity": self.severity.value,
            "message": self.message,
            "source_evidence": (
                self.source_evidence.as_payload()
                if self.source_evidence is not None
                else None
            ),
            "target_evidence": (
                self.target_evidence.as_payload()
                if self.target_evidence is not None
                else None
            ),
        }

    def as_payload(self) -> dict[str, object]:
        return {"finding_id": self.finding_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "VerificationFinding":
        raw_source = payload.get("source_evidence")
        raw_target = payload.get("target_evidence")
        if raw_source is not None and not isinstance(raw_source, dict):
            raise ValueError("verification source evidence payload is malformed")
        if raw_target is not None and not isinstance(raw_target, dict):
            raise ValueError("verification target evidence payload is malformed")
        return cls(
            finding_id=str(payload["finding_id"]),
            plan_key=str(payload["plan_key"]),
            unit_revision=str(payload["unit_revision"]),
            verifier_actor_key=str(payload["verifier_actor_key"]),
            category=FindingCategory(str(payload["category"])),
            severity=FindingSeverity(str(payload["severity"])),
            message=str(payload["message"]),
            source_evidence=(
                TextSpan.from_payload(raw_source)
                if isinstance(raw_source, dict)
                else None
            ),
            target_evidence=(
                TextSpan.from_payload(raw_target)
                if isinstance(raw_target, dict)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class VerificationReport:
    report_id: str
    plan_key: str
    unit_key: str
    unit_revision: str
    verifier_actor_key: str
    edit_id: str
    verdict: VerificationVerdict
    edit_impact: EditImpactVerdict
    findings: tuple[VerificationFinding, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        edit: EditRevision,
        verdict: VerificationVerdict,
        edit_impact: EditImpactVerdict,
        findings: tuple[VerificationFinding, ...] | list[VerificationFinding],
        summary: str,
    ) -> "VerificationReport":
        findings = tuple(sorted(findings, key=lambda item: item.finding_id))
        summary = _nonempty("verification summary", summary)
        payload = {
            "plan_key": plan.plan_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "verifier_actor_key": plan.verifier.actor_key,
            "edit_id": edit.edit_id,
            "verdict": verdict.value,
            "edit_impact": edit_impact.value,
            "findings": [item.as_payload() for item in findings],
            "summary": summary,
        }
        result = cls(
            report_id=digest(VERIFICATION_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            verifier_actor_key=plan.verifier.actor_key,
            edit_id=edit.edit_id,
            verdict=verdict,
            edit_impact=edit_impact,
            findings=findings,
            summary=summary,
        )
        result.validate(plan=plan, unit=unit, edit=edit)
        return result

    def __post_init__(self) -> None:
        for name in (
            "report_id",
            "plan_key",
            "unit_key",
            "unit_revision",
            "verifier_actor_key",
            "edit_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.summary != _nonempty("verification summary", self.summary):
            raise ValueError("verification summary is not canonical")
        if self.findings != tuple(sorted(self.findings, key=lambda item: item.finding_id)):
            raise ValueError("verification findings are not canonical")
        if self.report_id != digest(
            VERIFICATION_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("verification report id mismatch")

    def validate(
        self,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        edit: EditRevision,
    ) -> None:
        if (
            self.plan_key != plan.plan_key
            or self.verifier_actor_key != plan.verifier.actor_key
            or self.edit_id != edit.edit_id
        ):
            raise VerificationContractError("verification binding differs")
        if self.unit_key != unit.unit_key or self.unit_revision != unit.unit_revision:
            raise VerificationContractError("verification belongs to another unit")
        if not edit.changed and self.edit_impact is not EditImpactVerdict.EQUIVALENT:
            raise VerificationContractError("unchanged edit must be rated equivalent")
        serious = [item for item in self.findings if _requires_resolution(item.severity)]
        if self.verdict is VerificationVerdict.PASS:
            if serious or self.edit_impact is EditImpactVerdict.DEGRADES:
                raise VerificationContractError(
                    "PASS contradicts serious findings or a degrading edit"
                )
        elif not serious and self.edit_impact is not EditImpactVerdict.DEGRADES:
            raise VerificationContractError("BLOCK requires a serious finding")
        finding_ids = [item.finding_id for item in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise VerificationContractError("verification duplicates a finding")
        for finding in self.findings:
            if (
                finding.plan_key != self.plan_key
                or finding.unit_revision != self.unit_revision
                or finding.verifier_actor_key != self.verifier_actor_key
            ):
                raise VerificationContractError("verification finding binding differs")
            try:
                if finding.source_evidence is not None:
                    finding.source_evidence.validate(unit.source_text)
                if finding.target_evidence is not None:
                    finding.target_evidence.validate(
                        edit.rendered_target.target_text
                    )
            except ValueError as exc:
                raise VerificationContractError(str(exc)) from exc

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "verifier_actor_key": self.verifier_actor_key,
            "edit_id": self.edit_id,
            "verdict": self.verdict.value,
            "edit_impact": self.edit_impact.value,
            "findings": [item.as_payload() for item in self.findings],
            "summary": self.summary,
        }

    def as_payload(self) -> dict[str, object]:
        return {"report_id": self.report_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "VerificationReport":
        raw_findings = payload["findings"]
        if not isinstance(raw_findings, list) or any(
            not isinstance(item, dict) for item in raw_findings
        ):
            raise ValueError("verification report payload is malformed")
        return cls(
            report_id=str(payload["report_id"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            verifier_actor_key=str(payload["verifier_actor_key"]),
            edit_id=str(payload["edit_id"]),
            verdict=VerificationVerdict(str(payload["verdict"])),
            edit_impact=EditImpactVerdict(str(payload["edit_impact"])),
            findings=tuple(
                VerificationFinding.from_payload(item) for item in raw_findings
            ),
            summary=str(payload["summary"]),
        )


@dataclass(frozen=True, slots=True)
class UnitOutcome:
    outcome_id: str
    plan_key: str
    unit_key: str
    unit_revision: str
    edit_id: str
    verification_report_id: str
    rendered_target: RenderedTarget

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        unit: PreparedUnit,
        edit: EditRevision,
        verification: VerificationReport,
    ) -> "UnitOutcome":
        verification.validate(plan=plan, unit=unit, edit=edit)
        if verification.verdict is not VerificationVerdict.PASS:
            raise VerificationContractError("blocked unit cannot become an outcome")
        payload = {
            "plan_key": plan.plan_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "edit_id": edit.edit_id,
            "verification_report_id": verification.report_id,
            "rendered_target": edit.rendered_target.as_payload(),
        }
        return cls(
            outcome_id=digest(OUTCOME_NAMESPACE, payload),
            plan_key=plan.plan_key,
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            edit_id=edit.edit_id,
            verification_report_id=verification.report_id,
            rendered_target=edit.rendered_target,
        )

    def __post_init__(self) -> None:
        for name in (
            "outcome_id",
            "plan_key",
            "unit_key",
            "unit_revision",
            "edit_id",
            "verification_report_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.rendered_target.unit_key != self.unit_key:
            raise PlanBindingError("unit outcome target belongs to another unit")
        if self.outcome_id != digest(OUTCOME_NAMESPACE, self._identity_payload()):
            raise IdentityError("unit outcome id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "edit_id": self.edit_id,
            "verification_report_id": self.verification_report_id,
            "rendered_target": self.rendered_target.as_payload(),
        }

    def as_payload(self) -> dict[str, object]:
        return {"outcome_id": self.outcome_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "UnitOutcome":
        raw_target = payload["rendered_target"]
        if not isinstance(raw_target, dict):
            raise ValueError("unit outcome target payload is malformed")
        return cls(
            outcome_id=str(payload["outcome_id"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            edit_id=str(payload["edit_id"]),
            verification_report_id=str(payload["verification_report_id"]),
            rendered_target=RenderedTarget.from_payload(raw_target),
        )


@dataclass(frozen=True, slots=True)
class GlobalFinding:
    finding_id: str
    plan_key: str
    reviewer_actor_key: str
    category: FindingCategory
    severity: FindingSeverity
    unit_keys: tuple[str, ...]
    message: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        category: FindingCategory,
        severity: FindingSeverity,
        unit_keys: tuple[str, ...] | list[str],
        message: str,
    ) -> "GlobalFinding":
        order = {unit_key: index for index, (unit_key, _revision) in enumerate(plan.unit_revisions)}
        unique_unit_keys = set(unit_keys)
        if not unique_unit_keys or not unique_unit_keys.issubset(order):
            raise VerificationContractError("global finding references unknown units")
        unit_keys = tuple(sorted(unique_unit_keys, key=order.__getitem__))
        message = _nonempty("global finding", message)
        payload = {
            "plan_key": plan.plan_key,
            "reviewer_actor_key": plan.global_reviewer.actor_key,
            "category": category.value,
            "severity": severity.value,
            "unit_keys": list(unit_keys),
            "message": message,
        }
        return cls(
            finding_id=digest("pubtrans.global-finding/v1", payload),
            plan_key=plan.plan_key,
            reviewer_actor_key=plan.global_reviewer.actor_key,
            category=category,
            severity=severity,
            unit_keys=unit_keys,
            message=message,
        )

    def __post_init__(self) -> None:
        for name in ("finding_id", "plan_key", "reviewer_actor_key"):
            require_sha256(name, getattr(self, name))
        for unit_key in self.unit_keys:
            require_sha256("unit_key", unit_key)
        if not self.unit_keys:
            raise VerificationContractError("global finding requires affected units")
        if self.message != _nonempty("global finding", self.message):
            raise ValueError("global finding message is not canonical")
        if self.finding_id != digest(
            "pubtrans.global-finding/v1",
            self._identity_payload(),
        ):
            raise IdentityError("global finding id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "reviewer_actor_key": self.reviewer_actor_key,
            "category": self.category.value,
            "severity": self.severity.value,
            "unit_keys": list(self.unit_keys),
            "message": self.message,
        }

    def as_payload(self) -> dict[str, object]:
        return {"finding_id": self.finding_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "GlobalFinding":
        raw_units = payload["unit_keys"]
        if not isinstance(raw_units, list):
            raise ValueError("global finding units payload is malformed")
        return cls(
            finding_id=str(payload["finding_id"]),
            plan_key=str(payload["plan_key"]),
            reviewer_actor_key=str(payload["reviewer_actor_key"]),
            category=FindingCategory(str(payload["category"])),
            severity=FindingSeverity(str(payload["severity"])),
            unit_keys=tuple(str(item) for item in raw_units),
            message=str(payload["message"]),
        )


@dataclass(frozen=True, slots=True)
class GlobalReport:
    report_id: str
    plan_key: str
    reviewer_actor_key: str
    outcome_set_id: str
    verdict: VerificationVerdict
    findings: tuple[GlobalFinding, ...]
    summary: str

    @classmethod
    def create(
        cls,
        *,
        plan: KernelPlan,
        outcomes: tuple[UnitOutcome, ...] | list[UnitOutcome],
        verdict: VerificationVerdict,
        findings: tuple[GlobalFinding, ...] | list[GlobalFinding],
        summary: str,
    ) -> "GlobalReport":
        outcome_by_unit = {item.unit_key: item for item in outcomes}
        ordered_units = [item[0] for item in plan.unit_revisions]
        if len(outcome_by_unit) != len(outcomes) or set(outcome_by_unit) != set(
            ordered_units
        ):
            raise ReleaseContractError("global review requires the exact outcome set")
        outcomes = tuple(outcome_by_unit[item] for item in ordered_units)
        findings = tuple(sorted(findings, key=lambda item: item.finding_id))
        summary = _nonempty("global review summary", summary)
        outcome_set_id = digest(
            "pubtrans.outcome-set/v1",
            [item.as_payload() for item in outcomes],
        )
        payload = {
            "plan_key": plan.plan_key,
            "reviewer_actor_key": plan.global_reviewer.actor_key,
            "outcome_set_id": outcome_set_id,
            "verdict": verdict.value,
            "findings": [item.as_payload() for item in findings],
            "summary": summary,
        }
        result = cls(
            report_id=digest("pubtrans.global-report/v1", payload),
            plan_key=plan.plan_key,
            reviewer_actor_key=plan.global_reviewer.actor_key,
            outcome_set_id=outcome_set_id,
            verdict=verdict,
            findings=findings,
            summary=summary,
        )
        result.validate(plan=plan, outcomes=outcomes)
        return result

    def __post_init__(self) -> None:
        for name in (
            "report_id",
            "plan_key",
            "reviewer_actor_key",
            "outcome_set_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.summary != _nonempty("global review summary", self.summary):
            raise ValueError("global review summary is not canonical")
        if self.findings != tuple(sorted(self.findings, key=lambda item: item.finding_id)):
            raise ValueError("global findings are not canonical")
        if self.report_id != digest(
            "pubtrans.global-report/v1",
            self._identity_payload(),
        ):
            raise IdentityError("global report id mismatch")

    def validate(
        self,
        *,
        plan: KernelPlan,
        outcomes: tuple[UnitOutcome, ...],
    ) -> None:
        if (
            self.plan_key != plan.plan_key
            or self.reviewer_actor_key != plan.global_reviewer.actor_key
        ):
            raise VerificationContractError("global report binding differs")
        expected_set = digest(
            "pubtrans.outcome-set/v1",
            [item.as_payload() for item in outcomes],
        )
        if self.outcome_set_id != expected_set:
            raise VerificationContractError("global report outcome set differs")
        serious = [item for item in self.findings if _requires_resolution(item.severity)]
        if self.verdict is VerificationVerdict.PASS and serious:
            raise VerificationContractError("global PASS retains serious findings")
        if self.verdict is VerificationVerdict.BLOCK and not serious:
            raise VerificationContractError("global BLOCK requires a serious finding")
        known_units = {item.unit_key for item in outcomes}
        for finding in self.findings:
            if (
                finding.plan_key != self.plan_key
                or finding.reviewer_actor_key != self.reviewer_actor_key
                or not set(finding.unit_keys).issubset(known_units)
            ):
                raise VerificationContractError("global finding binding differs")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "reviewer_actor_key": self.reviewer_actor_key,
            "outcome_set_id": self.outcome_set_id,
            "verdict": self.verdict.value,
            "findings": [item.as_payload() for item in self.findings],
            "summary": self.summary,
        }

    def as_payload(self) -> dict[str, object]:
        return {"report_id": self.report_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "GlobalReport":
        raw_findings = payload["findings"]
        if not isinstance(raw_findings, list) or any(
            not isinstance(item, dict) for item in raw_findings
        ):
            raise ValueError("global report payload is malformed")
        return cls(
            report_id=str(payload["report_id"]),
            plan_key=str(payload["plan_key"]),
            reviewer_actor_key=str(payload["reviewer_actor_key"]),
            outcome_set_id=str(payload["outcome_set_id"]),
            verdict=VerificationVerdict(str(payload["verdict"])),
            findings=tuple(GlobalFinding.from_payload(item) for item in raw_findings),
            summary=str(payload["summary"]),
        )


@dataclass(frozen=True, slots=True)
class Release:
    release_id: str
    plan_key: str
    project_key: str
    snapshot_key: str
    manifest_sha256: str
    global_report_id: str
    outcomes: tuple[UnitOutcome, ...]
    approvals: tuple[ApprovalRevision, ...]

    @classmethod
    def create(
        cls,
        *,
        document: PreparedDocument,
        plan: KernelPlan,
        terminology: TerminologySnapshot,
        outcomes: tuple[UnitOutcome, ...] | list[UnitOutcome],
        global_report: GlobalReport,
    ) -> "Release":
        plan.validate_against(document=document, terminology=terminology)
        outcome_by_unit = {item.unit_key: item for item in outcomes}
        if len(outcome_by_unit) != len(outcomes):
            raise ReleaseContractError("release contains duplicate unit outcomes")
        ordered: list[UnitOutcome] = []
        approvals: list[ApprovalRevision] = []
        for unit in document.units:
            outcome = outcome_by_unit.get(unit.unit_key)
            if (
                outcome is None
                or outcome.plan_key != plan.plan_key
                or outcome.unit_revision != unit.unit_revision
            ):
                raise ReleaseContractError("release outcome set is incomplete or stale")
            outcome.rendered_target.validate(unit=unit, terminology=terminology)
            ordered.append(outcome)
            approvals.append(
                ApprovalRevision.create(
                    unit=unit,
                    target_text=outcome.rendered_target.target_text,
                    origin=f"pubtrans-m1:{plan.plan_key}:{outcome.outcome_id}",
                )
            )
        if set(outcome_by_unit) != {unit.unit_key for unit in document.units}:
            raise ReleaseContractError("release contains an outcome for another unit")
        ordered_tuple = tuple(ordered)
        approvals_tuple = validate_approval_set(document, approvals)
        global_report.validate(plan=plan, outcomes=ordered_tuple)
        if global_report.verdict is not VerificationVerdict.PASS:
            raise ReleaseContractError("blocked global review cannot be released")
        payload = {
            "plan_key": plan.plan_key,
            "project_key": document.project.project_key,
            "snapshot_key": document.snapshot.snapshot_key,
            "manifest_sha256": document.manifest_sha256,
            "global_report_id": global_report.report_id,
            "outcomes": [item.as_payload() for item in ordered_tuple],
            "approvals": [item.as_payload() for item in approvals_tuple],
        }
        return cls(
            release_id=digest(RELEASE_NAMESPACE, payload),
            plan_key=plan.plan_key,
            project_key=document.project.project_key,
            snapshot_key=document.snapshot.snapshot_key,
            manifest_sha256=document.manifest_sha256,
            global_report_id=global_report.report_id,
            outcomes=ordered_tuple,
            approvals=approvals_tuple,
        )

    def __post_init__(self) -> None:
        for name in (
            "release_id",
            "plan_key",
            "project_key",
            "snapshot_key",
            "manifest_sha256",
            "global_report_id",
        ):
            require_sha256(name, getattr(self, name))
        if self.release_id != digest(RELEASE_NAMESPACE, self._identity_payload()):
            raise IdentityError("release id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "project_key": self.project_key,
            "snapshot_key": self.snapshot_key,
            "manifest_sha256": self.manifest_sha256,
            "global_report_id": self.global_report_id,
            "outcomes": [item.as_payload() for item in self.outcomes],
            "approvals": [item.as_payload() for item in self.approvals],
        }

    def as_payload(self) -> dict[str, object]:
        return {"release_id": self.release_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "Release":
        raw_outcomes = payload["outcomes"]
        raw_approvals = payload["approvals"]
        if (
            not isinstance(raw_outcomes, list)
            or not isinstance(raw_approvals, list)
            or any(not isinstance(item, dict) for item in raw_outcomes)
            or any(not isinstance(item, dict) for item in raw_approvals)
        ):
            raise ValueError("release payload is malformed")
        return cls(
            release_id=str(payload["release_id"]),
            plan_key=str(payload["plan_key"]),
            project_key=str(payload["project_key"]),
            snapshot_key=str(payload["snapshot_key"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            global_report_id=str(payload["global_report_id"]),
            outcomes=tuple(UnitOutcome.from_payload(item) for item in raw_outcomes),
            approvals=tuple(ApprovalRevision.from_payload(item) for item in raw_approvals),
        )
