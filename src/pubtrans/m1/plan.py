"""Immutable actor, context, and whole-document M1 plan values."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.errors import IdentityError
from pubtrans.m0v2.model import ParagraphRecord
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import UnitLocator

from .errors import PlanBindingError
from .terminology import TerminologySnapshot


ACTOR_NAMESPACE = "pubtrans.actor-profile/v1"
LANE_NAMESPACE = "pubtrans.translation-lane/v1"
ROUTE_NAMESPACE = "pubtrans.translation-route/v1"
CONTEXT_POLICY_NAMESPACE = "pubtrans.context-policy/v1"
SOURCE_BRIEF_NAMESPACE = "pubtrans.source-brief/v1"
CONTEXT_NAMESPACE = "pubtrans.context-package/v1"
PLAN_NAMESPACE = "pubtrans.kernel-plan/v1"


def _nonempty(name: str, value: str) -> str:
    result = normalize_text(value).strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _reject_secret_keys(value: object, path: str = "settings") -> None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key).lower().replace("-", "_")
            if any(
                marker in key
                for marker in ("api_key", "token", "secret", "password", "credential")
            ):
                raise ValueError(f"actor {path} must not persist secret key {raw_key!r}")
            _reject_secret_keys(item, f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")


class ActorRole(str, Enum):
    DOCUMENT_ANALYST = "DOCUMENT_ANALYST"
    TERMINOLOGY_RESEARCHER = "TERMINOLOGY_RESEARCHER"
    TRANSLATOR = "TRANSLATOR"
    BILINGUAL_REVIEWER = "BILINGUAL_REVIEWER"
    ADJUDICATOR = "ADJUDICATOR"
    CHINESE_EDITOR = "CHINESE_EDITOR"
    FINAL_VERIFIER = "FINAL_VERIFIER"
    GLOBAL_REVIEWER = "GLOBAL_REVIEWER"


class RiskLevel(str, Enum):
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


@dataclass(frozen=True, slots=True)
class ActorSpec:
    actor_key: str
    role: ActorRole
    provider: str
    model: str
    prompt_revision: str
    settings_json: str

    @classmethod
    def create(
        cls,
        *,
        role: ActorRole,
        provider: str,
        model: str,
        prompt_revision: str,
        settings: dict[str, object] | None = None,
    ) -> "ActorSpec":
        provider = _nonempty("provider", provider)
        model = _nonempty("model", model)
        prompt_revision = _nonempty("prompt_revision", prompt_revision)
        settings = settings or {}
        _reject_secret_keys(settings)
        settings_json = canonical_json(settings)
        payload = {
            "role": role.value,
            "provider": provider,
            "model": model,
            "prompt_revision": prompt_revision,
            "settings_json": settings_json,
        }
        return cls(
            actor_key=digest(ACTOR_NAMESPACE, payload),
            role=role,
            provider=provider,
            model=model,
            prompt_revision=prompt_revision,
            settings_json=settings_json,
        )

    def __post_init__(self) -> None:
        require_sha256("actor_key", self.actor_key)
        for name in ("provider", "model", "prompt_revision"):
            if getattr(self, name) != _nonempty(name, getattr(self, name)):
                raise ValueError(f"actor {name} is not canonical")
        try:
            settings = json.loads(self.settings_json)
        except json.JSONDecodeError as exc:
            raise ValueError("actor settings_json is not valid JSON") from exc
        if not isinstance(settings, dict):
            raise ValueError("actor settings must be a JSON object")
        _reject_secret_keys(settings)
        if self.settings_json != canonical_json(settings):
            raise ValueError("actor settings JSON is not canonical")
        if self.actor_key != digest(ACTOR_NAMESPACE, self._identity_payload()):
            raise IdentityError("actor key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "role": self.role.value,
            "provider": self.provider,
            "model": self.model,
            "prompt_revision": self.prompt_revision,
            "settings_json": self.settings_json,
        }

    def as_payload(self) -> dict[str, object]:
        return {"actor_key": self.actor_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ActorSpec":
        return cls(
            actor_key=str(payload["actor_key"]),
            role=ActorRole(str(payload["role"])),
            provider=str(payload["provider"]),
            model=str(payload["model"]),
            prompt_revision=str(payload["prompt_revision"]),
            settings_json=str(payload["settings_json"]),
        )


@dataclass(frozen=True, slots=True)
class LaneSpec:
    lane_key: str
    label: str
    actor: ActorSpec

    @classmethod
    def create(cls, *, label: str, actor: ActorSpec) -> "LaneSpec":
        label = _nonempty("lane label", label)
        if actor.role is not ActorRole.TRANSLATOR:
            raise ValueError("translation lane requires a TRANSLATOR actor")
        payload = {"label": label, "actor": actor.as_payload()}
        return cls(lane_key=digest(LANE_NAMESPACE, payload), label=label, actor=actor)

    def __post_init__(self) -> None:
        require_sha256("lane_key", self.lane_key)
        if self.label != _nonempty("lane label", self.label):
            raise ValueError("lane label is not canonical")
        if self.actor.role is not ActorRole.TRANSLATOR:
            raise ValueError("lane actor is not a translator")
        if self.lane_key != digest(LANE_NAMESPACE, self._identity_payload()):
            raise IdentityError("lane key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {"label": self.label, "actor": self.actor.as_payload()}

    def as_payload(self) -> dict[str, object]:
        return {"lane_key": self.lane_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "LaneSpec":
        actor = payload["actor"]
        if not isinstance(actor, dict):
            raise ValueError("lane actor payload is malformed")
        return cls(
            lane_key=str(payload["lane_key"]),
            label=str(payload["label"]),
            actor=ActorSpec.from_payload(actor),
        )


@dataclass(frozen=True, slots=True)
class UnitRoute:
    """Risk-adaptive candidate route for one immutable source unit."""

    route_key: str
    unit_key: str
    unit_revision: str
    risk_level: RiskLevel
    lane_keys: tuple[str, ...]
    reasons: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        unit_key: str,
        unit_revision: str,
        risk_level: RiskLevel,
        lanes: tuple[LaneSpec, ...] | list[LaneSpec],
        reasons: tuple[str, ...] | list[str] = (),
    ) -> "UnitRoute":
        lanes = tuple(lanes)
        lane_keys = tuple(lane.lane_key for lane in lanes)
        reasons = tuple(sorted({_nonempty("route reason", item) for item in reasons}))
        payload = {
            "unit_key": unit_key,
            "unit_revision": unit_revision,
            "risk_level": risk_level.value,
            "lane_keys": list(lane_keys),
            "reasons": list(reasons),
        }
        return cls(
            route_key=digest(ROUTE_NAMESPACE, payload),
            unit_key=unit_key,
            unit_revision=unit_revision,
            risk_level=risk_level,
            lane_keys=lane_keys,
            reasons=reasons,
        )

    def __post_init__(self) -> None:
        for name in ("route_key", "unit_key", "unit_revision"):
            require_sha256(name, getattr(self, name))
        if not self.lane_keys or len(self.lane_keys) > 3:
            raise PlanBindingError("a unit route requires one to three candidates")
        if len(self.lane_keys) != len(set(self.lane_keys)):
            raise PlanBindingError("a unit route cannot repeat a translation lane")
        for lane_key in self.lane_keys:
            require_sha256("lane_key", lane_key)
        if self.reasons != tuple(sorted(set(self.reasons))):
            raise ValueError("route reasons are not canonical and unique")
        if self.risk_level is RiskLevel.R1 and len(self.lane_keys) != 1:
            raise PlanBindingError("R1 units require exactly one baseline candidate")
        if self.risk_level is RiskLevel.R3 and len(self.lane_keys) < 2:
            raise PlanBindingError("R3 units require at least two isolated candidates")
        if self.risk_level is not RiskLevel.R1 and not self.reasons:
            raise PlanBindingError("R2/R3 routes require explicit risk reasons")
        if self.route_key != digest(ROUTE_NAMESPACE, self._identity_payload()):
            raise IdentityError("unit route key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "risk_level": self.risk_level.value,
            "lane_keys": list(self.lane_keys),
            "reasons": list(self.reasons),
        }

    def as_payload(self) -> dict[str, object]:
        return {"route_key": self.route_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "UnitRoute":
        raw_lanes = payload["lane_keys"]
        raw_reasons = payload["reasons"]
        if not isinstance(raw_lanes, list) or not isinstance(raw_reasons, list):
            raise ValueError("unit route payload is malformed")
        return cls(
            route_key=str(payload["route_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            risk_level=RiskLevel(str(payload["risk_level"])),
            lane_keys=tuple(str(item) for item in raw_lanes),
            reasons=tuple(str(item) for item in raw_reasons),
        )


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    policy_key: str
    before_records: int
    after_records: int
    max_neighbor_characters: int

    @classmethod
    def create(
        cls,
        *,
        before_records: int = 4,
        after_records: int = 4,
        max_neighbor_characters: int = 6000,
    ) -> "ContextPolicy":
        payload = {
            "before_records": int(before_records),
            "after_records": int(after_records),
            "max_neighbor_characters": int(max_neighbor_characters),
        }
        return cls(policy_key=digest(CONTEXT_POLICY_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        require_sha256("policy_key", self.policy_key)
        if self.before_records < 0 or self.after_records < 0:
            raise ValueError("context record limits must be non-negative")
        if self.max_neighbor_characters < 0:
            raise ValueError("context character limit must be non-negative")
        if self.policy_key != digest(CONTEXT_POLICY_NAMESPACE, self._identity_payload()):
            raise IdentityError("context policy key mismatch")

    def _identity_payload(self) -> dict[str, int]:
        return {
            "before_records": self.before_records,
            "after_records": self.after_records,
            "max_neighbor_characters": self.max_neighbor_characters,
        }

    def as_payload(self) -> dict[str, object]:
        return {"policy_key": self.policy_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ContextPolicy":
        return cls(
            policy_key=str(payload["policy_key"]),
            before_records=int(payload["before_records"]),
            after_records=int(payload["after_records"]),
            max_neighbor_characters=int(payload["max_neighbor_characters"]),
        )


@dataclass(frozen=True, slots=True)
class SourceBrief:
    brief_id: str
    project_key: str
    snapshot_key: str
    brief_text: str
    origin: str

    @classmethod
    def create(
        cls,
        *,
        document: PreparedDocument,
        brief_text: str,
        origin: str,
    ) -> "SourceBrief":
        brief_text = _nonempty("source brief", brief_text)
        origin = _nonempty("source brief origin", origin)
        payload = {
            "project_key": document.project.project_key,
            "snapshot_key": document.snapshot.snapshot_key,
            "brief_text": brief_text,
            "origin": origin,
        }
        return cls(brief_id=digest(SOURCE_BRIEF_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        for name in ("brief_id", "project_key", "snapshot_key"):
            require_sha256(name, getattr(self, name))
        if self.brief_text != _nonempty("source brief", self.brief_text):
            raise ValueError("source brief is not canonical")
        if self.origin != _nonempty("source brief origin", self.origin):
            raise ValueError("source brief origin is not canonical")
        if self.brief_id != digest(SOURCE_BRIEF_NAMESPACE, self._identity_payload()):
            raise IdentityError("source brief id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "project_key": self.project_key,
            "snapshot_key": self.snapshot_key,
            "brief_text": self.brief_text,
            "origin": self.origin,
        }

    def as_payload(self) -> dict[str, object]:
        return {"brief_id": self.brief_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SourceBrief":
        return cls(
            brief_id=str(payload["brief_id"]),
            project_key=str(payload["project_key"]),
            snapshot_key=str(payload["snapshot_key"]),
            brief_text=str(payload["brief_text"]),
            origin=str(payload["origin"]),
        )


@dataclass(frozen=True, slots=True)
class ContextFragment:
    record_key: str
    record_revision: str
    locator: UnitLocator
    source_text: str
    disposition: str
    reason: str
    layout_label: str | None

    @classmethod
    def from_record(cls, record: ParagraphRecord) -> "ContextFragment":
        return cls(
            record_key=record.record_key,
            record_revision=record.record_revision,
            locator=record.locator,
            source_text=record.source_text,
            disposition=record.disposition.value,
            reason=record.reason.value,
            layout_label=record.layout_label,
        )

    def __post_init__(self) -> None:
        require_sha256("record_key", self.record_key)
        require_sha256("record_revision", self.record_revision)
        if self.source_text != normalize_text(self.source_text):
            raise ValueError("context source text is not canonical")
        if not self.disposition or not self.reason:
            raise ValueError("context classification must not be empty")

    def as_payload(self) -> dict[str, object]:
        return {
            "record_key": self.record_key,
            "record_revision": self.record_revision,
            "locator": self.locator.as_payload(),
            "source_text": self.source_text,
            "disposition": self.disposition,
            "reason": self.reason,
            "layout_label": self.layout_label,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ContextFragment":
        locator = payload["locator"]
        if not isinstance(locator, dict):
            raise ValueError("context locator payload is malformed")
        return cls(
            record_key=str(payload["record_key"]),
            record_revision=str(payload["record_revision"]),
            locator=UnitLocator.from_payload(locator),
            source_text=str(payload["source_text"]),
            disposition=str(payload["disposition"]),
            reason=str(payload["reason"]),
            layout_label=(
                str(payload["layout_label"])
                if payload.get("layout_label") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class ContextPackage:
    context_key: str
    plan_key: str
    unit_key: str
    unit_revision: str
    current: ContextFragment
    before: tuple[ContextFragment, ...]
    after: tuple[ContextFragment, ...]
    relevant_directive_ids: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        plan_key: str,
        unit_key: str,
        unit_revision: str,
        current: ContextFragment,
        before: tuple[ContextFragment, ...] | list[ContextFragment],
        after: tuple[ContextFragment, ...] | list[ContextFragment],
        relevant_directive_ids: tuple[str, ...] | list[str],
    ) -> "ContextPackage":
        before = tuple(before)
        after = tuple(after)
        relevant_directive_ids = tuple(sorted(relevant_directive_ids))
        payload = {
            "plan_key": plan_key,
            "unit_key": unit_key,
            "unit_revision": unit_revision,
            "current": current.as_payload(),
            "before": [item.as_payload() for item in before],
            "after": [item.as_payload() for item in after],
            "relevant_directive_ids": list(relevant_directive_ids),
        }
        return cls(
            context_key=digest(CONTEXT_NAMESPACE, payload),
            plan_key=plan_key,
            unit_key=unit_key,
            unit_revision=unit_revision,
            current=current,
            before=before,
            after=after,
            relevant_directive_ids=relevant_directive_ids,
        )

    def __post_init__(self) -> None:
        for name in ("context_key", "plan_key", "unit_key", "unit_revision"):
            require_sha256(name, getattr(self, name))
        for directive_id in self.relevant_directive_ids:
            require_sha256("directive_id", directive_id)
        if self.relevant_directive_ids != tuple(sorted(self.relevant_directive_ids)):
            raise ValueError("context directives are not in canonical order")
        record_keys = [item.record_key for item in (*self.before, self.current, *self.after)]
        if len(record_keys) != len(set(record_keys)):
            raise ValueError("context contains duplicate paragraph records")
        if tuple(item.locator for item in self.before) != tuple(
            sorted(item.locator for item in self.before)
        ):
            raise ValueError("preceding context is not in source order")
        if tuple(item.locator for item in self.after) != tuple(
            sorted(item.locator for item in self.after)
        ):
            raise ValueError("following context is not in source order")
        if self.context_key != digest(CONTEXT_NAMESPACE, self._identity_payload()):
            raise IdentityError("context package key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "plan_key": self.plan_key,
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "current": self.current.as_payload(),
            "before": [item.as_payload() for item in self.before],
            "after": [item.as_payload() for item in self.after],
            "relevant_directive_ids": list(self.relevant_directive_ids),
        }

    def as_payload(self) -> dict[str, object]:
        return {"context_key": self.context_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ContextPackage":
        current = payload["current"]
        before = payload["before"]
        after = payload["after"]
        directives = payload["relevant_directive_ids"]
        if (
            not isinstance(current, dict)
            or not isinstance(before, list)
            or not isinstance(after, list)
            or not isinstance(directives, list)
            or any(not isinstance(item, dict) for item in (*before, *after))
        ):
            raise ValueError("context package payload is malformed")
        return cls(
            context_key=str(payload["context_key"]),
            plan_key=str(payload["plan_key"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            current=ContextFragment.from_payload(current),
            before=tuple(ContextFragment.from_payload(item) for item in before),
            after=tuple(ContextFragment.from_payload(item) for item in after),
            relevant_directive_ids=tuple(str(item) for item in directives),
        )


@dataclass(frozen=True, slots=True)
class KernelPlan:
    plan_key: str
    project_key: str
    snapshot_key: str
    manifest_sha256: str
    unit_revisions: tuple[tuple[str, str], ...]
    terminology_snapshot_id: str
    context_policy: ContextPolicy
    source_brief: SourceBrief | None
    lanes: tuple[LaneSpec, ...]
    routes: tuple[UnitRoute, ...]
    reviewer: ActorSpec
    adjudicator: ActorSpec
    editor: ActorSpec
    verifier: ActorSpec
    global_reviewer: ActorSpec

    @classmethod
    def create(
        cls,
        *,
        document: PreparedDocument,
        terminology: TerminologySnapshot,
        context_policy: ContextPolicy,
        source_brief: SourceBrief | None,
        lanes: tuple[LaneSpec, ...] | list[LaneSpec],
        routes: tuple[UnitRoute, ...] | list[UnitRoute],
        reviewer: ActorSpec,
        adjudicator: ActorSpec,
        editor: ActorSpec,
        verifier: ActorSpec,
        global_reviewer: ActorSpec,
    ) -> "KernelPlan":
        document.require_unblocked()
        terminology.validate_against(document)
        unit_revisions = tuple(
            (unit.unit_key, unit.unit_revision) for unit in document.units
        )
        lanes = tuple(lanes)
        routes = tuple(routes)
        payload = {
            "project_key": document.project.project_key,
            "snapshot_key": document.snapshot.snapshot_key,
            "manifest_sha256": document.manifest_sha256,
            "unit_revisions": [list(item) for item in unit_revisions],
            "terminology_snapshot_id": terminology.snapshot_id,
            "context_policy": context_policy.as_payload(),
            "source_brief": source_brief.as_payload() if source_brief else None,
            "lanes": [item.as_payload() for item in lanes],
            "routes": [item.as_payload() for item in routes],
            "reviewer": reviewer.as_payload(),
            "adjudicator": adjudicator.as_payload(),
            "editor": editor.as_payload(),
            "verifier": verifier.as_payload(),
            "global_reviewer": global_reviewer.as_payload(),
        }
        return cls(
            plan_key=digest(PLAN_NAMESPACE, payload),
            project_key=document.project.project_key,
            snapshot_key=document.snapshot.snapshot_key,
            manifest_sha256=document.manifest_sha256,
            unit_revisions=unit_revisions,
            terminology_snapshot_id=terminology.snapshot_id,
            context_policy=context_policy,
            source_brief=source_brief,
            lanes=lanes,
            routes=routes,
            reviewer=reviewer,
            adjudicator=adjudicator,
            editor=editor,
            verifier=verifier,
            global_reviewer=global_reviewer,
        )

    def __post_init__(self) -> None:
        for name in (
            "plan_key",
            "project_key",
            "snapshot_key",
            "manifest_sha256",
            "terminology_snapshot_id",
        ):
            require_sha256(name, getattr(self, name))
        if not 1 <= len(self.lanes) <= 3:
            raise PlanBindingError("publication plan requires one to three lanes")
        lane_keys = [item.lane_key for item in self.lanes]
        labels = [item.label for item in self.lanes]
        translator_actor_keys = [item.actor.actor_key for item in self.lanes]
        if (
            len(lane_keys) != len(set(lane_keys))
            or len(labels) != len(set(labels))
            or len(translator_actor_keys) != len(set(translator_actor_keys))
        ):
            raise PlanBindingError(
                "translation lanes, labels, and actor profiles must be unique"
            )
        expected_roles = (
            (self.reviewer, ActorRole.BILINGUAL_REVIEWER),
            (self.adjudicator, ActorRole.ADJUDICATOR),
            (self.editor, ActorRole.CHINESE_EDITOR),
            (self.verifier, ActorRole.FINAL_VERIFIER),
            (self.global_reviewer, ActorRole.GLOBAL_REVIEWER),
        )
        for actor, role in expected_roles:
            if actor.role is not role:
                raise PlanBindingError(f"{role.value} actor has the wrong role")
        downstream_keys = [actor.actor_key for actor, _role in expected_roles]
        if len(downstream_keys) != len(set(downstream_keys)):
            raise PlanBindingError("review-bus actor profiles must be distinct")
        translator_keys = {lane.actor.actor_key for lane in self.lanes}
        if translator_keys.intersection(downstream_keys):
            raise PlanBindingError("review-bus actor cannot be a translation actor")
        unit_keys = [item[0] for item in self.unit_revisions]
        if not unit_keys or len(unit_keys) != len(set(unit_keys)):
            raise PlanBindingError("kernel plan requires a unique non-empty unit set")
        for unit_key, unit_revision in self.unit_revisions:
            require_sha256("unit_key", unit_key)
            require_sha256("unit_revision", unit_revision)
        route_by_unit = {route.unit_key: route for route in self.routes}
        if len(route_by_unit) != len(self.routes) or set(route_by_unit) != set(unit_keys):
            raise PlanBindingError("unit routes must cover the exact kernel unit set")
        revision_by_unit = dict(self.unit_revisions)
        lane_key_set = set(lane_keys)
        for route in self.routes:
            if route.unit_revision != revision_by_unit[route.unit_key]:
                raise PlanBindingError("unit route references a stale source revision")
            if not set(route.lane_keys).issubset(lane_key_set):
                raise PlanBindingError("unit route references a lane outside the plan")
        if self.source_brief is not None:
            if (
                self.source_brief.project_key != self.project_key
                or self.source_brief.snapshot_key != self.snapshot_key
            ):
                raise PlanBindingError("source brief belongs to another snapshot")
        if self.plan_key != digest(PLAN_NAMESPACE, self._identity_payload()):
            raise IdentityError("kernel plan key mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "project_key": self.project_key,
            "snapshot_key": self.snapshot_key,
            "manifest_sha256": self.manifest_sha256,
            "unit_revisions": [list(item) for item in self.unit_revisions],
            "terminology_snapshot_id": self.terminology_snapshot_id,
            "context_policy": self.context_policy.as_payload(),
            "source_brief": self.source_brief.as_payload() if self.source_brief else None,
            "lanes": [item.as_payload() for item in self.lanes],
            "routes": [item.as_payload() for item in self.routes],
            "reviewer": self.reviewer.as_payload(),
            "adjudicator": self.adjudicator.as_payload(),
            "editor": self.editor.as_payload(),
            "verifier": self.verifier.as_payload(),
            "global_reviewer": self.global_reviewer.as_payload(),
        }

    def as_payload(self) -> dict[str, object]:
        return {"plan_key": self.plan_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "KernelPlan":
        raw_units = payload["unit_revisions"]
        raw_lanes = payload["lanes"]
        raw_routes = payload["routes"]
        raw_policy = payload["context_policy"]
        raw_brief = payload.get("source_brief")
        raw_actors = [
            payload["reviewer"],
            payload["adjudicator"],
            payload["editor"],
            payload["verifier"],
            payload["global_reviewer"],
        ]
        if (
            not isinstance(raw_units, list)
            or not isinstance(raw_lanes, list)
            or not isinstance(raw_routes, list)
            or not isinstance(raw_policy, dict)
            or any(not isinstance(item, list) or len(item) != 2 for item in raw_units)
            or any(not isinstance(item, dict) for item in raw_lanes)
            or any(not isinstance(item, dict) for item in raw_routes)
            or any(not isinstance(item, dict) for item in raw_actors)
            or (raw_brief is not None and not isinstance(raw_brief, dict))
        ):
            raise ValueError("kernel plan payload is malformed")
        return cls(
            plan_key=str(payload["plan_key"]),
            project_key=str(payload["project_key"]),
            snapshot_key=str(payload["snapshot_key"]),
            manifest_sha256=str(payload["manifest_sha256"]),
            unit_revisions=tuple((str(item[0]), str(item[1])) for item in raw_units),
            terminology_snapshot_id=str(payload["terminology_snapshot_id"]),
            context_policy=ContextPolicy.from_payload(raw_policy),
            source_brief=(
                SourceBrief.from_payload(raw_brief)
                if isinstance(raw_brief, dict)
                else None
            ),
            lanes=tuple(LaneSpec.from_payload(item) for item in raw_lanes),
            routes=tuple(UnitRoute.from_payload(item) for item in raw_routes),
            reviewer=ActorSpec.from_payload(raw_actors[0]),
            adjudicator=ActorSpec.from_payload(raw_actors[1]),
            editor=ActorSpec.from_payload(raw_actors[2]),
            verifier=ActorSpec.from_payload(raw_actors[3]),
            global_reviewer=ActorSpec.from_payload(raw_actors[4]),
        )

    def validate_against(
        self,
        *,
        document: PreparedDocument,
        terminology: TerminologySnapshot,
    ) -> None:
        rebuilt = KernelPlan.create(
            document=document,
            terminology=terminology,
            context_policy=self.context_policy,
            source_brief=self.source_brief,
            lanes=self.lanes,
            routes=self.routes,
            reviewer=self.reviewer,
            adjudicator=self.adjudicator,
            editor=self.editor,
            verifier=self.verifier,
            global_reviewer=self.global_reviewer,
        )
        if rebuilt.as_payload() != self.as_payload():
            raise PlanBindingError("kernel plan is stale or belongs to another document")
