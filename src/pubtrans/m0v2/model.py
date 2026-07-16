"""Immutable M0 v2 project, snapshot, unit, and approval values."""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .canonical import canonical_json
from .canonical import digest
from .canonical import normalize_text
from .canonical import quantize_point
from .canonical import require_sha256
from .canonical import sha256_text
from .errors import DocumentBlockedError
from .errors import IdentityError
from .errors import PlaceholderContractError


PROJECT_NAMESPACE = "pubtrans.project/v2"
SNAPSHOT_NAMESPACE = "pubtrans.prepared-snapshot/v2"
UNIT_NAMESPACE = "pubtrans.unit/v2"
UNIT_REVISION_NAMESPACE = "pubtrans.unit-revision/v2"
RECORD_NAMESPACE = "pubtrans.paragraph-record/v2"
MANIFEST_NAMESPACE = "pubtrans.prepared-document/v2"
PLACEHOLDER_NAMESPACE = "pubtrans.placeholder-contract/v2"
APPROVAL_NAMESPACE = "pubtrans.approval/v2"

PLACEHOLDER_NAMESPACE_RE = re.compile(r"^PT2-[0-9a-f]{12}$")


def _language_tag(value: str) -> str:
    result = value.strip().lower()
    if not result or any(character.isspace() for character in result):
        raise ValueError("language tag must be a non-empty BCP-47-like token")
    return result


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    project_key: str
    original_pdf_sha256: str
    source_language: str
    target_language: str
    profile_name: str

    @classmethod
    def create(
        cls,
        *,
        original_pdf_sha256: str,
        source_language: str,
        target_language: str,
        profile_name: str,
    ) -> "ProjectBinding":
        require_sha256("original_pdf_sha256", original_pdf_sha256)
        payload = {
            "original_pdf_sha256": original_pdf_sha256,
            "source_language": _language_tag(source_language),
            "target_language": _language_tag(target_language),
            "profile_name": profile_name.strip(),
        }
        if not payload["profile_name"]:
            raise ValueError("profile_name must not be empty")
        return cls(project_key=digest(PROJECT_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        require_sha256("project_key", self.project_key)
        require_sha256("original_pdf_sha256", self.original_pdf_sha256)
        if self.source_language != _language_tag(self.source_language):
            raise ValueError("source_language is not canonical")
        if self.target_language != _language_tag(self.target_language):
            raise ValueError("target_language is not canonical")
        if not self.profile_name.strip():
            raise ValueError("profile_name must not be empty")
        if self.project_key != digest(PROJECT_NAMESPACE, self._identity_payload()):
            raise IdentityError("project_key does not match project binding")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "original_pdf_sha256": self.original_pdf_sha256,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "profile_name": self.profile_name,
        }

    def as_payload(self) -> dict[str, object]:
        return {"project_key": self.project_key, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ProjectBinding":
        return cls(
            project_key=str(payload["project_key"]),
            original_pdf_sha256=str(payload["original_pdf_sha256"]),
            source_language=str(payload["source_language"]),
            target_language=str(payload["target_language"]),
            profile_name=str(payload["profile_name"]),
        )


@dataclass(frozen=True, slots=True)
class PreparedSnapshot:
    snapshot_key: str
    project_key: str
    prepared_pdf_sha256: str
    engine_name: str
    engine_version: str
    engine_commit: str
    extraction_profile_json: str
    extraction_profile_sha256: str
    part_key: str
    artifact_sha256: str

    @classmethod
    def create(
        cls,
        *,
        project: ProjectBinding,
        prepared_pdf_sha256: str,
        engine_name: str,
        engine_version: str,
        engine_commit: str,
        extraction_profile: dict[str, object],
        part_key: str,
        artifact_sha256: str,
    ) -> "PreparedSnapshot":
        require_sha256("prepared_pdf_sha256", prepared_pdf_sha256)
        require_sha256("artifact_sha256", artifact_sha256)
        profile_json = canonical_json(extraction_profile)
        profile_sha = digest(
            "pubtrans.extraction-profile/v2",
            json.loads(profile_json),
        )
        payload = {
            "project_key": project.project_key,
            "prepared_pdf_sha256": prepared_pdf_sha256,
            "engine_name": engine_name.strip(),
            "engine_version": engine_version.strip(),
            "engine_commit": engine_commit.strip().lower(),
            "extraction_profile_sha256": profile_sha,
            "part_key": part_key.strip(),
            "artifact_sha256": artifact_sha256,
        }
        for name in ("engine_name", "engine_version", "engine_commit", "part_key"):
            if not payload[name]:
                raise ValueError(f"{name} must not be empty")
        return cls(
            snapshot_key=digest(SNAPSHOT_NAMESPACE, payload),
            extraction_profile_json=profile_json,
            **payload,
        )

    def __post_init__(self) -> None:
        for name in (
            "snapshot_key",
            "project_key",
            "prepared_pdf_sha256",
            "extraction_profile_sha256",
            "artifact_sha256",
        ):
            require_sha256(name, getattr(self, name))
        if not all(
            value.strip()
            for value in (
                self.engine_name,
                self.engine_version,
                self.engine_commit,
                self.part_key,
            )
        ):
            raise ValueError("snapshot engine and part fields must not be empty")
        try:
            profile = json.loads(self.extraction_profile_json)
        except json.JSONDecodeError as exc:
            raise ValueError("extraction_profile_json is malformed") from exc
        if canonical_json(profile) != self.extraction_profile_json:
            raise ValueError("extraction_profile_json is not canonical")
        expected_profile = digest("pubtrans.extraction-profile/v2", profile)
        if expected_profile != self.extraction_profile_sha256:
            raise IdentityError("extraction profile digest mismatch")
        if self.snapshot_key != digest(SNAPSHOT_NAMESPACE, self._identity_payload()):
            raise IdentityError("snapshot_key does not match snapshot binding")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "project_key": self.project_key,
            "prepared_pdf_sha256": self.prepared_pdf_sha256,
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "engine_commit": self.engine_commit,
            "extraction_profile_sha256": self.extraction_profile_sha256,
            "part_key": self.part_key,
            "artifact_sha256": self.artifact_sha256,
        }

    def as_payload(self) -> dict[str, object]:
        return {
            "snapshot_key": self.snapshot_key,
            **self._identity_payload(),
            "extraction_profile": json.loads(self.extraction_profile_json),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PreparedSnapshot":
        profile = payload["extraction_profile"]
        return cls(
            snapshot_key=str(payload["snapshot_key"]),
            project_key=str(payload["project_key"]),
            prepared_pdf_sha256=str(payload["prepared_pdf_sha256"]),
            engine_name=str(payload["engine_name"]),
            engine_version=str(payload["engine_version"]),
            engine_commit=str(payload["engine_commit"]),
            extraction_profile_json=canonical_json(profile),
            extraction_profile_sha256=str(payload["extraction_profile_sha256"]),
            part_key=str(payload["part_key"]),
            artifact_sha256=str(payload["artifact_sha256"]),
        )


@dataclass(frozen=True, order=True, slots=True)
class UnitLocator:
    page_ordinal: int
    paragraph_ordinal: int

    def __post_init__(self) -> None:
        if self.page_ordinal < 0 or self.paragraph_ordinal < 0:
            raise ValueError("unit locator ordinals must be non-negative")

    def as_payload(self) -> dict[str, int]:
        return {
            "page_ordinal": self.page_ordinal,
            "paragraph_ordinal": self.paragraph_ordinal,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "UnitLocator":
        return cls(
            page_ordinal=int(payload["page_ordinal"]),
            paragraph_ordinal=int(payload["paragraph_ordinal"]),
        )


@dataclass(frozen=True, slots=True)
class BoxFingerprint:
    x0: str
    y0: str
    x1: str
    y1: str

    @classmethod
    def create(
        cls,
        x0: int | float | str,
        y0: int | float | str,
        x1: int | float | str,
        y1: int | float | str,
    ) -> "BoxFingerprint":
        result = cls(
            x0=quantize_point(x0),
            y0=quantize_point(y0),
            x1=quantize_point(x1),
            y1=quantize_point(y1),
        )
        if float(result.x1) < float(result.x0) or float(result.y1) < float(
            result.y0
        ):
            raise ValueError("PDF box coordinates are inverted")
        return result

    def __post_init__(self) -> None:
        expected = tuple(quantize_point(item) for item in self.as_tuple())
        if expected != self.as_tuple():
            raise ValueError("PDF box coordinates are not canonical")
        if float(self.x1) < float(self.x0) or float(self.y1) < float(self.y0):
            raise ValueError("PDF box coordinates are inverted")

    def as_tuple(self) -> tuple[str, str, str, str]:
        return self.x0, self.y0, self.x1, self.y1

    def as_payload(self) -> dict[str, str]:
        return dict(zip(("x0", "y0", "x1", "y1"), self.as_tuple(), strict=True))

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "BoxFingerprint":
        return cls(
            x0=str(payload["x0"]),
            y0=str(payload["y0"]),
            x1=str(payload["x1"]),
            y1=str(payload["y1"]),
        )


class PlaceholderKind(str, Enum):
    FORMULA = "formula"
    RICH_STYLE = "rich_style"


@dataclass(frozen=True, slots=True)
class PlaceholderSpec:
    kind: PlaceholderKind
    open_token: str
    close_token: str | None = None

    def __post_init__(self) -> None:
        if not self.open_token:
            raise ValueError("placeholder token must not be empty")
        if self.kind is PlaceholderKind.FORMULA and self.close_token is not None:
            raise ValueError("formula placeholder cannot have a close token")
        if self.kind is PlaceholderKind.RICH_STYLE:
            if not self.close_token or self.close_token == self.open_token:
                raise ValueError("rich style requires distinct open and close tokens")

    @property
    def tokens(self) -> tuple[str, ...]:
        if self.close_token is None:
            return (self.open_token,)
        return (self.open_token, self.close_token)

    def as_payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "open_token": self.open_token,
            "close_token": self.close_token,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PlaceholderSpec":
        return cls(
            kind=PlaceholderKind(str(payload["kind"])),
            open_token=str(payload["open_token"]),
            close_token=(
                str(payload["close_token"])
                if payload.get("close_token") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PlaceholderContract:
    namespace: str
    specs: tuple[PlaceholderSpec, ...]
    signature: str

    @classmethod
    def create(
        cls,
        namespace: str,
        specs: tuple[PlaceholderSpec, ...] | list[PlaceholderSpec] = (),
    ) -> "PlaceholderContract":
        specs = tuple(specs)
        payload = {
            "namespace": namespace,
            "specs": [spec.as_payload() for spec in specs],
        }
        return cls(
            namespace=namespace,
            specs=specs,
            signature=digest(PLACEHOLDER_NAMESPACE, payload),
        )

    def __post_init__(self) -> None:
        if PLACEHOLDER_NAMESPACE_RE.fullmatch(self.namespace) is None:
            raise ValueError("placeholder namespace must be PT2- plus 12 hex digits")
        tokens = self.tokens
        if len(tokens) != len(set(tokens)):
            raise ValueError("generated placeholder tokens must be unique")
        prefix = self.reserved_prefix
        for spec in self.specs:
            if spec.kind is PlaceholderKind.FORMULA:
                expected_patterns = (
                    re.compile(
                        rf"^\[\[{re.escape(self.namespace)}:F:\d{{4,}}\]\]$"
                    ),
                )
            else:
                expected_patterns = (
                    re.compile(
                        rf"^\[\[{re.escape(self.namespace)}:S:(\d{{4,}}):OPEN\]\]$"
                    ),
                    re.compile(
                        rf"^\[\[{re.escape(self.namespace)}:S:(\d{{4,}}):CLOSE\]\]$"
                    ),
                )
            if len(expected_patterns) != len(spec.tokens) or any(
                pattern.fullmatch(token) is None
                for pattern, token in zip(
                    expected_patterns,
                    spec.tokens,
                    strict=True,
                )
            ):
                raise ValueError("placeholder token does not match its declared kind")
            if spec.kind is PlaceholderKind.RICH_STYLE:
                open_match = expected_patterns[0].fullmatch(spec.open_token)
                close_match = expected_patterns[1].fullmatch(spec.close_token or "")
                assert open_match is not None and close_match is not None
                if open_match.group(1) != close_match.group(1):
                    raise ValueError("rich-style open and close ids do not match")
        for token in tokens:
            if not token.startswith(prefix) or not token.endswith("]]"):
                raise ValueError("placeholder token is outside its reserved namespace")
            if "\n" in token or "\r" in token:
                raise ValueError("placeholder token must fit on one line")
        expected = digest(
            PLACEHOLDER_NAMESPACE,
            {
                "namespace": self.namespace,
                "specs": [spec.as_payload() for spec in self.specs],
            },
        )
        require_sha256("placeholder signature", self.signature)
        if expected != self.signature:
            raise IdentityError("placeholder signature mismatch")

    @property
    def reserved_prefix(self) -> str:
        return f"[[{self.namespace}:"

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(token for spec in self.specs for token in spec.tokens)

    def validate(self, text: str, *, require_nonempty_styles: bool) -> None:
        if text != normalize_text(text):
            raise PlaceholderContractError("text is not NFC/LF canonical")
        expected = self.tokens
        wrong_counts = {
            token: {"expected": 1, "actual": text.count(token)}
            for token in expected
            if text.count(token) != 1
        }
        if wrong_counts:
            raise PlaceholderContractError(
                f"placeholder occurrence mismatch: {wrong_counts}"
            )

        actual_reserved: list[str] = []
        cursor = 0
        while True:
            start = text.find(self.reserved_prefix, cursor)
            if start < 0:
                break
            end = text.find("]]", start + len(self.reserved_prefix))
            if end < 0:
                raise PlaceholderContractError("unterminated reserved placeholder")
            actual_reserved.append(text[start : end + 2])
            cursor = end + 2
        if Counter(actual_reserved) != Counter(expected):
            raise PlaceholderContractError(
                "reserved placeholder set contains invented or malformed tokens"
            )

        positions = [text.find(token) for token in expected]
        if positions != sorted(positions):
            raise PlaceholderContractError("protected tokens were reordered")

        if require_nonempty_styles:
            for spec in self.specs:
                if spec.kind is not PlaceholderKind.RICH_STYLE:
                    continue
                assert spec.close_token is not None
                left = text.find(spec.open_token) + len(spec.open_token)
                right = text.find(spec.close_token)
                if right < left or not text[left:right].strip():
                    raise PlaceholderContractError(
                        f"empty or reversed rich-style span: {spec.open_token}"
                    )

    def as_payload(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "specs": [spec.as_payload() for spec in self.specs],
            "signature": self.signature,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PlaceholderContract":
        raw_specs = payload.get("specs", [])
        if not isinstance(raw_specs, list):
            raise ValueError("placeholder specs payload must be a list")
        if any(not isinstance(item, dict) for item in raw_specs):
            raise ValueError("placeholder spec payload is malformed")
        return cls(
            namespace=str(payload["namespace"]),
            specs=tuple(
                PlaceholderSpec.from_payload(item) for item in raw_specs
            ),
            signature=str(payload["signature"]),
        )


@dataclass(frozen=True, slots=True)
class PreparedUnit:
    unit_key: str
    unit_revision: str
    snapshot_key: str
    locator: UnitLocator
    source_text: str
    source_sha256: str
    placeholders: PlaceholderContract
    layout_label: str | None
    vertical: bool
    box: BoxFingerprint

    @classmethod
    def create(
        cls,
        *,
        snapshot_key: str,
        locator: UnitLocator,
        source_text: str,
        placeholders: PlaceholderContract,
        layout_label: str | None,
        vertical: bool,
        box: BoxFingerprint,
    ) -> "PreparedUnit":
        require_sha256("snapshot_key", snapshot_key)
        source_text = normalize_text(source_text)
        placeholders.validate(source_text, require_nonempty_styles=True)
        unit_key = digest(
            UNIT_NAMESPACE,
            {"snapshot_key": snapshot_key, "locator": locator.as_payload()},
        )
        revision_payload = {
            "unit_key": unit_key,
            "source_text": source_text,
            "source_sha256": sha256_text(source_text),
            "placeholder_contract": placeholders.as_payload(),
            "layout_label": layout_label,
            "vertical": vertical,
            "box": box.as_payload(),
        }
        return cls(
            unit_key=unit_key,
            unit_revision=digest(UNIT_REVISION_NAMESPACE, revision_payload),
            snapshot_key=snapshot_key,
            locator=locator,
            source_text=source_text,
            source_sha256=sha256_text(source_text),
            placeholders=placeholders,
            layout_label=layout_label,
            vertical=vertical,
            box=box,
        )

    def __post_init__(self) -> None:
        for name in ("unit_key", "unit_revision", "snapshot_key", "source_sha256"):
            require_sha256(name, getattr(self, name))
        if self.source_text != normalize_text(self.source_text):
            raise ValueError("source_text is not NFC/LF canonical")
        if self.source_sha256 != sha256_text(self.source_text):
            raise IdentityError("source_sha256 mismatch")
        self.placeholders.validate(self.source_text, require_nonempty_styles=True)
        expected_key = digest(
            UNIT_NAMESPACE,
            {"snapshot_key": self.snapshot_key, "locator": self.locator.as_payload()},
        )
        if self.unit_key != expected_key:
            raise IdentityError("unit_key mismatch")
        if self.unit_revision != digest(
            UNIT_REVISION_NAMESPACE,
            self._revision_payload(),
        ):
            raise IdentityError("unit_revision mismatch")

    def _revision_payload(self) -> dict[str, object]:
        return {
            "unit_key": self.unit_key,
            "source_text": self.source_text,
            "source_sha256": self.source_sha256,
            "placeholder_contract": self.placeholders.as_payload(),
            "layout_label": self.layout_label,
            "vertical": self.vertical,
            "box": self.box.as_payload(),
        }

    def as_payload(self) -> dict[str, object]:
        return {
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "snapshot_key": self.snapshot_key,
            "locator": self.locator.as_payload(),
            **self._revision_payload(),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PreparedUnit":
        locator = payload["locator"]
        placeholders = payload["placeholder_contract"]
        box = payload["box"]
        if not all(isinstance(item, dict) for item in (locator, placeholders, box)):
            raise ValueError("prepared unit payload is malformed")
        return cls(
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            snapshot_key=str(payload["snapshot_key"]),
            locator=UnitLocator.from_payload(locator),
            source_text=str(payload["source_text"]),
            source_sha256=str(payload["source_sha256"]),
            placeholders=PlaceholderContract.from_payload(placeholders),
            layout_label=(
                str(payload["layout_label"])
                if payload.get("layout_label") is not None
                else None
            ),
            vertical=bool(payload["vertical"]),
            box=BoxFingerprint.from_payload(box),
        )


class Disposition(str, Enum):
    TRANSLATABLE = "translatable"
    SAFE_EXCLUSION = "safe_exclusion"
    BLOCKER = "blocker"


class ParagraphReason(str, Enum):
    TEXT = "TEXT"
    EMPTY = "EMPTY"
    PURE_NUMERIC = "PURE_NUMERIC"
    FORMULA_ONLY = "FORMULA_ONLY"
    DEBUG_ARTIFACT = "DEBUG_ARTIFACT"
    VERTICAL_TEXT_UNSUPPORTED = "VERTICAL_TEXT_UNSUPPORTED"
    UNKNOWN_COMPOSITION = "UNKNOWN_COMPOSITION"
    MISSING_GEOMETRY = "MISSING_GEOMETRY"


@dataclass(frozen=True, slots=True)
class ParagraphRecord:
    record_key: str
    record_revision: str
    snapshot_key: str
    locator: UnitLocator
    disposition: Disposition
    reason: ParagraphReason
    source_text: str
    source_sha256: str
    layout_label: str | None
    vertical: bool
    box: BoxFingerprint | None
    unit: PreparedUnit | None = None

    @classmethod
    def create(
        cls,
        *,
        snapshot_key: str,
        locator: UnitLocator,
        disposition: Disposition,
        reason: ParagraphReason,
        source_text: str,
        layout_label: str | None,
        vertical: bool,
        box: BoxFingerprint | None,
        unit: PreparedUnit | None = None,
    ) -> "ParagraphRecord":
        source_text = normalize_text(source_text)
        record_key = digest(
            RECORD_NAMESPACE,
            {"snapshot_key": snapshot_key, "locator": locator.as_payload()},
        )
        payload = {
            "record_key": record_key,
            "disposition": disposition.value,
            "reason": reason.value,
            "source_text": source_text,
            "source_sha256": sha256_text(source_text),
            "layout_label": layout_label,
            "vertical": vertical,
            "box": box.as_payload() if box is not None else None,
            "unit_revision": unit.unit_revision if unit else None,
        }
        return cls(
            record_key=record_key,
            record_revision=digest("pubtrans.paragraph-record-revision/v2", payload),
            snapshot_key=snapshot_key,
            locator=locator,
            disposition=disposition,
            reason=reason,
            source_text=source_text,
            source_sha256=sha256_text(source_text),
            layout_label=layout_label,
            vertical=vertical,
            box=box,
            unit=unit,
        )

    def __post_init__(self) -> None:
        for name in (
            "record_key",
            "record_revision",
            "snapshot_key",
            "source_sha256",
        ):
            require_sha256(name, getattr(self, name))
        if self.source_text != normalize_text(self.source_text):
            raise ValueError("paragraph source is not canonical")
        if self.source_sha256 != sha256_text(self.source_text):
            raise IdentityError("paragraph source digest mismatch")
        allowed_reasons = {
            Disposition.TRANSLATABLE: {ParagraphReason.TEXT},
            Disposition.SAFE_EXCLUSION: {
                ParagraphReason.EMPTY,
                ParagraphReason.PURE_NUMERIC,
                ParagraphReason.FORMULA_ONLY,
                ParagraphReason.DEBUG_ARTIFACT,
            },
            Disposition.BLOCKER: {
                ParagraphReason.VERTICAL_TEXT_UNSUPPORTED,
                ParagraphReason.UNKNOWN_COMPOSITION,
                ParagraphReason.MISSING_GEOMETRY,
            },
        }
        if self.reason not in allowed_reasons[self.disposition]:
            raise ValueError(
                f"reason {self.reason.value} is invalid for {self.disposition.value}"
            )
        if self.disposition is Disposition.TRANSLATABLE:
            if self.reason is not ParagraphReason.TEXT or self.unit is None:
                raise ValueError("translatable record requires a TEXT unit")
        elif self.unit is not None:
            raise ValueError("excluded or blocking record cannot carry a unit")
        if self.unit is not None:
            if self.unit.snapshot_key != self.snapshot_key:
                raise IdentityError("record and unit snapshot mismatch")
            if self.unit.locator != self.locator:
                raise IdentityError("record and unit locator mismatch")
            if self.unit.source_text != self.source_text:
                raise IdentityError("record and unit source mismatch")
            if self.unit.box != self.box:
                raise IdentityError("record and unit geometry mismatch")
        if self.reason is ParagraphReason.MISSING_GEOMETRY:
            if self.box is not None:
                raise ValueError("missing-geometry blocker cannot carry a box")
        elif self.box is None:
            raise ValueError(
                "paragraph without a box must be a missing-geometry blocker"
            )
        if (
            self.reason is ParagraphReason.VERTICAL_TEXT_UNSUPPORTED
            and not self.vertical
        ):
            raise ValueError("vertical-text blocker must be marked vertical")
        expected_key = digest(
            RECORD_NAMESPACE,
            {"snapshot_key": self.snapshot_key, "locator": self.locator.as_payload()},
        )
        if self.record_key != expected_key:
            raise IdentityError("paragraph record key mismatch")
        if self.record_revision != digest(
            "pubtrans.paragraph-record-revision/v2",
            self._revision_payload(),
        ):
            raise IdentityError("paragraph record revision mismatch")

    def _revision_payload(self) -> dict[str, object]:
        return {
            "record_key": self.record_key,
            "disposition": self.disposition.value,
            "reason": self.reason.value,
            "source_text": self.source_text,
            "source_sha256": self.source_sha256,
            "layout_label": self.layout_label,
            "vertical": self.vertical,
            "box": self.box.as_payload() if self.box is not None else None,
            "unit_revision": self.unit.unit_revision if self.unit else None,
        }

    def as_payload(self) -> dict[str, object]:
        return {
            "record_key": self.record_key,
            "record_revision": self.record_revision,
            "snapshot_key": self.snapshot_key,
            "locator": self.locator.as_payload(),
            **self._revision_payload(),
            "unit": self.unit.as_payload() if self.unit else None,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ParagraphRecord":
        locator = payload["locator"]
        box = payload["box"]
        raw_unit = payload.get("unit")
        if not isinstance(locator, dict) or (
            box is not None and not isinstance(box, dict)
        ):
            raise ValueError("paragraph record payload is malformed")
        return cls(
            record_key=str(payload["record_key"]),
            record_revision=str(payload["record_revision"]),
            snapshot_key=str(payload["snapshot_key"]),
            locator=UnitLocator.from_payload(locator),
            disposition=Disposition(str(payload["disposition"])),
            reason=ParagraphReason(str(payload["reason"])),
            source_text=str(payload["source_text"]),
            source_sha256=str(payload["source_sha256"]),
            layout_label=(
                str(payload["layout_label"])
                if payload.get("layout_label") is not None
                else None
            ),
            vertical=bool(payload["vertical"]),
            box=(BoxFingerprint.from_payload(box) if isinstance(box, dict) else None),
            unit=(
                PreparedUnit.from_payload(raw_unit)
                if isinstance(raw_unit, dict)
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    project: ProjectBinding
    snapshot: PreparedSnapshot
    page_paragraph_counts: tuple[int, ...]
    records: tuple[ParagraphRecord, ...]
    manifest_sha256: str

    @classmethod
    def create(
        cls,
        *,
        project: ProjectBinding,
        snapshot: PreparedSnapshot,
        page_paragraph_counts: tuple[int, ...] | list[int],
        records: tuple[ParagraphRecord, ...] | list[ParagraphRecord],
    ) -> "PreparedDocument":
        page_paragraph_counts = tuple(page_paragraph_counts)
        records = tuple(records)
        payload = cls._manifest_payload(
            project,
            snapshot,
            page_paragraph_counts,
            records,
        )
        return cls(
            project=project,
            snapshot=snapshot,
            page_paragraph_counts=page_paragraph_counts,
            records=records,
            manifest_sha256=digest(MANIFEST_NAMESPACE, payload),
        )

    def __post_init__(self) -> None:
        require_sha256("manifest_sha256", self.manifest_sha256)
        if self.project.project_key != self.snapshot.project_key:
            raise IdentityError("project and snapshot keys differ")
        if any(count < 0 for count in self.page_paragraph_counts):
            raise ValueError("page paragraph counts must be non-negative")
        locators = [record.locator for record in self.records]
        if locators != sorted(locators):
            raise ValueError("paragraph records must be in locator order")
        if len(locators) != len(set(locators)):
            raise ValueError("duplicate paragraph locator")
        expected_locators = [
            UnitLocator(page_ordinal, paragraph_ordinal)
            for page_ordinal, count in enumerate(self.page_paragraph_counts)
            for paragraph_ordinal in range(count)
        ]
        if locators != expected_locators:
            raise ValueError(
                "paragraph records do not exactly classify every prepared paragraph"
            )
        if any(record.snapshot_key != self.snapshot.snapshot_key for record in self.records):
            raise IdentityError("paragraph record belongs to another snapshot")
        expected = digest(
            MANIFEST_NAMESPACE,
            self._manifest_payload(
                self.project,
                self.snapshot,
                self.page_paragraph_counts,
                self.records,
            ),
        )
        if self.manifest_sha256 != expected:
            raise IdentityError("prepared-document manifest digest mismatch")

    @staticmethod
    def _manifest_payload(
        project: ProjectBinding,
        snapshot: PreparedSnapshot,
        page_paragraph_counts: tuple[int, ...],
        records: tuple[ParagraphRecord, ...],
    ) -> dict[str, object]:
        return {
            "project": project.as_payload(),
            "snapshot": snapshot.as_payload(),
            "page_paragraph_counts": list(page_paragraph_counts),
            "records": [record.as_payload() for record in records],
        }

    @property
    def units(self) -> tuple[PreparedUnit, ...]:
        return tuple(record.unit for record in self.records if record.unit is not None)

    @property
    def blockers(self) -> tuple[ParagraphRecord, ...]:
        return tuple(
            record
            for record in self.records
            if record.disposition is Disposition.BLOCKER
        )

    def require_unblocked(self) -> None:
        if self.blockers:
            details = [
                {
                    "locator": record.locator.as_payload(),
                    "reason": record.reason.value,
                }
                for record in self.blockers
            ]
            raise DocumentBlockedError(f"prepared document has blockers: {details}")

    def as_payload(self) -> dict[str, object]:
        return {
            **self._manifest_payload(
                self.project,
                self.snapshot,
                self.page_paragraph_counts,
                self.records,
            ),
            "manifest_sha256": self.manifest_sha256,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "PreparedDocument":
        project = payload["project"]
        snapshot = payload["snapshot"]
        page_paragraph_counts = payload["page_paragraph_counts"]
        records = payload["records"]
        if (
            not isinstance(project, dict)
            or not isinstance(snapshot, dict)
            or not isinstance(page_paragraph_counts, list)
            or not isinstance(records, list)
        ):
            raise ValueError("prepared-document payload is malformed")
        if any(not isinstance(record, dict) for record in records):
            raise ValueError("prepared-document record payload is malformed")
        return cls(
            project=ProjectBinding.from_payload(project),
            snapshot=PreparedSnapshot.from_payload(snapshot),
            page_paragraph_counts=tuple(int(count) for count in page_paragraph_counts),
            records=tuple(
                ParagraphRecord.from_payload(record) for record in records
            ),
            manifest_sha256=str(payload["manifest_sha256"]),
        )


@dataclass(frozen=True, slots=True)
class ApprovalRevision:
    approval_id: str
    unit_key: str
    unit_revision: str
    target_text: str
    target_sha256: str
    origin: str

    @classmethod
    def create(
        cls,
        *,
        unit: PreparedUnit,
        target_text: str,
        origin: str,
    ) -> "ApprovalRevision":
        target_text = normalize_text(target_text)
        unit.placeholders.validate(target_text, require_nonempty_styles=True)
        payload = {
            "unit_key": unit.unit_key,
            "unit_revision": unit.unit_revision,
            "target_text": target_text,
            "target_sha256": sha256_text(target_text),
            "origin": origin.strip(),
        }
        if not payload["origin"]:
            raise ValueError("approval origin must not be empty")
        return cls(approval_id=digest(APPROVAL_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        for name in (
            "approval_id",
            "unit_key",
            "unit_revision",
            "target_sha256",
        ):
            require_sha256(name, getattr(self, name))
        if self.target_text != normalize_text(self.target_text):
            raise ValueError("target_text is not NFC/LF canonical")
        if not self.target_text.strip():
            raise ValueError("approved target must not be blank")
        disallowed = [
            character
            for character in self.target_text
            if unicodedata.category(character) == "Cc" and character not in "\n\t"
        ]
        if disallowed:
            raise ValueError("approved target contains disallowed control characters")
        if self.target_sha256 != sha256_text(self.target_text):
            raise IdentityError("approved target digest mismatch")
        if not self.origin.strip():
            raise ValueError("approval origin must not be empty")
        expected = digest(APPROVAL_NAMESPACE, self._identity_payload())
        if self.approval_id != expected:
            raise IdentityError("approval_id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "unit_key": self.unit_key,
            "unit_revision": self.unit_revision,
            "target_text": self.target_text,
            "target_sha256": self.target_sha256,
            "origin": self.origin,
        }

    def as_payload(self) -> dict[str, object]:
        return {"approval_id": self.approval_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ApprovalRevision":
        return cls(
            approval_id=str(payload["approval_id"]),
            unit_key=str(payload["unit_key"]),
            unit_revision=str(payload["unit_revision"]),
            target_text=str(payload["target_text"]),
            target_sha256=str(payload["target_sha256"]),
            origin=str(payload["origin"]),
        )
