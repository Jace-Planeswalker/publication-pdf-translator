"""Strict structured-model adapters for every M1 semantic stage."""

from __future__ import annotations

import re

from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m1.errors import ServiceContractError
from pubtrans.m1.services import AdjudicationDraft
from pubtrans.m1.services import AdjudicationRequest
from pubtrans.m1.services import CandidateDraft
from pubtrans.m1.services import EditDraft
from pubtrans.m1.services import EditRequest
from pubtrans.m1.services import FindingDraft
from pubtrans.m1.services import GenerationRequest
from pubtrans.m1.services import GlobalFindingDraft
from pubtrans.m1.services import GlobalReviewDraft
from pubtrans.m1.services import GlobalReviewRequest
from pubtrans.m1.services import ResolutionDraft
from pubtrans.m1.services import ReviewDraft
from pubtrans.m1.services import ReviewRequest
from pubtrans.m1.services import ServiceBundle
from pubtrans.m1.services import SpanDraft
from pubtrans.m1.services import VerificationDraft
from pubtrans.m1.services import VerificationRequest
from pubtrans.m1.workflow import AdjudicationMode
from pubtrans.m1.workflow import EditImpactVerdict
from pubtrans.m1.workflow import FindingCategory
from pubtrans.m1.workflow import FindingSeverity
from pubtrans.m1.workflow import ResolutionAction
from pubtrans.m1.workflow import VerificationVerdict
from pubtrans.m1.terminology import TermTreatment
from pubtrans.m2.executor import ResilientExecutor
from pubtrans.m2.model import BudgetPolicy
from pubtrans.m2.model import CallDescriptor
from pubtrans.m2.model import CallEstimate
from pubtrans.m2.model import CallStage

from .marking import decode_marked_target
from .marking import marker_contract
from .marking import quote_span
from .openai import StructuredModelClient
from .prompts import ADJUDICATION_INSTRUCTIONS
from .prompts import EDIT_INSTRUCTIONS
from .prompts import GLOBAL_REVIEW_CHUNK_INSTRUCTIONS
from .prompts import GLOBAL_REVIEW_CHUNK_PROMPT_REVISION
from .prompts import GLOBAL_REVIEW_INSTRUCTIONS
from .prompts import GLOBAL_REVIEW_SYNTHESIS_INSTRUCTIONS
from .prompts import GLOBAL_REVIEW_SYNTHESIS_PROMPT_REVISION
from .prompts import REVIEW_INSTRUCTIONS
from .prompts import TRANSLATION_INSTRUCTIONS
from .prompts import VERIFICATION_INSTRUCTIONS


_CATEGORIES = [item.value for item in FindingCategory]
_SEVERITIES = [item.value for item in FindingSeverity]
_PLACEHOLDER_TOKEN_RE = re.compile(r"\[\[PT2-[^\]]+\]\]")
_URL_RE = re.compile(r"https?://\S+")
_LATIN_WORD_RE = re.compile(r"(?i)\b[A-Z][A-Z'-]{1,}\b")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _object(
    properties: dict[str, object],
    *,
    required: list[str] | None = None,
) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, object]) -> dict[str, object]:
    return {"type": "array", "items": items}


_FINDING_SCHEMA = _object(
    {
        "category": {"type": "string", "enum": _CATEGORIES},
        "severity": {"type": "string", "enum": _SEVERITIES},
        "message": {"type": "string", "minLength": 1},
        "option_key": {"type": ["string", "null"]},
        "source_quote": {"type": "string"},
        "source_start": {"type": ["integer", "null"]},
        "target_quote": {"type": "string"},
        "target_start": {"type": ["integer", "null"]},
    }
)

_GENERATION_SCHEMA = _object(
    {
        "marked_target_text": {"type": "string", "minLength": 1},
        "translator_note": {"type": "string"},
    }
)

