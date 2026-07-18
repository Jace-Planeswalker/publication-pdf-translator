"""Chunked document analysis and evidence-governed terminology planning."""

from __future__ import annotations

import re
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedUnit
from pubtrans.m0v2.canonical import digest
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import ContextPolicy
from pubtrans.m1.plan import KernelPlan
from pubtrans.m1.plan import LaneSpec
from pubtrans.m1.plan import RiskLevel
from pubtrans.m1.plan import SourceBrief
from pubtrans.m1.plan import UnitRoute
from pubtrans.m1.terminology import Conventionality
from pubtrans.m1.terminology import DecisionConfidence
from pubtrans.m1.terminology import EvidenceStance
from pubtrans.m1.terminology import EvidenceTier
from pubtrans.m1.terminology import SemanticFit
from pubtrans.m1.terminology import TargetTermCandidate
from pubtrans.m1.terminology import TermDecision
from pubtrans.m1.terminology import TermEvidence
from pubtrans.m1.terminology import TermOccurrence
from pubtrans.m1.terminology import TermRevision
from pubtrans.m1.terminology import TermTreatment
from pubtrans.m1.terminology import TerminologySnapshot
from pubtrans.m2.executor import ResilientExecutor
from pubtrans.m2.executor import RetryPolicy
from pubtrans.m2.model import BudgetPolicy
from pubtrans.m2.model import CallDescriptor
from pubtrans.m2.model import CallEstimate
from pubtrans.m2.model import CallStage
from pubtrans.m2.store import RecoveryStore
from pubtrans.planning import PlannedTranslation

from .config import ProductConfig
from .errors import TerminologyPlanningError
from .evidence import EvidenceCatalog
from .evidence import EvidenceMaterial
from .evidence import PageFetcher
from .evidence import SafeHTTPPageFetcher
from .evidence import harvest_citations
from .openai import ResearchModelClient
from .openai import StructuredModelClient
from .openai import WebResearchResult
from .prompts import ADJUDICATION_PROMPT_REVISION
from .prompts import EDIT_PROMPT_REVISION
from .prompts import GLOBAL_REVIEW_PROMPT_REVISION
from .prompts import REVIEW_PROMPT_REVISION
from .prompts import TRANSLATION_PROMPT_REVISION
from .prompts import VERIFICATION_PROMPT_REVISION


ANALYSIS_PROMPT_REVISION = "pubtrans-document-analysis-v1"
TERM_RESEARCH_PROMPT_REVISION = "pubtrans-term-research-v1"
TERM_REVIEW_PROMPT_REVISION = "pubtrans-term-independent-review-v1"
PLANNER_REVISION = "publication-planner-v1"
_PROTECTED_TOKEN_RE = re.compile(r"\[\[PT2-[^\]]+\]\]")
_ENGLISH_SEMANTIC_RISK_RE = re.compile(
    r"(?i)\b(?:not|no|never|neither|unless|except|only|must|shall|should|"
    r"may|might|can|cannot|could|would|at\s+least|at\s+most|more\s+than|"
    r"less\s+than|respectively)\b"
)


ANALYSIS_INSTRUCTIONS = """
Analyze only the supplied source units for publication translation into
Simplified Chinese. Produce a concise source-language document brief. Identify
only terminology or proper-name concepts whose rendering materially affects
accuracy or consistency; source_term must be an exact case-sensitive substring
of at least one supplied unit, and sense_id must distinguish the meaning in
this document. Offer established Chinese candidate forms when known, but do
not claim that memory is evidence. Flag each unit R1, R2 or R3: use R3 for
ambiguity, negation/modality, dense logic, formulas, high-impact terms or
meaningful proper-name uncertainty. Return only the strict schema.
""".strip()


SYNTHESIS_INSTRUCTIONS = """
Consolidate chunk analyses into one source-language document brief, one
deduplicated list of sense-specific high-impact terms, and one risk record per
known unit. Preserve exact source_term spelling and unit keys from the input.
Do not invent evidence or add a term absent from all chunks. Prefer mainstream
candidate forms but retain competing candidates for later evidence review.
Return only the strict schema.
""".strip()


TERM_RESEARCH_INSTRUCTIONS = """
Research the supplied source concept and competing Chinese forms on the live
web. Search Chinese authoritative terminology databases first (including
Termonline or CNTERM when relevant), then official/domain-primary Chinese
sources, parallel publications and real corpus usage. Distinguish the exact
sense and domain; actively seek evidence that a candidate is obscure,
nonstandard or belongs to another sense. Cite every factual claim with the
provider's URL citations. This memo is discovery material only: do not call a
form verified merely because it appears in a search result.
""".strip()


