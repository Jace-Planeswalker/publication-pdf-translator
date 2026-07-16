"""Sense-aware, occurrence-bound M1 terminology contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.canonical import sha256_text
from pubtrans.m0v2.errors import IdentityError
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedUnit

from .errors import TerminologyError


TERM_NAMESPACE = "pubtrans.term/v1"
TERM_EVIDENCE_NAMESPACE = "pubtrans.term-evidence/v1"
TERM_CANDIDATE_NAMESPACE = "pubtrans.term-candidate/v1"
TERM_DECISION_NAMESPACE = "pubtrans.term-decision/v1"
TERM_REVISION_NAMESPACE = "pubtrans.term-revision/v1"
TERM_OCCURRENCE_NAMESPACE = "pubtrans.term-occurrence/v1"
TERM_DIRECTIVE_NAMESPACE = "pubtrans.term-directive/v1"
TERMINOLOGY_SNAPSHOT_NAMESPACE = "pubtrans.terminology-snapshot/v1"
TERM_APPLICATION_NAMESPACE = "pubtrans.term-application/v1"
RENDERED_TARGET_NAMESPACE = "pubtrans.rendered-target/v1"


def _nonempty(name: str, value: str) -> str:
    result = normalize_text(value).strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


class TermTreatment(str, Enum):
    TRANSLATE_ONLY = "TRANSLATE_ONLY"
    TRANSLATE_WITH_SOURCE_FIRST = "TRANSLATE_WITH_SOURCE_FIRST"
    TRANSLATE_WITH_SOURCE_ALWAYS = "TRANSLATE_WITH_SOURCE_ALWAYS"
    RETAIN_SOURCE = "RETAIN_SOURCE"


class EvidenceKind(str, Enum):
    AUTHORITY_TERMBANK = "AUTHORITY_TERMBANK"
    OFFICIAL_NAMING = "OFFICIAL_NAMING"
    DOMAIN_PRIMARY = "DOMAIN_PRIMARY"
    PARALLEL_PUBLICATION = "PARALLEL_PUBLICATION"
    CORPUS_ATTESTATION = "CORPUS_ATTESTATION"
    DICTIONARY = "DICTIONARY"
    GENERAL_REFERENCE = "GENERAL_REFERENCE"


class EvidenceTier(str, Enum):
    A_AUTHORITY = "A_AUTHORITY"
    B_DOMAIN = "B_DOMAIN"
    C_CORPUS = "C_CORPUS"
    D_REFERENCE = "D_REFERENCE"
    E_WEAK = "E_WEAK"


class EvidenceStance(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"


class SemanticFit(str, Enum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNRESOLVED = "UNRESOLVED"


class Conventionality(str, Enum):
    ESTABLISHED = "ESTABLISHED"
    ATTESTED = "ATTESTED"
    RARE = "RARE"
    UNATTESTED = "UNATTESTED"


class DecisionConfidence(str, Enum):
    VERIFIED = "VERIFIED"
    SUPPORTED = "SUPPORTED"
    PROVISIONAL = "PROVISIONAL"
    RETAINED_UNRESOLVED = "RETAINED_UNRESOLVED"


_CONVENTIONALITY_RANK = {
    Conventionality.ESTABLISHED: 3,
    Conventionality.ATTESTED: 2,
    Conventionality.RARE: 1,
    Conventionality.UNATTESTED: 0,
}


def term_key_for(*, project_key: str, source_term: str, sense_id: str) -> str:
    require_sha256("project_key", project_key)
    return digest(
        TERM_NAMESPACE,
        {
            "project_key": project_key,
            "source_term": _nonempty("source_term", source_term),
            "sense_id": _nonempty("sense_id", sense_id),
        },
    )


@dataclass(frozen=True, slots=True)
class TermOccurrence:
    occurrence_key: str
    term_key: str
    unit_key: str
    unit_revision: str
    source_start: int
    source_end: int
    matched_source: str

    @classmethod
    def create(
        cls,
        *,
        term_key: str,
        unit: PreparedUnit,
        source_start: int,
        source_end: int,
    ) -> "TermOccurrence":
        require_sha256("term_key", term_key)
        if not 0 <= source_start < source_end <= len(unit.source_text):
            raise TerminologyError("term occurrence source span is outside the unit")
        matched_source = unit.source_text[source_start:source_end]
        if not matched_source.strip():
            raise TerminologyError("term occurrence cannot bind blank source text")
        payload = {
            "term_key": term_key,
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "source_start": source_start,
            "source_end": source_end,
            "matched_source": matched_source,
        }
        return cls(
            occurrence_key=digest(TERM_OCCURRENCE_NAMESPACE, payload),
            **payload,
        )

    def __post_init__(self) -> None:
        for name in ("occurrence_key", "term_key", "unit_key", "unit_revision"):
            require_sha256(name, getattr(self, name))
        if self.matched_source != normalize_text(self.matched_source):
            raise ValueError("matched_source is not canonical")
        if not self.matched_source.strip():
            raise ValueError("matched_source must not be blank")
        if self.source_start < 0 or self.source_end <= self.source_start:
            raise ValueError("term occurrence span is invalid")
        if self.occurrence_key != digest(
            TERM_OCCURRENCE_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term occurrence key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "term_key": self.term_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "matched_source": self.matched_source,
        }

    def as_payload(self) -> dict[str, object]:
        return {"occurrence_key": self.occurrence_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TermOccurrence":
        return cls(
            occurrence_key=str(payload["occurrence_key"]),
            term_key=str(payload["term_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            source_start=int(payload["source_start"]),
            source_end=int(payload["source_end"]),
            matched_source=str(payload["matched_source"]),
        )


@dataclass(frozen=True, slots=True)
class TermEvidence:
    """One auditable target-language attestation or counterexample."""

    evidence_id: str
    target_form: str
    stance: EvidenceStance
    kind: EvidenceKind
    tier: EvidenceTier
    source_key: str
    source_uri: str
    source_title: str
    excerpt: str
    retrieved_on: str
    sense_match: bool
    domain_match: bool
    notes: str

    @classmethod
    def create(
        cls,
        *,
        target_form: str,
        stance: EvidenceStance,
        kind: EvidenceKind,
        tier: EvidenceTier,
        source_key: str,
        source_uri: str,
        source_title: str,
        excerpt: str,
        retrieved_on: str,
        sense_match: bool,
        domain_match: bool,
        notes: str = "",
    ) -> "TermEvidence":
        target_form = _nonempty("evidence target form", target_form)
        source_key = _nonempty("evidence source key", source_key)
        source_uri = _nonempty("evidence source URI", source_uri)
        source_title = _nonempty("evidence source title", source_title)
        excerpt = _nonempty("evidence excerpt", excerpt)
        notes = normalize_text(notes).strip()
        payload = {
            "target_form": target_form,
            "stance": stance.value,
            "kind": kind.value,
            "tier": tier.value,
            "source_key": source_key,
            "source_uri": source_uri,
            "source_title": source_title,
            "excerpt": excerpt,
            "retrieved_on": retrieved_on,
            "sense_match": bool(sense_match),
            "domain_match": bool(domain_match),
            "notes": notes,
        }
        return cls(
            evidence_id=digest(TERM_EVIDENCE_NAMESPACE, payload),
            target_form=target_form,
            stance=stance,
            kind=kind,
            tier=tier,
            source_key=source_key,
            source_uri=source_uri,
            source_title=source_title,
            excerpt=excerpt,
            retrieved_on=retrieved_on,
            sense_match=bool(sense_match),
            domain_match=bool(domain_match),
            notes=notes,
        )

    def __post_init__(self) -> None:
        require_sha256("evidence_id", self.evidence_id)
        for name in (
            "target_form",
            "source_key",
            "source_uri",
            "source_title",
            "excerpt",
        ):
            if getattr(self, name) != _nonempty(name, getattr(self, name)):
                raise ValueError(f"term evidence {name} is not canonical")
        if not self.source_uri.startswith(("https://", "http://", "urn:")):
            raise TerminologyError("term evidence URI must be HTTP(S) or URN")
        try:
            date.fromisoformat(self.retrieved_on)
        except ValueError as exc:
            raise TerminologyError("term evidence date must use YYYY-MM-DD") from exc
        if self.notes != normalize_text(self.notes).strip():
            raise ValueError("term evidence notes are not canonical")
        if self.evidence_id != digest(
            TERM_EVIDENCE_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term evidence id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "target_form": self.target_form,
            "stance": self.stance.value,
            "kind": self.kind.value,
            "tier": self.tier.value,
            "source_key": self.source_key,
            "source_uri": self.source_uri,
            "source_title": self.source_title,
            "excerpt": self.excerpt,
            "retrieved_on": self.retrieved_on,
            "sense_match": self.sense_match,
            "domain_match": self.domain_match,
            "notes": self.notes,
        }

    def as_payload(self) -> dict[str, object]:
        return {"evidence_id": self.evidence_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TermEvidence":
        return cls(
            evidence_id=str(payload["evidence_id"]),
            target_form=str(payload["target_form"]),
            stance=EvidenceStance(str(payload["stance"])),
            kind=EvidenceKind(str(payload["kind"])),
            tier=EvidenceTier(str(payload["tier"])),
            source_key=str(payload["source_key"]),
            source_uri=str(payload["source_uri"]),
            source_title=str(payload["source_title"]),
            excerpt=str(payload["excerpt"]),
            retrieved_on=str(payload["retrieved_on"]),
            sense_match=bool(payload["sense_match"]),
            domain_match=bool(payload["domain_match"]),
            notes=str(payload["notes"]),
        )


@dataclass(frozen=True, slots=True)
class TargetTermCandidate:
    candidate_id: str
    target_form: str
    semantic_fit: SemanticFit
    conventionality: Conventionality
    rationale: str
    evidence: tuple[TermEvidence, ...]

    @classmethod
    def create(
        cls,
        *,
        target_form: str,
        semantic_fit: SemanticFit,
        conventionality: Conventionality,
        rationale: str,
        evidence: tuple[TermEvidence, ...] | list[TermEvidence],
    ) -> "TargetTermCandidate":
        target_form = _nonempty("candidate target form", target_form)
        rationale = _nonempty("candidate rationale", rationale)
        evidence = tuple(sorted(evidence, key=lambda item: item.evidence_id))
        payload = {
            "target_form": target_form,
            "semantic_fit": semantic_fit.value,
            "conventionality": conventionality.value,
            "rationale": rationale,
            "evidence": [item.as_payload() for item in evidence],
        }
        return cls(
            candidate_id=digest(TERM_CANDIDATE_NAMESPACE, payload),
            target_form=target_form,
            semantic_fit=semantic_fit,
            conventionality=conventionality,
            rationale=rationale,
            evidence=evidence,
        )

    def __post_init__(self) -> None:
        require_sha256("candidate_id", self.candidate_id)
        if self.target_form != _nonempty("candidate target form", self.target_form):
            raise ValueError("candidate target form is not canonical")
        if self.rationale != _nonempty("candidate rationale", self.rationale):
            raise ValueError("candidate rationale is not canonical")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if evidence_ids != sorted(evidence_ids) or len(evidence_ids) != len(
            set(evidence_ids)
        ):
            raise TerminologyError("candidate evidence is not canonical and unique")
        if any(item.target_form != self.target_form for item in self.evidence):
            raise TerminologyError("candidate contains evidence for another target form")
        if self.semantic_fit is SemanticFit.CONFIRMED and not self.qualified_support:
            raise TerminologyError("a confirmed term candidate requires qualified evidence")
        if self.candidate_id != digest(
            TERM_CANDIDATE_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term candidate id mismatch")

    @property
    def qualified_support(self) -> tuple[TermEvidence, ...]:
        return tuple(
            item
            for item in self.evidence
            if item.stance is EvidenceStance.SUPPORTS
            and item.sense_match
            and item.domain_match
        )

    @property
    def qualified_contradictions(self) -> tuple[TermEvidence, ...]:
        return tuple(
            item
            for item in self.evidence
            if item.stance is EvidenceStance.CONTRADICTS
            and item.sense_match
            and item.domain_match
        )

    def _identity_payload(self) -> dict[str, object]:
        return {
            "target_form": self.target_form,
            "semantic_fit": self.semantic_fit.value,
            "conventionality": self.conventionality.value,
            "rationale": self.rationale,
            "evidence": [item.as_payload() for item in self.evidence],
        }

    def as_payload(self) -> dict[str, object]:
        return {"candidate_id": self.candidate_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TargetTermCandidate":
        raw_evidence = payload["evidence"]
        if not isinstance(raw_evidence, list) or any(
            not isinstance(item, dict) for item in raw_evidence
        ):
            raise ValueError("term candidate evidence payload is malformed")
        return cls(
            candidate_id=str(payload["candidate_id"]),
            target_form=str(payload["target_form"]),
            semantic_fit=SemanticFit(str(payload["semantic_fit"])),
            conventionality=Conventionality(str(payload["conventionality"])),
            rationale=str(payload["rationale"]),
            evidence=tuple(TermEvidence.from_payload(item) for item in raw_evidence),
        )


@dataclass(frozen=True, slots=True)
class TermDecision:
    """Concept-oriented decision with mainstream-use evidence and counterevidence."""

    decision_id: str
    term_key: str
    project_key: str
    source_term: str
    sense_id: str
    concept_definition: str
    domain: str
    candidates: tuple[TargetTermCandidate, ...]
    selected_candidate_id: str
    confidence: DecisionConfidence
    rationale: str
    mainstream_override_reason: str
    researcher_actor_key: str
    reviewer_actor_key: str

    @classmethod
    def create(
        cls,
        *,
        project_key: str,
        source_term: str,
        sense_id: str,
        concept_definition: str,
        domain: str,
        candidates: tuple[TargetTermCandidate, ...] | list[TargetTermCandidate],
        selected_candidate_id: str,
        confidence: DecisionConfidence,
        rationale: str,
        researcher_actor_key: str,
        reviewer_actor_key: str,
        mainstream_override_reason: str = "",
    ) -> "TermDecision":
        source_term = _nonempty("source_term", source_term)
        sense_id = _nonempty("sense_id", sense_id)
        concept_definition = _nonempty("concept definition", concept_definition)
        domain = _nonempty("term domain", domain)
        rationale = _nonempty("term decision rationale", rationale)
        mainstream_override_reason = normalize_text(mainstream_override_reason).strip()
        candidates = tuple(sorted(candidates, key=lambda item: item.candidate_id))
        term_key = term_key_for(
            project_key=project_key,
            source_term=source_term,
            sense_id=sense_id,
        )
        payload = {
            "term_key": term_key,
            "project_key": project_key,
            "source_term": source_term,
            "sense_id": sense_id,
            "concept_definition": concept_definition,
            "domain": domain,
            "candidates": [item.as_payload() for item in candidates],
            "selected_candidate_id": selected_candidate_id,
            "confidence": confidence.value,
            "rationale": rationale,
            "mainstream_override_reason": mainstream_override_reason,
            "researcher_actor_key": researcher_actor_key,
            "reviewer_actor_key": reviewer_actor_key,
        }
        return cls(
            decision_id=digest(TERM_DECISION_NAMESPACE, payload),
            term_key=term_key,
            project_key=project_key,
            source_term=source_term,
            sense_id=sense_id,
            concept_definition=concept_definition,
            domain=domain,
            candidates=candidates,
            selected_candidate_id=selected_candidate_id,
            confidence=confidence,
            rationale=rationale,
            mainstream_override_reason=mainstream_override_reason,
            researcher_actor_key=researcher_actor_key,
            reviewer_actor_key=reviewer_actor_key,
        )

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "term_key",
            "project_key",
            "selected_candidate_id",
            "researcher_actor_key",
            "reviewer_actor_key",
        ):
            require_sha256(name, getattr(self, name))
        for name in (
            "source_term",
            "sense_id",
            "concept_definition",
            "domain",
            "rationale",
        ):
            if getattr(self, name) != _nonempty(name, getattr(self, name)):
                raise ValueError(f"term decision {name} is not canonical")
        if self.mainstream_override_reason != normalize_text(
            self.mainstream_override_reason
        ).strip():
            raise ValueError("mainstream override reason is not canonical")
        if self.researcher_actor_key == self.reviewer_actor_key:
            raise TerminologyError("term research and approval require distinct actors")
        if self.term_key != term_key_for(
            project_key=self.project_key,
            source_term=self.source_term,
            sense_id=self.sense_id,
        ):
            raise IdentityError("term decision key mismatch")
        candidate_ids = [item.candidate_id for item in self.candidates]
        target_forms = [item.target_form for item in self.candidates]
        if (
            not candidate_ids
            or candidate_ids != sorted(candidate_ids)
            or len(candidate_ids) != len(set(candidate_ids))
            or len(target_forms) != len(set(target_forms))
        ):
            raise TerminologyError("term decision candidates are not canonical and unique")
        selected = self.selected_candidate
        if (
            self.confidence is DecisionConfidence.RETAINED_UNRESOLVED
            and selected.semantic_fit
            not in {SemanticFit.CONFIRMED, SemanticFit.UNRESOLVED}
        ) or (
            self.confidence is not DecisionConfidence.RETAINED_UNRESOLVED
            and selected.semantic_fit is not SemanticFit.CONFIRMED
        ):
            raise TerminologyError("selected term candidate lacks confirmed sense fit")
        self._validate_confidence(selected)
        better_mainstream = [
            item
            for item in self.candidates
            if item.semantic_fit is SemanticFit.CONFIRMED
            and _CONVENTIONALITY_RANK[item.conventionality]
            > _CONVENTIONALITY_RANK[selected.conventionality]
        ]
        if (
            self.confidence is not DecisionConfidence.RETAINED_UNRESOLVED
            and better_mainstream
            and not self.mainstream_override_reason
        ):
            raise TerminologyError(
                "a less conventional term requires an explicit accuracy override"
            )
        if self.decision_id != digest(
            TERM_DECISION_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term decision id mismatch")

    @property
    def selected_candidate(self) -> TargetTermCandidate:
        for candidate in self.candidates:
            if candidate.candidate_id == self.selected_candidate_id:
                return candidate
        raise TerminologyError("selected term candidate is not in the decision")

    @property
    def target_term(self) -> str:
        return self.selected_candidate.target_form

    def _validate_confidence(self, selected: TargetTermCandidate) -> None:
        support = selected.qualified_support
        source_keys = {item.source_key for item in support}
        strong = {
            item.source_key
            for item in support
            if item.tier in {EvidenceTier.A_AUTHORITY, EvidenceTier.B_DOMAIN}
        }
        authoritative_contradiction = any(
            item.tier is EvidenceTier.A_AUTHORITY
            for item in selected.qualified_contradictions
        )
        if self.confidence is DecisionConfidence.VERIFIED:
            if len(source_keys) < 2 or not strong or authoritative_contradiction:
                raise TerminologyError(
                    "VERIFIED terminology requires two independent sources, one strong, "
                    "and no authoritative contradiction"
                )
            if selected.conventionality not in {
                Conventionality.ESTABLISHED,
                Conventionality.ATTESTED,
            }:
                raise TerminologyError("VERIFIED terminology cannot be rare or unattested")
        elif self.confidence is DecisionConfidence.SUPPORTED:
            if not support or (not strong and len(source_keys) < 2):
                raise TerminologyError(
                    "SUPPORTED terminology requires strong or corroborated evidence"
                )
            if authoritative_contradiction:
                raise TerminologyError(
                    "authoritative contradiction prevents SUPPORTED confidence"
                )
        elif self.confidence is DecisionConfidence.PROVISIONAL:
            if not support:
                raise TerminologyError("PROVISIONAL terminology still requires attestation")
        elif self.confidence is DecisionConfidence.RETAINED_UNRESOLVED:
            if selected.target_form != self.source_term:
                raise TerminologyError(
                    "unresolved terminology must retain the source expression"
                )

    def _identity_payload(self) -> dict[str, object]:
        return {
            "term_key": self.term_key,
            "project_key": self.project_key,
            "source_term": self.source_term,
            "sense_id": self.sense_id,
            "concept_definition": self.concept_definition,
            "domain": self.domain,
            "candidates": [item.as_payload() for item in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "confidence": self.confidence.value,
            "rationale": self.rationale,
            "mainstream_override_reason": self.mainstream_override_reason,
            "researcher_actor_key": self.researcher_actor_key,
            "reviewer_actor_key": self.reviewer_actor_key,
        }

    def as_payload(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TermDecision":
        raw_candidates = payload["candidates"]
        if not isinstance(raw_candidates, list) or any(
            not isinstance(item, dict) for item in raw_candidates
        ):
            raise ValueError("term decision candidates payload is malformed")
        return cls(
            decision_id=str(payload["decision_id"]),
            term_key=str(payload["term_key"]),
            project_key=str(payload["project_key"]),
            source_term=str(payload["source_term"]),
            sense_id=str(payload["sense_id"]),
            concept_definition=str(payload["concept_definition"]),
            domain=str(payload["domain"]),
            candidates=tuple(
                TargetTermCandidate.from_payload(item) for item in raw_candidates
            ),
            selected_candidate_id=str(payload["selected_candidate_id"]),
            confidence=DecisionConfidence(str(payload["confidence"])),
            rationale=str(payload["rationale"]),
            mainstream_override_reason=str(payload["mainstream_override_reason"]),
            researcher_actor_key=str(payload["researcher_actor_key"]),
            reviewer_actor_key=str(payload["reviewer_actor_key"]),
        )


@dataclass(frozen=True, slots=True)
class TermRevision:
    term_key: str
    revision_id: str
    decision: TermDecision
    treatment: TermTreatment
    rationale: str
    occurrences: tuple[TermOccurrence, ...]
    supersedes_revision_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        decision: TermDecision,
        treatment: TermTreatment,
        rationale: str,
        occurrences: tuple[TermOccurrence, ...] | list[TermOccurrence],
        supersedes_revision_id: str | None = None,
    ) -> "TermRevision":
        rationale = _nonempty("rationale", rationale)
        term_key = decision.term_key
        occurrences = tuple(
            sorted(
                occurrences,
                key=lambda item: (
                    item.unit_key,
                    item.source_start,
                    item.source_end,
                    item.occurrence_key,
                ),
            )
        )
        payload = {
            "term_key": term_key,
            "decision": decision.as_payload(),
            "treatment": treatment.value,
            "rationale": rationale,
            "occurrences": [item.as_payload() for item in occurrences],
            "supersedes_revision_id": supersedes_revision_id,
        }
        return cls(
            revision_id=digest(TERM_REVISION_NAMESPACE, payload),
            term_key=term_key,
            decision=decision,
            treatment=treatment,
            rationale=rationale,
            occurrences=occurrences,
            supersedes_revision_id=supersedes_revision_id,
        )

    def __post_init__(self) -> None:
        for name in ("term_key", "revision_id"):
            require_sha256(name, getattr(self, name))
        if self.supersedes_revision_id is not None:
            require_sha256("supersedes_revision_id", self.supersedes_revision_id)
            if self.supersedes_revision_id == self.revision_id:
                raise ValueError("term revision cannot supersede itself")
        if self.rationale != _nonempty("rationale", self.rationale):
            raise ValueError("term revision rationale is not canonical")
        if self.term_key != self.decision.term_key:
            raise IdentityError("term key mismatch")
        if self.treatment is TermTreatment.RETAIN_SOURCE:
            if self.target_term != self.source_term:
                raise TerminologyError(
                    "RETAIN_SOURCE requires target_term to equal source_term"
                )
        if self.decision.confidence is DecisionConfidence.RETAINED_UNRESOLVED:
            if self.treatment is not TermTreatment.RETAIN_SOURCE:
                raise TerminologyError("unresolved terminology must retain the source")
        if (
            self.decision.confidence is DecisionConfidence.PROVISIONAL
            and self.treatment is TermTreatment.TRANSLATE_ONLY
        ):
            raise TerminologyError(
                "provisional terminology must expose the source on first use"
            )
        if any(item.term_key != self.term_key for item in self.occurrences):
            raise TerminologyError("term revision contains an occurrence for another term")
        keys = [item.occurrence_key for item in self.occurrences]
        if len(keys) != len(set(keys)):
            raise TerminologyError("term revision contains duplicate occurrences")
        expected_order = tuple(
            sorted(
                self.occurrences,
                key=lambda item: (
                    item.unit_key,
                    item.source_start,
                    item.source_end,
                    item.occurrence_key,
                ),
            )
        )
        if self.occurrences != expected_order:
            raise ValueError("term occurrences are not in canonical order")
        if self.revision_id != digest(
            TERM_REVISION_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term revision id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "term_key": self.term_key,
            "decision": self.decision.as_payload(),
            "treatment": self.treatment.value,
            "rationale": self.rationale,
            "occurrences": [item.as_payload() for item in self.occurrences],
            "supersedes_revision_id": self.supersedes_revision_id,
        }

    def as_payload(self) -> dict[str, object]:
        return {"revision_id": self.revision_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TermRevision":
        raw_decision = payload["decision"]
        raw_occurrences = payload["occurrences"]
        if not isinstance(raw_decision, dict) or not isinstance(
            raw_occurrences, list
        ) or any(
            not isinstance(item, dict) for item in raw_occurrences
        ):
            raise ValueError("term occurrences payload is malformed")
        supersedes = payload.get("supersedes_revision_id")
        return cls(
            term_key=str(payload["term_key"]),
            revision_id=str(payload["revision_id"]),
            decision=TermDecision.from_payload(raw_decision),
            treatment=TermTreatment(str(payload["treatment"])),
            rationale=str(payload["rationale"]),
            occurrences=tuple(
                TermOccurrence.from_payload(item) for item in raw_occurrences
            ),
            supersedes_revision_id=(str(supersedes) if supersedes is not None else None),
        )

    @property
    def project_key(self) -> str:
        return self.decision.project_key

    @property
    def source_term(self) -> str:
        return self.decision.source_term

    @property
    def sense_id(self) -> str:
        return self.decision.sense_id

    @property
    def target_term(self) -> str:
        return self.decision.target_term


@dataclass(frozen=True, slots=True)
class TermDirective:
    directive_id: str
    term_revision_id: str
    occurrence_key: str
    unit_key: str
    required_rendering: str
    first_for_term: bool

    @classmethod
    def create(
        cls,
        *,
        term: TermRevision,
        occurrence: TermOccurrence,
        first_for_term: bool,
    ) -> "TermDirective":
        if occurrence.term_key != term.term_key:
            raise TerminologyError("directive term and occurrence differ")
        if term.treatment is TermTreatment.RETAIN_SOURCE:
            required = occurrence.matched_source
        elif term.treatment is TermTreatment.TRANSLATE_WITH_SOURCE_ALWAYS or (
            term.treatment is TermTreatment.TRANSLATE_WITH_SOURCE_FIRST
            and first_for_term
        ):
            required = f"{term.target_term}（{term.source_term}）"
        else:
            required = term.target_term
        payload = {
            "term_revision_id": term.revision_id,
            "occurrence_key": occurrence.occurrence_key,
            "unit_key": occurrence.unit_key,
            "required_rendering": required,
            "first_for_term": first_for_term,
        }
        return cls(
            directive_id=digest(TERM_DIRECTIVE_NAMESPACE, payload),
            **payload,
        )

    def __post_init__(self) -> None:
        for name in (
            "directive_id",
            "term_revision_id",
            "occurrence_key",
            "unit_key",
        ):
            require_sha256(name, getattr(self, name))
        if self.required_rendering != _nonempty(
            "required_rendering",
            self.required_rendering,
        ):
            raise ValueError("required_rendering is not canonical")
        if self.directive_id != digest(
            TERM_DIRECTIVE_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term directive id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "term_revision_id": self.term_revision_id,
            "occurrence_key": self.occurrence_key,
            "unit_key": self.unit_key,
            "required_rendering": self.required_rendering,
            "first_for_term": self.first_for_term,
        }

    def as_payload(self) -> dict[str, object]:
        return {"directive_id": self.directive_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TermDirective":
        return cls(
            directive_id=str(payload["directive_id"]),
            term_revision_id=str(payload["term_revision_id"]),
            occurrence_key=str(payload["occurrence_key"]),
            unit_key=str(payload["unit_key"]),
            required_rendering=str(payload["required_rendering"]),
            first_for_term=bool(payload["first_for_term"]),
        )


@dataclass(frozen=True, slots=True)
class TerminologySnapshot:
    snapshot_id: str
    project_key: str
    manifest_sha256: str
    terms: tuple[TermRevision, ...]
    directives: tuple[TermDirective, ...]

    @classmethod
    def create(
        cls,
        document: PreparedDocument,
        terms: tuple[TermRevision, ...] | list[TermRevision],
    ) -> "TerminologySnapshot":
        terms = tuple(sorted(terms, key=lambda item: item.term_key))
        term_keys = [item.term_key for item in terms]
        if len(term_keys) != len(set(term_keys)):
            raise TerminologyError("one terminology snapshot has two active revisions")
        unit_order = {unit.unit_key: index for index, unit in enumerate(document.units)}
        unit_by_key = {unit.unit_key: unit for unit in document.units}
        occupied: dict[str, list[tuple[int, int, str]]] = {}
        directives: list[TermDirective] = []

        for term in terms:
            if term.project_key != document.project.project_key:
                raise TerminologyError("term belongs to another project")
            ordered_occurrences = sorted(
                term.occurrences,
                key=lambda item: (
                    unit_order.get(item.unit_key, len(unit_order)),
                    item.source_start,
                    item.source_end,
                    item.occurrence_key,
                ),
            )
            for index, occurrence in enumerate(ordered_occurrences):
                unit = unit_by_key.get(occurrence.unit_key)
                if unit is None:
                    raise TerminologyError("term occurrence references an unknown unit")
                if occurrence.unit_revision != unit.unit_revision:
                    raise TerminologyError("term occurrence references a stale unit")
                if occurrence.source_end > len(unit.source_text):
                    raise TerminologyError("term occurrence exceeds source text")
                if (
                    unit.source_text[occurrence.source_start : occurrence.source_end]
                    != occurrence.matched_source
                ):
                    raise TerminologyError("term occurrence source slice is stale")
                if "[[PT2-" in occurrence.matched_source:
                    raise TerminologyError("term occurrence cannot cover a placeholder")
                spans = occupied.setdefault(unit.unit_key, [])
                if any(
                    occurrence.source_start < end and start < occurrence.source_end
                    for start, end, _key in spans
                ):
                    raise TerminologyError("active term occurrences overlap")
                spans.append(
                    (
                        occurrence.source_start,
                        occurrence.source_end,
                        occurrence.occurrence_key,
                    )
                )
                directives.append(
                    TermDirective.create(
                        term=term,
                        occurrence=occurrence,
                        first_for_term=index == 0,
                    )
                )

        directives_tuple = tuple(
            sorted(
                directives,
                key=lambda item: (
                    unit_order[item.unit_key],
                    next(
                        occurrence.source_start
                        for term in terms
                        for occurrence in term.occurrences
                        if occurrence.occurrence_key == item.occurrence_key
                    ),
                    item.occurrence_key,
                ),
            )
        )
        payload = {
            "project_key": document.project.project_key,
            "manifest_sha256": document.manifest_sha256,
            "terms": [item.as_payload() for item in terms],
            "directives": [item.as_payload() for item in directives_tuple],
        }
        return cls(
            snapshot_id=digest(TERMINOLOGY_SNAPSHOT_NAMESPACE, payload),
            project_key=document.project.project_key,
            manifest_sha256=document.manifest_sha256,
            terms=terms,
            directives=directives_tuple,
        )

    def __post_init__(self) -> None:
        for name in ("snapshot_id", "project_key", "manifest_sha256"):
            require_sha256(name, getattr(self, name))
        term_keys = [item.term_key for item in self.terms]
        if term_keys != sorted(term_keys) or len(term_keys) != len(set(term_keys)):
            raise TerminologyError("terminology terms are not canonical and unique")
        revision_ids = {item.revision_id for item in self.terms}
        occurrence_keys = {
            occurrence.occurrence_key
            for term in self.terms
            for occurrence in term.occurrences
        }
        directive_occurrences = [item.occurrence_key for item in self.directives]
        if set(directive_occurrences) != occurrence_keys or len(
            directive_occurrences
        ) != len(set(directive_occurrences)):
            raise TerminologyError("terminology directives do not cover occurrences")
        if any(item.term_revision_id not in revision_ids for item in self.directives):
            raise TerminologyError("directive references an unknown term revision")
        if self.snapshot_id != digest(
            TERMINOLOGY_SNAPSHOT_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("terminology snapshot id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "project_key": self.project_key,
            "manifest_sha256": self.manifest_sha256,
            "terms": [item.as_payload() for item in self.terms],
            "directives": [item.as_payload() for item in self.directives],
        }

    def as_payload(self) -> dict[str, object]:
        return {"snapshot_id": self.snapshot_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TerminologySnapshot":
        raw_terms = payload["terms"]
        raw_directives = payload["directives"]
        if (
            not isinstance(raw_terms, list)
            or not isinstance(raw_directives, list)
            or any(not isinstance(item, dict) for item in raw_terms)
            or any(not isinstance(item, dict) for item in raw_directives)
        ):
            raise ValueError("terminology snapshot payload is malformed")
        return cls(
            snapshot_id=str(payload["snapshot_id"]),
            project_key=str(payload["project_key"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            terms=tuple(TermRevision.from_payload(item) for item in raw_terms),
            directives=tuple(
                TermDirective.from_payload(item) for item in raw_directives
            ),
        )

    def validate_against(self, document: PreparedDocument) -> None:
        rebuilt = TerminologySnapshot.create(document, self.terms)
        if rebuilt.as_payload() != self.as_payload():
            raise TerminologyError("terminology snapshot is stale or non-canonical")

    def directives_for_unit(self, unit_key: str) -> tuple[TermDirective, ...]:
        return tuple(item for item in self.directives if item.unit_key == unit_key)


@dataclass(frozen=True, slots=True)
class TermApplication:
    application_id: str
    occurrence_key: str
    target_start: int
    target_end: int
    rendered_text: str

    @classmethod
    def create(
        cls,
        *,
        occurrence_key: str,
        target_text: str,
        target_start: int,
        target_end: int,
    ) -> "TermApplication":
        require_sha256("occurrence_key", occurrence_key)
        target_text = normalize_text(target_text)
        if not 0 <= target_start < target_end <= len(target_text):
            raise TerminologyError("term application target span is outside the target")
        rendered_text = target_text[target_start:target_end]
        payload = {
            "occurrence_key": occurrence_key,
            "target_start": target_start,
            "target_end": target_end,
            "rendered_text": rendered_text,
        }
        return cls(
            application_id=digest(TERM_APPLICATION_NAMESPACE, payload),
            **payload,
        )

    def __post_init__(self) -> None:
        require_sha256("application_id", self.application_id)
        require_sha256("occurrence_key", self.occurrence_key)
        if self.rendered_text != normalize_text(self.rendered_text):
            raise ValueError("rendered term text is not canonical")
        if not self.rendered_text.strip():
            raise TerminologyError("term application cannot be blank")
        if self.target_start < 0 or self.target_end <= self.target_start:
            raise ValueError("term application target span is invalid")
        if self.application_id != digest(
            TERM_APPLICATION_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("term application id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "occurrence_key": self.occurrence_key,
            "target_start": self.target_start,
            "target_end": self.target_end,
            "rendered_text": self.rendered_text,
        }

    def as_payload(self) -> dict[str, object]:
        return {"application_id": self.application_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "TermApplication":
        return cls(
            application_id=str(payload["application_id"]),
            occurrence_key=str(payload["occurrence_key"]),
            target_start=int(payload["target_start"]),
            target_end=int(payload["target_end"]),
            rendered_text=str(payload["rendered_text"]),
        )


@dataclass(frozen=True, slots=True)
class RenderedTarget:
    rendered_target_id: str
    unit_key: str
    unit_revision: str
    terminology_snapshot_id: str
    target_text: str
    target_sha256: str
    term_applications: tuple[TermApplication, ...]

    @classmethod
    def create(
        cls,
        *,
        unit: PreparedUnit,
        terminology: TerminologySnapshot,
        target_text: str,
        term_applications: tuple[TermApplication, ...] | list[TermApplication],
    ) -> "RenderedTarget":
        target_text = normalize_text(target_text)
        unit.placeholders.validate(target_text, require_nonempty_styles=True)
        applications = tuple(
            sorted(
                term_applications,
                key=lambda item: (
                    item.target_start,
                    item.target_end,
                    item.occurrence_key,
                ),
            )
        )
        payload = {
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "terminology_snapshot_id": terminology.snapshot_id,
            "target_text": target_text,
            "target_sha256": sha256_text(target_text),
            "term_applications": [item.as_payload() for item in applications],
        }
        result = cls(
            rendered_target_id=digest(RENDERED_TARGET_NAMESPACE, payload),
            unit_key=unit.unit_key,
            unit_revision=unit.unit_revision,
            terminology_snapshot_id=terminology.snapshot_id,
            target_text=target_text,
            target_sha256=sha256_text(target_text),
            term_applications=applications,
        )
        result.validate(unit=unit, terminology=terminology)
        return result

    def __post_init__(self) -> None:
        for name in (
            "rendered_target_id",
            "unit_key",
            "unit_revision",
            "terminology_snapshot_id",
            "target_sha256",
        ):
            require_sha256(name, getattr(self, name))
        if self.target_text != normalize_text(self.target_text):
            raise ValueError("target text is not canonical")
        if not self.target_text.strip():
            raise ValueError("target text must not be blank")
        if self.target_sha256 != sha256_text(self.target_text):
            raise IdentityError("rendered target digest mismatch")
        expected_order = tuple(
            sorted(
                self.term_applications,
                key=lambda item: (
                    item.target_start,
                    item.target_end,
                    item.occurrence_key,
                ),
            )
        )
        if self.term_applications != expected_order:
            raise ValueError("term applications are not in target order")
        if self.rendered_target_id != digest(
            RENDERED_TARGET_NAMESPACE,
            self._identity_payload(),
        ):
            raise IdentityError("rendered target id mismatch")

    def validate(
        self,
        *,
        unit: PreparedUnit,
        terminology: TerminologySnapshot,
    ) -> None:
        if self.unit_key != unit.unit_key or self.unit_revision != unit.unit_revision:
            raise TerminologyError("rendered target belongs to another unit revision")
        if self.terminology_snapshot_id != terminology.snapshot_id:
            raise TerminologyError("rendered target uses another terminology snapshot")
        unit.placeholders.validate(self.target_text, require_nonempty_styles=True)
        directives = {
            item.occurrence_key: item
            for item in terminology.directives_for_unit(unit.unit_key)
        }
        applications = {item.occurrence_key: item for item in self.term_applications}
        if len(applications) != len(self.term_applications):
            raise TerminologyError("duplicate term application")
        if set(applications) != set(directives):
            raise TerminologyError(
                "term application coverage mismatch; "
                f"missing={sorted(set(directives) - set(applications))}, "
                f"extra={sorted(set(applications) - set(directives))}"
            )
        spans: list[tuple[int, int]] = []
        for occurrence_key, application in applications.items():
            if application.target_end > len(self.target_text):
                raise TerminologyError("term application exceeds target text")
            if (
                self.target_text[application.target_start : application.target_end]
                != application.rendered_text
            ):
                raise TerminologyError("term application target slice is stale")
            if application.rendered_text != directives[occurrence_key].required_rendering:
                raise TerminologyError("term application uses the wrong rendering")
            if any(
                application.target_start < end and start < application.target_end
                for start, end in spans
            ):
                raise TerminologyError("term applications overlap in target text")
            spans.append((application.target_start, application.target_end))

    def _identity_payload(self) -> dict[str, object]:
        return {
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "terminology_snapshot_id": self.terminology_snapshot_id,
            "target_text": self.target_text,
            "target_sha256": self.target_sha256,
            "term_applications": [
                item.as_payload() for item in self.term_applications
            ],
        }

    def as_payload(self) -> dict[str, object]:
        return {"rendered_target_id": self.rendered_target_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "RenderedTarget":
        raw_applications = payload["term_applications"]
        if not isinstance(raw_applications, list) or any(
            not isinstance(item, dict) for item in raw_applications
        ):
            raise ValueError("term application payload is malformed")
        return cls(
            rendered_target_id=str(payload["rendered_target_id"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            terminology_snapshot_id=str(payload["terminology_snapshot_id"]),
            target_text=str(payload["target_text"]),
            target_sha256=str(payload["target_sha256"]),
            term_applications=tuple(
                TermApplication.from_payload(item) for item in raw_applications
            ),
        )