_REVIEW_SCHEMA = _object(
    {
        "findings": _array(_FINDING_SCHEMA),
        "recommended_option_keys": _array({"type": "string"}),
        "summary": {"type": "string", "minLength": 1},
    }
)

_RESOLUTION_SCHEMA = _object(
    {
        "finding_id": {"type": "string"},
        "action": {
            "type": "string",
            "enum": [item.value for item in ResolutionAction],
        },
        "explanation": {"type": "string", "minLength": 1},
    }
)

_ADJUDICATION_SCHEMA = _object(
    {
        "mode": {
            "type": "string",
            "enum": [item.value for item in AdjudicationMode],
        },
        "selected_option_key": {"type": ["string", "null"]},
        "marked_target_text": {"type": "string"},
        "resolutions": _array(_RESOLUTION_SCHEMA),
        "rationale": {"type": "string", "minLength": 1},
    }
)

_EDIT_SCHEMA = _object(
    {
        "marked_target_text": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
    }
)

_VERIFICATION_SCHEMA = _object(
    {
        "verdict": {
            "type": "string",
            "enum": [item.value for item in VerificationVerdict],
        },
        "edit_impact": {
            "type": "string",
            "enum": [item.value for item in EditImpactVerdict],
        },
        "findings": _array(_FINDING_SCHEMA),
        "summary": {"type": "string", "minLength": 1},
    }
)

_GLOBAL_FINDING_SCHEMA = _object(
    {
        "category": {"type": "string", "enum": _CATEGORIES},
        "severity": {"type": "string", "enum": _SEVERITIES},
        "unit_keys": _array({"type": "string"}),
        "message": {"type": "string", "minLength": 1},
    }
)

_GLOBAL_SCHEMA = _object(
    {
        "verdict": {
            "type": "string",
            "enum": [item.value for item in VerificationVerdict],
        },
        "findings": _array(_GLOBAL_FINDING_SCHEMA),
        "summary": {"type": "string", "minLength": 1},
    }
)

_CONTINUITY_OBSERVATION_SCHEMA = _object(
    {
        "subject": {"type": "string", "minLength": 1},
        "rendering": {"type": "string", "minLength": 1},
        "unit_keys": _array({"type": "string"}),
        "note": {"type": "string", "minLength": 1},
    }
)

_GLOBAL_CHUNK_SCHEMA = _object(
    {
        "verdict": {
            "type": "string",
            "enum": [item.value for item in VerificationVerdict],
        },
        "findings": _array(_GLOBAL_FINDING_SCHEMA),
        "summary": {"type": "string", "minLength": 1},
        "continuity_observations": _array(_CONTINUITY_OBSERVATION_SCHEMA),
    }
)