TERM_REVIEW_INSTRUCTIONS = """
Act as an independent terminology reviewer. Assess only the supplied, actually
captured source excerpts; model memory and the discovery memo are not evidence.
For every candidate and every evidence source, classify support or
contradiction and whether the excerpt matches this exact sense and domain.
Judge conventionality as ESTABLISHED, ATTESTED, RARE or UNATTESTED. Select the
most accurate mainstream confirmed form. Selecting a less conventional form
requires an explicit accuracy reason. If no candidate has qualified support,
select null so the runtime retains the source expression. Return only the
strict schema.
""".strip()


def _object(properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items: dict[str, object]) -> dict[str, object]:
    return {"type": "array", "items": items}


_CONCEPT_SCHEMA = _object(
    {
        "source_term": {"type": "string", "minLength": 1},
        "sense_id": {"type": "string", "minLength": 1},
        "concept_definition": {"type": "string", "minLength": 1},
        "domain": {"type": "string", "minLength": 1},
        "candidate_forms": _array({"type": "string", "minLength": 1}),
        "rationale": {"type": "string", "minLength": 1},
    }
)

_RISK_SCHEMA = _object(
    {
        "unit_key": {"type": "string"},
        "risk_level": {
            "type": "string",
            "enum": [item.value for item in RiskLevel],
        },
        "reasons": _array({"type": "string", "minLength": 1}),
    }
)

_ANALYSIS_SCHEMA = _object(
    {
        "brief": {"type": "string", "minLength": 1},
        "concepts": _array(_CONCEPT_SCHEMA),
        "risks": _array(_RISK_SCHEMA),
    }
)

_ASSESSMENT_SCHEMA = _object(
    {
        "source_key": {"type": "string"},
        "stance": {
            "type": "string",
            "enum": [item.value for item in EvidenceStance],
        },
        "sense_match": {"type": "boolean"},
        "domain_match": {"type": "boolean"},
    }
)

_CANDIDATE_REVIEW_SCHEMA = _object(
    {
        "target_form": {"type": "string"},
        "semantic_fit": {
            "type": "string",
            "enum": [item.value for item in SemanticFit],
        },
        "conventionality": {
            "type": "string",
            "enum": [item.value for item in Conventionality],
        },
        "rationale": {"type": "string", "minLength": 1},
        "assessments": _array(_ASSESSMENT_SCHEMA),
    }
)

_TERM_REVIEW_SCHEMA = _object(
    {
        "candidates": _array(_CANDIDATE_REVIEW_SCHEMA),
        "selected_target_form": {"type": ["string", "null"]},
        "rationale": {"type": "string", "minLength": 1},
        "mainstream_override_reason": {"type": "string"},
    }
)


@dataclass(frozen=True, slots=True)
class ConceptSpec:
    source_term: str
    sense_id: str
    concept_definition: str
    domain: str
    candidate_forms: tuple[str, ...]
    rationale: str

    def as_payload(self) -> dict[str, object]:
        return {
            "source_term": self.source_term,
            "sense_id": self.sense_id,
            "concept_definition": self.concept_definition,
            "domain": self.domain,
            "candidate_forms": list(self.candidate_forms),
            "rationale": self.rationale,
        }


class ProductionPlanner:
    """Create a replayable M1 plan; unsupported terminology stays in source."""

    def __init__(
        self,
        *,
        config: ProductConfig,
        structured_client: StructuredModelClient,
        research_client: ResearchModelClient | None = None,
        evidence_catalog: EvidenceCatalog | None = None,
        page_fetcher: PageFetcher | None = None,
        analysis_chunk_characters: int = 40_000,
    ) -> None:
        if analysis_chunk_characters < 1_000:
            raise ValueError("analysis chunks must allow at least 1000 characters")
        self.config = config
        self.structured_client = structured_client
        self.research_client = research_client
        self.evidence_catalog = evidence_catalog or EvidenceCatalog()
        self.page_fetcher = page_fetcher or SafeHTTPPageFetcher()
        self.analysis_chunk_characters = analysis_chunk_characters

    def plan(
        self,
        document: PreparedDocument,
        store: RecoveryStore,
    ) -> PlannedTranslation:
        executor = ResilientExecutor(
            store,
            owner_id=f"planner-{document.project.project_key[:16]}",
            retry_policy=RetryPolicy(),
            lease_ttl_seconds=max(300.0, self.config.request_timeout_seconds + 60),
        )
        budget = BudgetPolicy.create(
            scope_key=document.snapshot.snapshot_key,
            max_attempted_calls=self.config.max_planning_calls,
            max_estimated_tokens=self.config.max_estimated_tokens,
            max_estimated_microusd=self.config.max_estimated_microusd,
        )
        analysis = self._analyze(document, executor, budget)
        terminology = self._terminology(document, analysis, executor, budget)
        plan = self._kernel_plan(document, terminology, analysis)
        return PlannedTranslation(terminology=terminology, plan=plan)

    def _analyze(
        self,
        document: PreparedDocument,
        executor: ResilientExecutor,
        budget: BudgetPolicy,
    ) -> dict[str, object]:
        actor = self.config.actor(
            ActorRole.DOCUMENT_ANALYST,
            prompt_revision=ANALYSIS_PROMPT_REVISION,
        )
        chunks = _unit_chunks(document.units, self.analysis_chunk_characters)
        results: list[dict[str, object]] = []
        for index, units in enumerate(chunks):
            input_payload = {
                "source_language": document.project.source_language,
                "target_language": document.project.target_language,
                "chunk_index": index,
                "units": [_analysis_unit(item) for item in units],
            }
            descriptor = CallDescriptor.create(
                stage=CallStage.DOCUMENT_ANALYSIS,
                dependency_payload={
                    "actor": actor.as_payload(),
                    "planner_revision": PLANNER_REVISION,
                    "input": input_payload,
                },
                slot_hint=f"analysis-chunk-{index}",
            )
            results.append(
                executor.execute(
                    descriptor=descriptor,
                    budget=budget,
                    estimate=CallEstimate(30_000, 0),
                    operation=lambda payload=input_payload: self.structured_client.structured(
                        actor=actor,
                        instructions=ANALYSIS_INSTRUCTIONS,
                        input_payload=payload,
                        schema_name="publication_source_analysis",
                        schema=_ANALYSIS_SCHEMA,
                    ),
                    encode=lambda value: value,
                    decode=_dict_value,
                )
            )
        if len(results) == 1:
            return _validated_analysis(results[0])
        synthesis_payload = {
            "known_unit_keys": [item.unit_key for item in document.units],
            "chunk_analyses": results,
        }
        descriptor = CallDescriptor.create(
            stage=CallStage.DOCUMENT_SYNTHESIS,
            dependency_payload={
                "actor": actor.as_payload(),
                "planner_revision": PLANNER_REVISION,
                "input": synthesis_payload,
            },
            slot_hint="document-synthesis",
        )
        result = executor.execute(
            descriptor=descriptor,
            budget=budget,
            estimate=CallEstimate(40_000, 0),
            operation=lambda: self.structured_client.structured(
                actor=actor,
                instructions=SYNTHESIS_INSTRUCTIONS,
                input_payload=synthesis_payload,
                schema_name="publication_source_synthesis",
                schema=_ANALYSIS_SCHEMA,
            ),
            encode=lambda value: value,
            decode=_dict_value,
        )
        return _validated_analysis(result)

    def _terminology(
        self,
        document: PreparedDocument,
        analysis: dict[str, object],
        executor: ResilientExecutor,
        budget: BudgetPolicy,
    ) -> TerminologySnapshot:
        concepts = _concepts(analysis, document)
        occupied: dict[str, list[tuple[int, int]]] = defaultdict(list)
        terms: list[TermRevision] = []
        for concept in sorted(
            concepts,
            key=lambda item: (-len(item.source_term), item.source_term, item.sense_id),
        ):
            occurrences = _occurrences(
                document=document,
                concept=concept,
                occupied=occupied,
            )
            if not occurrences:
                continue
            decision = self._term_decision(
                document=document,
                concept=concept,
                executor=executor,
                budget=budget,
            )
            treatment = {
                DecisionConfidence.VERIFIED: TermTreatment.TRANSLATE_ONLY,
                DecisionConfidence.SUPPORTED: TermTreatment.TRANSLATE_WITH_SOURCE_FIRST,
                DecisionConfidence.PROVISIONAL: TermTreatment.TRANSLATE_WITH_SOURCE_FIRST,
                DecisionConfidence.RETAINED_UNRESOLVED: TermTreatment.RETAIN_SOURCE,
            }[decision.confidence]
            terms.append(
                TermRevision.create(
                    decision=decision,
                    treatment=treatment,
                    rationale=(
                        "Rendering follows independently reviewed evidence; uncertain "
                        "terms expose or retain the source form."
                    ),
                    occurrences=occurrences,
                )
            )
            for occurrence in occurrences:
                occupied[occurrence.unit_key].append(
                    (occurrence.source_start, occurrence.source_end)
                )
        return TerminologySnapshot.create(document, terms)

    def _term_decision(
        self,
        *,
        document: PreparedDocument,
        concept: ConceptSpec,
        executor: ResilientExecutor,
        budget: BudgetPolicy,
    ) -> TermDecision:
        researcher = self.config.actor(
            ActorRole.TERMINOLOGY_RESEARCHER,
            prompt_revision=TERM_RESEARCH_PROMPT_REVISION,
        )
        reviewer = self.config.actor(
            ActorRole.BILINGUAL_REVIEWER,
            prompt_revision=TERM_REVIEW_PROMPT_REVISION,
            variant="terminology-independent-review",
        )
        materials = list(
            self.evidence_catalog.for_concept(
                source_term=concept.source_term,
                sense_id=concept.sense_id,
            )
        )
        if (
            self.config.enable_web_research
            and self.research_client is not None
            and concept.candidate_forms
        ):
            research_payload = {
                "concept": concept.as_payload(),
                "source_language": document.project.source_language,
                "target_language": document.project.target_language,
            }
            descriptor = CallDescriptor.create(
                stage=CallStage.TERMINOLOGY_RESEARCH,
                dependency_payload={
                    "actor": researcher.as_payload(),
                    "planner_revision": PLANNER_REVISION,
                    "input": research_payload,
                },
                slot_hint=f"term-research:{concept.source_term}",
            )
            memo = executor.execute(
                descriptor=descriptor,
                budget=budget,
                estimate=CallEstimate(30_000, 0),
                operation=lambda: self.research_client.research(
                    actor=researcher,
                    instructions=TERM_RESEARCH_INSTRUCTIONS,
                    input_payload=research_payload,
                ),
                encode=lambda value: value.as_payload(),
                decode=lambda value: WebResearchResult.from_payload(
                    _dict_value(value)
                ),
            )
            harvest_descriptor = CallDescriptor.create(
                stage=CallStage.EVIDENCE_HARVEST,
                dependency_payload={
                    "planner_revision": PLANNER_REVISION,
                    "source_term": concept.source_term,
                    "sense_id": concept.sense_id,
                    "target_forms": list(concept.candidate_forms),
                    "citations": [
                        {"url": item.url, "title": item.title}
                        for item in memo.citations
                    ],
                    "fetcher": "safe-http-html-v1",
                },
                slot_hint=f"evidence-harvest:{concept.source_term}",
            )
            harvested = executor.execute(
                descriptor=harvest_descriptor,
                budget=budget,
                estimate=CallEstimate(0, 0),
                operation=lambda: harvest_citations(
                    source_term=concept.source_term,
                    sense_id=concept.sense_id,
                    target_forms=concept.candidate_forms,
                    citations=memo.citations,
                    retrieved_on=date.today().isoformat(),
                    fetcher=self.page_fetcher,
                ),
                encode=lambda values: [item.as_payload() for item in values],
                decode=_materials_value,
            )
            materials.extend(harvested)

        review_payload = {
            "concept": concept.as_payload(),
            "source_retention_fallback": concept.source_term,
            "evidence": [item.as_payload() for item in materials],
        }
        descriptor = CallDescriptor.create(
            stage=CallStage.TERMINOLOGY_REVIEW,
            dependency_payload={
                "actor": reviewer.as_payload(),
                "planner_revision": PLANNER_REVISION,
                "input": review_payload,
            },
            slot_hint=f"term-review:{concept.source_term}",
        )
        reviewed = executor.execute(
            descriptor=descriptor,
            budget=budget,
            estimate=CallEstimate(30_000, 0),
            operation=lambda: self.structured_client.structured(
                actor=reviewer,
                instructions=TERM_REVIEW_INSTRUCTIONS,
                input_payload=review_payload,
                schema_name="publication_terminology_review",
                schema=_TERM_REVIEW_SCHEMA,
            ),
            encode=lambda value: value,
            decode=_dict_value,
        )
        return _decision_from_review(
            document=document,
            concept=concept,
            materials=tuple(materials),
            reviewed=reviewed,
            researcher_key=researcher.actor_key,
            reviewer_key=reviewer.actor_key,
        )

    def _kernel_plan(
        self,
        document: PreparedDocument,
        terminology: TerminologySnapshot,
        analysis: dict[str, object],
    ) -> KernelPlan:
        baseline = LaneSpec.create(
            label="sense-faithful-baseline",
            actor=self.config.actor(
                ActorRole.TRANSLATOR,
                prompt_revision=TRANSLATION_PROMPT_REVISION,
                variant="sense-faithful",
            ),
        )
        alternative = LaneSpec.create(
            label="literal-logic-independent",
            actor=self.config.actor(
                ActorRole.TRANSLATOR,
                prompt_revision=TRANSLATION_PROMPT_REVISION,
                variant="literal-logic-independent",
            ),
        )
        risk_by_unit = _risk_map(analysis, document)
        directives_by_unit = {
            unit.unit_key: terminology.directives_for_unit(unit.unit_key)
            for unit in document.units
        }
        routes: list[UnitRoute] = []
        for unit in document.units:
            level, reasons = risk_by_unit.get(unit.unit_key, (RiskLevel.R1, ()))
            floor, deterministic_reasons = _semantic_risk_floor(
                unit,
                document.project.source_language,
            )
            if _risk_rank(floor) > _risk_rank(level):
                level = floor
            if _risk_rank(floor) >= _risk_rank(level):
                reasons = (*reasons, *deterministic_reasons)
            if any(item.kind.value == "formula" for item in unit.placeholders.specs):
                level = RiskLevel.R3
                reasons = (*reasons, "protected mathematical content")
            governed = directives_by_unit[unit.unit_key]
            if governed and _risk_rank(level) < _risk_rank(RiskLevel.R2):
                level = RiskLevel.R2
                reasons = (*reasons, "evidence-governed terminology")
            if any(
                term.decision.confidence
                in {
                    DecisionConfidence.PROVISIONAL,
                    DecisionConfidence.RETAINED_UNRESOLVED,
                }
                for directive in governed
                for term in terminology.terms
                if term.revision_id == directive.term_revision_id
            ):
                level = RiskLevel.R3
                reasons = (*reasons, "unresolved or provisional terminology")
            reasons = tuple(sorted(set(item for item in reasons if item.strip())))
            if level is not RiskLevel.R1 and not reasons:
                reasons = ("model-identified semantic risk",)
            routes.append(
                UnitRoute.create(
                    unit_key=unit.unit_key,
                    unit_revision=unit.unit_revision,
                    risk_level=level,
                    lanes=(baseline,) if level is RiskLevel.R1 else (baseline, alternative),
                    reasons=reasons,
                )
            )
        brief = SourceBrief.create(
            document=document,
            brief_text=_required_string(analysis, "brief"),
            origin=f"{ANALYSIS_PROMPT_REVISION};{PLANNER_REVISION}",
        )
        return KernelPlan.create(
            document=document,
            terminology=terminology,
            context_policy=ContextPolicy.create(),
            source_brief=brief,
            lanes=(baseline, alternative),
            routes=routes,
            reviewer=self.config.actor(
                ActorRole.BILINGUAL_REVIEWER,
                prompt_revision=REVIEW_PROMPT_REVISION,
            ),
            adjudicator=self.config.actor(
                ActorRole.ADJUDICATOR,
                prompt_revision=ADJUDICATION_PROMPT_REVISION,
            ),
            editor=self.config.actor(
                ActorRole.CHINESE_EDITOR,
                prompt_revision=EDIT_PROMPT_REVISION,
            ),
            verifier=self.config.actor(
                ActorRole.FINAL_VERIFIER,
                prompt_revision=VERIFICATION_PROMPT_REVISION,
            ),
            global_reviewer=self.config.actor(
                ActorRole.GLOBAL_REVIEWER,
                prompt_revision=GLOBAL_REVIEW_PROMPT_REVISION,
            ),
        )