class ModelQualityServices:
    """One typed adapter; actor identities keep the stages independent."""

    def __init__(self, client: StructuredModelClient) -> None:
        self.client = client

    @property
    def bundle(self) -> ServiceBundle:
        return ServiceBundle(self, self, self, self, self, self)

    def generate(self, request: GenerationRequest) -> CandidateDraft:
        payload = request.as_payload()
        payload["term_marker_contract"] = marker_contract(request.stage.terminology)
        response = self.client.structured(
            actor=request.actor,
            instructions=TRANSLATION_INSTRUCTIONS,
            input_payload=payload,
            schema_name="publication_translation",
            schema=_GENERATION_SCHEMA,
        )
        return CandidateDraft(
            rendered_target=decode_marked_target(
                _string(response, "marked_target_text"),
                request.stage.terminology,
            ),
            translator_note=_string(response, "translator_note", empty=True),
        )

    def review(self, request: ReviewRequest) -> ReviewDraft:
        response = self.client.structured(
            actor=request.actor,
            instructions=REVIEW_INSTRUCTIONS,
            input_payload=request.as_payload(),
            schema_name="publication_blind_review",
            schema=_REVIEW_SCHEMA,
        )
        option_text = {
            item.option_key: item.rendered_target.target_text for item in request.options
        }
        findings = tuple(
            _finding(
                item,
                source_text=request.stage.context.current.source_text,
                target_by_option=option_text,
                require_option=True,
            )
            for item in _dict_list(response, "findings")
        )
        return ReviewDraft(
            findings=findings,
            recommended_option_keys=tuple(
                _string_item(item, "recommended option key")
                for item in _list(response, "recommended_option_keys")
            ),
            summary=_string(response, "summary"),
        )

    def adjudicate(self, request: AdjudicationRequest) -> AdjudicationDraft:
        payload = request.as_payload()
        payload["term_marker_contract"] = marker_contract(request.stage.terminology)
        response = self.client.structured(
            actor=request.actor,
            instructions=ADJUDICATION_INSTRUCTIONS,
            input_payload=payload,
            schema_name="publication_adjudication",
            schema=_ADJUDICATION_SCHEMA,
        )
        mode = _enum(AdjudicationMode, response, "mode")
        selected_value = response.get("selected_option_key")
        selected = str(selected_value) if selected_value is not None else None
        if mode is AdjudicationMode.SELECT:
            option = next(
                (item for item in request.options if item.option_key == selected),
                None,
            )
            if option is None:
                raise ServiceContractError("adjudicator selected an unknown option")
            rendered = _draft_from_rendered(option.rendered_target)
        else:
            if selected is not None:
                raise ServiceContractError("synthesis cannot select an option")
            rendered = decode_marked_target(
                _string(response, "marked_target_text"),
                request.stage.terminology,
            )
        resolutions = tuple(
            ResolutionDraft(
                finding_id=_string(item, "finding_id"),
                action=_enum(ResolutionAction, item, "action"),
                explanation=_string(item, "explanation"),
            )
            for item in _dict_list(response, "resolutions")
        )
        return AdjudicationDraft(
            mode=mode,
            selected_option_key=selected,
            rendered_target=rendered,
            resolutions=resolutions,
            rationale=_string(response, "rationale"),
        )

    def edit(self, request: EditRequest) -> EditDraft:
        payload = request.as_payload()
        payload["term_marker_contract"] = marker_contract(request.stage.terminology)
        response = self.client.structured(
            actor=request.actor,
            instructions=EDIT_INSTRUCTIONS,
            input_payload=payload,
            schema_name="publication_chinese_edit",
            schema=_EDIT_SCHEMA,
        )
        return EditDraft(
            rendered_target=decode_marked_target(
                _string(response, "marked_target_text"),
                request.stage.terminology,
            ),
            summary=_string(response, "summary"),
        )

    def verify(self, request: VerificationRequest) -> VerificationDraft:
        response = self.client.structured(
            actor=request.actor,
            instructions=VERIFICATION_INSTRUCTIONS,
            input_payload=request.as_payload(),
            schema_name="publication_final_verification",
            schema=_VERIFICATION_SCHEMA,
        )
        edit = request.edit_payload.get("rendered_target")
        if not isinstance(edit, dict):
            raise ServiceContractError("verification edit target is malformed")
        target_text = _string(edit, "target_text")
        findings = list(
            _finding(
                item,
                source_text=request.stage.context.current.source_text,
                target_by_option={"final": target_text},
                require_option=False,
            )
            for item in _dict_list(response, "findings")
        )
        untranslated = _untranslated_finding(request, target_text)
        if untranslated is not None:
            findings.append(untranslated)
        verdict = _enum(VerificationVerdict, response, "verdict")
        if untranslated is not None:
            verdict = VerificationVerdict.BLOCK
        return VerificationDraft(
            verdict=verdict,
            edit_impact=_enum(EditImpactVerdict, response, "edit_impact"),
            findings=tuple(findings),
            summary=(
                _string(response, "summary")
                if untranslated is None
                else _string(response, "summary")
                + " Deterministic untranslated-text gate blocked release."
            ),
        )

    def review_document(self, request: GlobalReviewRequest) -> GlobalReviewDraft:
        response = self.client.structured(
            actor=request.actor,
            instructions=GLOBAL_REVIEW_INSTRUCTIONS,
            input_payload=request.as_payload(),
            schema_name="publication_global_review",
            schema=_GLOBAL_SCHEMA,
        )
        return _global_draft(response)