def _decision_from_review(
    *,
    document: PreparedDocument,
    concept: ConceptSpec,
    materials: tuple[EvidenceMaterial, ...],
    reviewed: dict[str, object],
    researcher_key: str,
    reviewer_key: str,
) -> TermDecision:
    raw_candidates = reviewed.get("candidates")
    if not isinstance(raw_candidates, list):
        raise TerminologyPlanningError("terminology review candidates are malformed")
    material_by_form: dict[str, dict[str, EvidenceMaterial]] = defaultdict(dict)
    for item in materials:
        material_by_form[item.target_form][item.source_key] = item
    candidates: list[TargetTermCandidate] = []
    candidate_by_form: dict[str, TargetTermCandidate] = {}
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        target = str(raw.get("target_form", "")).strip()
        if target not in concept.candidate_forms:
            continue
        assessments = raw.get("assessments")
        if not isinstance(assessments, list):
            assessments = []
        evidence: list[TermEvidence] = []
        for assessment in assessments:
            if not isinstance(assessment, dict):
                continue
            source_key = str(assessment.get("source_key", ""))
            material = material_by_form[target].get(source_key)
            if material is None:
                continue
            try:
                stance = EvidenceStance(str(assessment.get("stance")))
            except ValueError:
                continue
            evidence.append(
                TermEvidence.create(
                    target_form=target,
                    stance=stance,
                    kind=material.kind,
                    tier=material.tier,
                    source_key=_evidence_independence_key(material),
                    source_uri=material.source_uri,
                    source_title=material.source_title,
                    excerpt=material.excerpt,
                    retrieved_on=material.retrieved_on,
                    sense_match=bool(assessment.get("sense_match", False)),
                    domain_match=bool(assessment.get("domain_match", False)),
                    notes=(
                        material.notes
                        + f" Evidence record {material.source_key}."
                        + " Independent review applied."
                    ).strip(),
                )
            )
        semantic = _safe_enum(
            SemanticFit,
            raw.get("semantic_fit"),
            SemanticFit.UNRESOLVED,
        )
        conventionality = _safe_enum(
            Conventionality,
            raw.get("conventionality"),
            Conventionality.UNATTESTED,
        )
        qualified = [
            item
            for item in evidence
            if item.stance is EvidenceStance.SUPPORTS
            and item.sense_match
            and item.domain_match
        ]
        if not qualified:
            semantic = (
                SemanticFit.REJECTED
                if semantic is SemanticFit.REJECTED
                else SemanticFit.UNRESOLVED
            )
            conventionality = Conventionality.UNATTESTED
        elif len({item.source_key for item in qualified}) == 1:
            conventionality = min(
                conventionality,
                Conventionality.ATTESTED,
                key=_conventionality_rank,
            )
        candidate = TargetTermCandidate.create(
            target_form=target,
            semantic_fit=semantic,
            conventionality=conventionality,
            rationale=str(raw.get("rationale", "Independent evidence review.")),
            evidence=evidence,
        )
        candidates.append(candidate)
        candidate_by_form[target] = candidate

    source_candidate = TargetTermCandidate.create(
        target_form=concept.source_term,
        semantic_fit=SemanticFit.UNRESOLVED,
        conventionality=Conventionality.UNATTESTED,
        rationale="Safe source-retention fallback when Chinese evidence is insufficient.",
        evidence=(),
    )
    candidates.append(source_candidate)
    candidate_by_form[concept.source_term] = source_candidate
    raw_selected = reviewed.get("selected_target_form")
    selected_form = str(raw_selected).strip() if raw_selected is not None else ""
    selected = candidate_by_form.get(selected_form)
    confirmed = [
        item for item in candidates if item.semantic_fit is SemanticFit.CONFIRMED
    ]
    if selected is None or selected.semantic_fit is not SemanticFit.CONFIRMED:
        selected = None
    if confirmed:
        mainstream = max(
            confirmed,
            key=lambda item: (
                _conventionality_rank(item.conventionality),
                _evidence_rank(item),
                item.target_form,
            ),
        )
        override = str(reviewed.get("mainstream_override_reason", "")).strip()
        if selected is None:
            selected = mainstream
        elif (
            _conventionality_rank(selected.conventionality)
            < _conventionality_rank(mainstream.conventionality)
            and not override
        ):
            selected = mainstream
    if selected is None:
        selected = source_candidate
        confidence = DecisionConfidence.RETAINED_UNRESOLVED
        override = ""
    else:
        confidence = _confidence(selected)
        if confidence is DecisionConfidence.RETAINED_UNRESOLVED:
            selected = source_candidate
            override = ""
    return TermDecision.create(
        project_key=document.project.project_key,
        source_term=concept.source_term,
        sense_id=concept.sense_id,
        concept_definition=concept.concept_definition,
        domain=concept.domain,
        candidates=candidates,
        selected_candidate_id=selected.candidate_id,
        confidence=confidence,
        rationale=str(
            reviewed.get(
                "rationale",
                "Independent review selected the strongest qualified evidence.",
            )
        ),
        mainstream_override_reason=override,
        researcher_actor_key=researcher_key,
        reviewer_actor_key=reviewer_key,
    )


def _confidence(candidate: TargetTermCandidate) -> DecisionConfidence:
    support = candidate.qualified_support
    sources = {item.source_key for item in support}
    strong = any(
        item.tier in {EvidenceTier.A_AUTHORITY, EvidenceTier.B_DOMAIN}
        for item in support
    )
    authority_conflict = any(
        item.tier is EvidenceTier.A_AUTHORITY
        for item in candidate.qualified_contradictions
    )
    if (
        len(sources) >= 2
        and strong
        and not authority_conflict
        and candidate.conventionality
        in {Conventionality.ESTABLISHED, Conventionality.ATTESTED}
    ):
        return DecisionConfidence.VERIFIED
    if support and (strong or len(sources) >= 2) and not authority_conflict:
        return DecisionConfidence.SUPPORTED
    if support:
        return DecisionConfidence.PROVISIONAL
    return DecisionConfidence.RETAINED_UNRESOLVED


def _evidence_independence_key(material: EvidenceMaterial) -> str:
    """Group web records by publishing authority, not by individual URL."""

    parsed = urllib.parse.urlparse(material.source_uri)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme in {"http", "https"} and host:
        if host.startswith("www."):
            host = host[4:]
        for authority in ("termonline.cn", "cnterm.cn", "gov.cn"):
            if host == authority or host.endswith(f".{authority}"):
                host = authority
                break
        return digest(
            "pubtrans.term-evidence-authority/v1",
            {"web_authority": host},
        )
    return digest(
        "pubtrans.term-evidence-authority/v1",
        {"declared_source": material.source_key},
    )