class HierarchicalGlobalReviewService:
    """Bound whole-book review calls while preserving crash-safe paid-call reuse."""

    def __init__(
        self,
        *,
        client: StructuredModelClient,
        executor: ResilientExecutor,
        budget: BudgetPolicy,
        max_chunk_characters: int,
    ) -> None:
        if max_chunk_characters < 100:
            raise ValueError("global review chunk limit is too small")
        self.client = client
        self.executor = executor
        self.budget = budget
        self.max_chunk_characters = max_chunk_characters

    def review_document(self, request: GlobalReviewRequest) -> GlobalReviewDraft:
        if request.plan_key != self.budget.scope_key:
            raise ServiceContractError("global review budget belongs to another plan")
        chunks = _global_payload_chunks(
            request.unit_payloads,
            self.max_chunk_characters,
        )
        raw_reports: list[dict[str, object]] = []
        drafts: list[GlobalReviewDraft] = []
        for index, chunk in enumerate(chunks):
            payload = {
                "plan_key": request.plan_key,
                "source_language": request.source_language,
                "target_language": request.target_language,
                "source_brief": request.source_brief,
                "chunk_index": index,
                "chunk_count": len(chunks),
                "unit_payloads": list(chunk),
            }
            response = self.executor.execute(
                descriptor=CallDescriptor.create(
                    stage=CallStage.GLOBAL_REVIEW_CHUNK,
                    dependency_payload={
                        "actor": request.actor.as_payload(),
                        "prompt_revision": GLOBAL_REVIEW_CHUNK_PROMPT_REVISION,
                        "input": payload,
                    },
                    slot_hint=f"whole-document-chunk:{index}",
                ),
                budget=self.budget,
                estimate=CallEstimate(30_000, 0),
                operation=lambda value=payload: self.client.structured(
                    actor=request.actor,
                    instructions=GLOBAL_REVIEW_CHUNK_INSTRUCTIONS,
                    input_payload=value,
                    schema_name="publication_global_review_chunk",
                    schema=_GLOBAL_CHUNK_SCHEMA,
                ),
                encode=lambda value: value,
                decode=_model_object,
            )
            _continuity_observations(response, _unit_keys(chunk))
            raw_reports.append(response)
            drafts.append(_global_draft(response))

        if len(drafts) == 1:
            return drafts[0]
        if any(item.verdict is VerificationVerdict.BLOCK for item in drafts):
            return _merge_global_drafts(
                drafts,
                summary="One or more bounded whole-document reviews blocked release.",
            )

        synthesis_payload = {
            "plan_key": request.plan_key,
            "source_language": request.source_language,
            "target_language": request.target_language,
            "source_brief": request.source_brief,
            "chunk_reports": raw_reports,
        }
        response = self.executor.execute(
            descriptor=CallDescriptor.create(
                stage=CallStage.GLOBAL_REVIEW_SYNTHESIS,
                dependency_payload={
                    "actor": request.actor.as_payload(),
                    "prompt_revision": GLOBAL_REVIEW_SYNTHESIS_PROMPT_REVISION,
                    "input": synthesis_payload,
                },
                slot_hint="whole-document-synthesis",
            ),
            budget=self.budget,
            estimate=CallEstimate(30_000, 0),
            operation=lambda: self.client.structured(
                actor=request.actor,
                instructions=GLOBAL_REVIEW_SYNTHESIS_INSTRUCTIONS,
                input_payload=synthesis_payload,
                schema_name="publication_global_review_synthesis",
                schema=_GLOBAL_SCHEMA,
            ),
            encode=lambda value: value,
            decode=_model_object,
        )
        synthesis = _global_draft(response)
        return _merge_global_drafts(
            [*drafts, synthesis],
            summary=synthesis.summary,
        )