def _evidence_rank(candidate: TargetTermCandidate) -> int:
    return sum(
        {
            EvidenceTier.A_AUTHORITY: 5,
            EvidenceTier.B_DOMAIN: 4,
            EvidenceTier.C_CORPUS: 2,
            EvidenceTier.D_REFERENCE: 1,
            EvidenceTier.E_WEAK: 0,
        }[item.tier]
        for item in candidate.qualified_support
    )


def _conventionality_rank(value: Conventionality) -> int:
    return {
        Conventionality.UNATTESTED: 0,
        Conventionality.RARE: 1,
        Conventionality.ATTESTED: 2,
        Conventionality.ESTABLISHED: 3,
    }[value]


def _concepts(
    analysis: dict[str, object],
    document: PreparedDocument,
) -> tuple[ConceptSpec, ...]:
    raw = analysis.get("concepts")
    if not isinstance(raw, list):
        raise TerminologyPlanningError("document concepts are malformed")
    source_all = "\n".join(item.source_text for item in document.units)
    grouped: dict[str, list[ConceptSpec]] = defaultdict(list)
    for item in raw:
        if not isinstance(item, dict):
            continue
        source_term = str(item.get("source_term", "")).strip()
        if not source_term or "[[PT2-" in source_term or source_term not in source_all:
            continue
        candidate_raw = item.get("candidate_forms")
        if not isinstance(candidate_raw, list):
            continue
        forms = tuple(
            sorted(
                {
                    str(value).strip()
                    for value in candidate_raw
                    if str(value).strip() and str(value).strip() != source_term
                }
            )
        )
        if not forms:
            continue
        concept = ConceptSpec(
            source_term=source_term,
            sense_id=str(item.get("sense_id", "")).strip(),
            concept_definition=str(item.get("concept_definition", "")).strip(),
            domain=str(item.get("domain", "")).strip(),
            candidate_forms=forms,
            rationale=str(item.get("rationale", "")).strip(),
        )
        if all(
            (
                concept.sense_id,
                concept.concept_definition,
                concept.domain,
                concept.rationale,
            )
        ):
            grouped[source_term].append(concept)
    result = []
    for source_term, items in grouped.items():
        unique = {(item.source_term, item.sense_id): item for item in items}
        if len(unique) == 1:
            result.append(next(iter(unique.values())))
    return tuple(sorted(result, key=lambda item: (item.source_term, item.sense_id)))


def _occurrences(
    *,
    document: PreparedDocument,
    concept: ConceptSpec,
    occupied: dict[str, list[tuple[int, int]]],
) -> tuple[TermOccurrence, ...]:
    from pubtrans.m1.terminology import term_key_for

    term_key = term_key_for(
        project_key=document.project.project_key,
        source_term=concept.source_term,
        sense_id=concept.sense_id,
    )
    result = []
    for unit in document.units:
        for match in re.finditer(re.escape(concept.source_term), unit.source_text):
            start, end = match.span()
            if any(start < old_end and old_start < end for old_start, old_end in occupied[unit.unit_key]):
                continue
            result.append(
                TermOccurrence.create(
                    term_key=term_key,
                    unit=unit,
                    source_start=start,
                    source_end=end,
                )
            )
    return tuple(result)


def _risk_map(
    analysis: dict[str, object],
    document: PreparedDocument,
) -> dict[str, tuple[RiskLevel, tuple[str, ...]]]:
    known = {item.unit_key for item in document.units}
    raw = analysis.get("risks")
    if not isinstance(raw, list):
        return {}
    result: dict[str, tuple[RiskLevel, tuple[str, ...]]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("unit_key", ""))
        if key not in known:
            continue
        try:
            level = RiskLevel(str(item.get("risk_level")))
        except ValueError:
            continue
        reasons_raw = item.get("reasons")
        reasons = (
            tuple(sorted({str(value).strip() for value in reasons_raw if str(value).strip()}))
            if isinstance(reasons_raw, list)
            else ()
        )
        result[key] = (level, reasons)
    return result