def _global_payload_chunks(
    payloads: tuple[dict[str, object], ...],
    maximum_characters: int,
) -> tuple[tuple[dict[str, object], ...], ...]:
    if not payloads:
        raise ServiceContractError("whole-document review has no units")
    chunks: list[tuple[dict[str, object], ...]] = []
    current: list[dict[str, object]] = []
    size = 0
    for payload in payloads:
        item_size = len(canonical_json(payload))
        if item_size > maximum_characters:
            raise ServiceContractError(
                "one unit exceeds the configured global review chunk limit"
            )
        if current and size + item_size > maximum_characters:
            chunks.append(tuple(current))
            current = []
            size = 0
        current.append(payload)
        size += item_size
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _unit_keys(payloads: tuple[dict[str, object], ...]) -> set[str]:
    result: set[str] = set()
    for payload in payloads:
        stage = payload.get("stage")
        if not isinstance(stage, dict):
            raise ServiceContractError("global review unit stage is malformed")
        context = stage.get("context")
        if not isinstance(context, dict):
            raise ServiceContractError("global review unit context is malformed")
        key = context.get("unit_key")
        if not isinstance(key, str) or not key:
            raise ServiceContractError("global review unit key is malformed")
        result.add(key)
    return result


def _continuity_observations(
    response: dict[str, object],
    known_unit_keys: set[str],
) -> tuple[dict[str, object], ...]:
    result = []
    for item in _dict_list(response, "continuity_observations"):
        keys = tuple(
            _string_item(value, "continuity observation unit key")
            for value in _list(item, "unit_keys")
        )
        if not keys or not set(keys).issubset(known_unit_keys):
            raise ServiceContractError(
                "continuity observation cites an unknown unit"
            )
        result.append(
            {
                "subject": _string(item, "subject"),
                "rendering": _string(item, "rendering"),
                "unit_keys": list(keys),
                "note": _string(item, "note"),
            }
        )
    return tuple(result)


def _global_draft(response: dict[str, object]) -> GlobalReviewDraft:
    findings = tuple(
        GlobalFindingDraft(
            category=_enum(FindingCategory, item, "category"),
            severity=_enum(FindingSeverity, item, "severity"),
            unit_keys=tuple(
                _string_item(key, "global finding unit key")
                for key in _list(item, "unit_keys")
            ),
            message=_string(item, "message"),
        )
        for item in _dict_list(response, "findings")
    )
    return GlobalReviewDraft(
        verdict=_enum(VerificationVerdict, response, "verdict"),
        findings=findings,
        summary=_string(response, "summary"),
    )


def _merge_global_drafts(
    drafts: list[GlobalReviewDraft],
    *,
    summary: str,
) -> GlobalReviewDraft:
    unique: dict[
        tuple[FindingCategory, FindingSeverity, tuple[str, ...], str],
        GlobalFindingDraft,
    ] = {}
    for draft in drafts:
        for finding in draft.findings:
            key = (
                finding.category,
                finding.severity,
                finding.unit_keys,
                finding.message,
            )
            unique[key] = finding
    verdict = (
        VerificationVerdict.BLOCK
        if any(item.verdict is VerificationVerdict.BLOCK for item in drafts)
        else VerificationVerdict.PASS
    )
    return GlobalReviewDraft(
        verdict=verdict,
        findings=tuple(unique[key] for key in sorted(unique, key=str)),
        summary=summary,
    )


def _model_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ServiceContractError("cached model response is not an object")
    return value


def _untranslated_finding(
    request: VerificationRequest,
    target_text: str,
) -> FindingDraft | None:
    if not request.stage.target_language.lower().startswith("zh"):
        return None
    source_text = request.stage.context.current.source_text
    source_residual = source_text
    target_residual = target_text
    for guidance in request.stage.terminology:
        source_term = guidance.term.decision.source_term
        source_residual = source_residual.replace(source_term, " ")
        target_residual = target_residual.replace(
            guidance.directive.required_rendering,
            " ",
        )
        if guidance.term.treatment is TermTreatment.RETAIN_SOURCE:
            target_residual = target_residual.replace(source_term, " ")
    source_residual = _URL_RE.sub(" ", _PLACEHOLDER_TOKEN_RE.sub(" ", source_residual))
    target_residual = _URL_RE.sub(" ", _PLACEHOLDER_TOKEN_RE.sub(" ", target_residual))
    words = _LATIN_WORD_RE.findall(source_residual)
    if not words:
        return None
    normalized_source = " ".join(normalize_text(source_residual).split()).casefold()
    normalized_target = " ".join(normalize_text(target_residual).split()).casefold()
    exact = normalized_source == normalized_target
    retained = sum(
        1
        for word in words
        if re.search(rf"(?i)\b{re.escape(word)}\b", target_residual)
    )
    ratio = retained / len(words)
    cjk_count = len(_CJK_RE.findall(target_residual))
    mostly_source = (
        len(words) >= 2
        and ratio >= 0.8
        and cjk_count < max(4, len(words) * 2)
    ) or (len(words) == 1 and retained == 1 and cjk_count == 0)
    if not exact and not mostly_source:
        return None
    return FindingDraft(
        category=FindingCategory.UNTRANSLATED,
        severity=FindingSeverity.BLOCKING,
        message=(
            "The target retains nearly all translatable source-language text "
            "outside governed terms and protected material."
        ),
        source_evidence=SpanDraft(0, len(source_text)),
        target_evidence=SpanDraft(0, len(target_text)),
    )


def _draft_from_rendered(rendered):
    from pubtrans.m1.services import ApplicationDraft
    from pubtrans.m1.services import RenderedTargetDraft

    return RenderedTargetDraft(
        target_text=rendered.target_text,
        term_applications=tuple(
            ApplicationDraft(
                occurrence_key=item.occurrence_key,
                target_start=item.target_start,
                target_end=item.target_end,
            )
            for item in rendered.term_applications
        ),
    )


def _finding(
    payload: dict[str, object],
    *,
    source_text: str,
    target_by_option: dict[str, str],
    require_option: bool,
) -> FindingDraft:
    raw_option = payload.get("option_key")
    option = str(raw_option) if raw_option is not None else None
    if require_option and option not in target_by_option:
        raise ServiceContractError("review finding cites an unknown option")
    target_text = target_by_option.get(option or "final", "")
    return FindingDraft(
        category=_enum(FindingCategory, payload, "category"),
        severity=_enum(FindingSeverity, payload, "severity"),
        message=_string(payload, "message"),
        option_key=option if require_option else None,
        source_evidence=quote_span(
            source_text,
            _string(payload, "source_quote", empty=True),
            payload.get("source_start"),
        ),
        target_evidence=quote_span(
            target_text,
            _string(payload, "target_quote", empty=True),
            payload.get("target_start"),
        ),
    )


def _enum(enum_type, payload: dict[str, object], key: str):
    try:
        return enum_type(_string(payload, key))
    except ValueError as exc:
        raise ServiceContractError(f"model returned an invalid {key}") from exc


def _string(
    payload: dict[str, object],
    key: str,
    *,
    empty: bool = False,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ServiceContractError(f"model response {key} is not a valid string")
    return value


def _string_item(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ServiceContractError(f"model response {label} is invalid")
    return value


def _list(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ServiceContractError(f"model response {key} is not a list")
    return value


def _dict_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    result = _list(payload, key)
    if any(not isinstance(item, dict) for item in result):
        raise ServiceContractError(f"model response {key} contains a non-object")
    return [item for item in result if isinstance(item, dict)]