def _semantic_risk_floor(
    unit: PreparedUnit,
    source_language: str,
) -> tuple[RiskLevel, tuple[str, ...]]:
    source = _PROTECTED_TOKEN_RE.sub(" ", unit.source_text)
    reasons = []
    if re.search(r"(?<![A-Za-z])\d", source):
        reasons.append("protected numeric meaning")
    if "http://" in source or "https://" in source:
        reasons.append("source URL")
    if source_language.lower().startswith("en") and _ENGLISH_SEMANTIC_RISK_RE.search(
        source
    ):
        reasons.append("negation, modality or logical scope")
    return (
        (RiskLevel.R2, tuple(reasons))
        if reasons
        else (RiskLevel.R1, ())
    )


def _risk_rank(value: RiskLevel) -> int:
    return {RiskLevel.R1: 1, RiskLevel.R2: 2, RiskLevel.R3: 3}[value]


def _unit_chunks(
    units: tuple[PreparedUnit, ...],
    maximum_characters: int,
) -> tuple[tuple[PreparedUnit, ...], ...]:
    chunks: list[tuple[PreparedUnit, ...]] = []
    current: list[PreparedUnit] = []
    size = 0
    for unit in units:
        length = len(unit.source_text)
        if current and size + length > maximum_characters:
            chunks.append(tuple(current))
            current = []
            size = 0
        current.append(unit)
        size += length
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _analysis_unit(unit: PreparedUnit) -> dict[str, object]:
    return {
        "unit_key": unit.unit_key,
        "page_ordinal": unit.locator.page_ordinal,
        "paragraph_ordinal": unit.locator.paragraph_ordinal,
        "layout_label": unit.layout_label,
        "source_text": unit.source_text,
        "protected_placeholders": [
            {
                "kind": item.kind.value,
                "open_token": item.open_token,
                "close_token": item.close_token,
            }
            for item in unit.placeholders.specs
        ],
    }


def _validated_analysis(payload: dict[str, object]) -> dict[str, object]:
    _required_string(payload, "brief")
    if not isinstance(payload.get("concepts"), list) or not isinstance(
        payload.get("risks"), list
    ):
        raise TerminologyPlanningError("document analysis lists are malformed")
    return payload


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TerminologyPlanningError(f"planning response {key} is invalid")
    return value


def _dict_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TerminologyPlanningError("cached planning response is not an object")
    return value


def _materials_value(value: object) -> tuple[EvidenceMaterial, ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise TerminologyPlanningError("cached harvested evidence is malformed")
    return tuple(
        EvidenceMaterial.from_payload(item)
        for item in value
        if isinstance(item, dict)
    )


def _safe_enum(enum_type, value: object, default):
    try:
        return enum_type(str(value))
    except ValueError:
        return default
