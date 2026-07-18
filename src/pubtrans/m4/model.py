"""Immutable artifact-verification profiles, findings, and reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum

from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.errors import IdentityError


PROFILE_NAMESPACE = "pubtrans.artifact-profile/v1"
FINDING_NAMESPACE = "pubtrans.artifact-finding/v1"
REPORT_NAMESPACE = "pubtrans.artifact-report/v1"


def _nonempty(name: str, value: str) -> str:
    result = normalize_text(value).strip()
    if not result:
        raise ValueError(f"{name} must not be empty")
    return result


class ArtifactVerdict(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"


class ArtifactSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    BLOCKING = "BLOCKING"


class ArtifactCategory(str, Enum):
    FILE_INTEGRITY = "FILE_INTEGRITY"
    PAGE_COUNT = "PAGE_COUNT"
    PAGE_GEOMETRY = "PAGE_GEOMETRY"
    BLANK_PAGE = "BLANK_PAGE"
    CONTENT_DENSITY = "CONTENT_DENSITY"
    TEXT_COVERAGE = "TEXT_COVERAGE"
    PROTECTED_ANCHOR = "PROTECTED_ANCHOR"
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
    OVERLAP = "OVERLAP"
    IMAGE_COVERAGE = "IMAGE_COVERAGE"
    FONT = "FONT"
    CORRUPT_GLYPH = "CORRUPT_GLYPH"


@dataclass(frozen=True, slots=True)
class ArtifactVerificationProfile:
    profile_id: str
    page_size_tolerance_points: float
    minimum_ink_ratio: float
    minimum_relative_ink_ratio: float
    maximum_overlap_fraction: float
    raster_scale: float
    require_exact_page_count: bool
    require_all_unit_literals: bool
    require_source_anchors: bool
    require_image_hashes: bool

    @classmethod
    def create(
        cls,
        *,
        page_size_tolerance_points: float = 1.0,
        minimum_ink_ratio: float = 0.0001,
        minimum_relative_ink_ratio: float = 0.15,
        maximum_overlap_fraction: float = 0.55,
        raster_scale: float = 0.5,
        require_exact_page_count: bool = True,
        require_all_unit_literals: bool = True,
        require_source_anchors: bool = True,
        require_image_hashes: bool = True,
    ) -> "ArtifactVerificationProfile":
        payload = {
            "page_size_tolerance_points": float(page_size_tolerance_points),
            "minimum_ink_ratio": float(minimum_ink_ratio),
            "minimum_relative_ink_ratio": float(minimum_relative_ink_ratio),
            "maximum_overlap_fraction": float(maximum_overlap_fraction),
            "raster_scale": float(raster_scale),
            "require_exact_page_count": bool(require_exact_page_count),
            "require_all_unit_literals": bool(require_all_unit_literals),
            "require_source_anchors": bool(require_source_anchors),
            "require_image_hashes": bool(require_image_hashes),
        }
        return cls(profile_id=digest(PROFILE_NAMESPACE, payload), **payload)

    def __post_init__(self) -> None:
        require_sha256("profile_id", self.profile_id)
        if self.page_size_tolerance_points < 0:
            raise ValueError("page-size tolerance must be non-negative")
        for name in (
            "minimum_ink_ratio",
            "minimum_relative_ink_ratio",
            "maximum_overlap_fraction",
        ):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")
        if self.raster_scale <= 0:
            raise ValueError("raster scale must be positive")
        if self.profile_id != digest(PROFILE_NAMESPACE, self._identity_payload()):
            raise IdentityError("artifact profile id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "page_size_tolerance_points": self.page_size_tolerance_points,
            "minimum_ink_ratio": self.minimum_ink_ratio,
            "minimum_relative_ink_ratio": self.minimum_relative_ink_ratio,
            "maximum_overlap_fraction": self.maximum_overlap_fraction,
            "raster_scale": self.raster_scale,
            "require_exact_page_count": self.require_exact_page_count,
            "require_all_unit_literals": self.require_all_unit_literals,
            "require_source_anchors": self.require_source_anchors,
            "require_image_hashes": self.require_image_hashes,
        }

    def as_payload(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ArtifactVerificationProfile":
        return cls(
            profile_id=str(payload["profile_id"]),
            page_size_tolerance_points=float(payload["page_size_tolerance_points"]),
            minimum_ink_ratio=float(payload["minimum_ink_ratio"]),
            minimum_relative_ink_ratio=float(payload["minimum_relative_ink_ratio"]),
            maximum_overlap_fraction=float(payload["maximum_overlap_fraction"]),
            raster_scale=float(payload["raster_scale"]),
            require_exact_page_count=bool(payload["require_exact_page_count"]),
            require_all_unit_literals=bool(payload["require_all_unit_literals"]),
            require_source_anchors=bool(payload["require_source_anchors"]),
            require_image_hashes=bool(payload["require_image_hashes"]),
        )


@dataclass(frozen=True, slots=True)
class ArtifactFinding:
    finding_id: str
    category: ArtifactCategory
    severity: ArtifactSeverity
    page_ordinal: int | None
    message: str
    evidence: str

    @classmethod
    def create(
        cls,
        *,
        category: ArtifactCategory,
        severity: ArtifactSeverity,
        message: str,
        page_ordinal: int | None = None,
        evidence: str = "",
    ) -> "ArtifactFinding":
        if page_ordinal is not None and page_ordinal < 0:
            raise ValueError("finding page ordinal must be non-negative")
        message = _nonempty("artifact finding message", message)
        evidence = normalize_text(evidence).strip()
        payload = {
            "category": category.value,
            "severity": severity.value,
            "page_ordinal": page_ordinal,
            "message": message,
            "evidence": evidence,
        }
        return cls(
            finding_id=digest(FINDING_NAMESPACE, payload),
            category=category,
            severity=severity,
            page_ordinal=page_ordinal,
            message=message,
            evidence=evidence,
        )

    def __post_init__(self) -> None:
        require_sha256("finding_id", self.finding_id)
        if self.page_ordinal is not None and self.page_ordinal < 0:
            raise ValueError("finding page ordinal must be non-negative")
        if self.message != _nonempty("artifact finding message", self.message):
            raise ValueError("artifact finding message is not canonical")
        if self.evidence != normalize_text(self.evidence).strip():
            raise ValueError("artifact finding evidence is not canonical")
        if self.finding_id != digest(FINDING_NAMESPACE, self._identity_payload()):
            raise IdentityError("artifact finding id mismatch")

    def _identity_payload(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "severity": self.severity.value,
            "page_ordinal": self.page_ordinal,
            "message": self.message,
            "evidence": self.evidence,
        }

    def as_payload(self) -> dict[str, object]:
        return {"finding_id": self.finding_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ArtifactFinding":
        page = payload.get("page_ordinal")
        return cls(
            finding_id=str(payload["finding_id"]),
            category=ArtifactCategory(str(payload["category"])),
            severity=ArtifactSeverity(str(payload["severity"])),
            page_ordinal=int(page) if page is not None else None,
            message=str(payload["message"]),
            evidence=str(payload["evidence"]),
        )


@dataclass(frozen=True, slots=True)
class ArtifactReport:
    report_id: str
    release_id: str
    project_key: str
    source_pdf_sha256: str
    target_pdf_sha256: str
    profile: ArtifactVerificationProfile
    source_page_count: int
    target_page_count: int
    verdict: ArtifactVerdict
    findings: tuple[ArtifactFinding, ...]
    metrics_json: str

    @classmethod
    def create(
        cls,
        *,
        release_id: str,
        project_key: str,
        source_pdf_sha256: str,
        target_pdf_sha256: str,
        profile: ArtifactVerificationProfile,
        source_page_count: int,
        target_page_count: int,
        findings: tuple[ArtifactFinding, ...] | list[ArtifactFinding],
        metrics: dict[str, object],
    ) -> "ArtifactReport":
        findings = tuple(sorted(findings, key=lambda item: item.finding_id))
        verdict = (
            ArtifactVerdict.BLOCK
            if any(
                item.severity in {ArtifactSeverity.ERROR, ArtifactSeverity.BLOCKING}
                for item in findings
            )
            else ArtifactVerdict.PASS
        )
        metrics_json = canonical_json(metrics)
        payload = {
            "release_id": release_id,
            "project_key": project_key,
            "source_pdf_sha256": source_pdf_sha256,
            "target_pdf_sha256": target_pdf_sha256,
            "profile": profile.as_payload(),
            "source_page_count": source_page_count,
            "target_page_count": target_page_count,
            "verdict": verdict.value,
            "findings": [item.as_payload() for item in findings],
            "metrics_json": metrics_json,
        }
        return cls(
            report_id=digest(REPORT_NAMESPACE, payload),
            release_id=release_id,
            project_key=project_key,
            source_pdf_sha256=source_pdf_sha256,
            target_pdf_sha256=target_pdf_sha256,
            profile=profile,
            source_page_count=source_page_count,
            target_page_count=target_page_count,
            verdict=verdict,
            findings=findings,
            metrics_json=metrics_json,
        )

    def __post_init__(self) -> None:
        for name in (
            "report_id",
            "release_id",
            "project_key",
            "source_pdf_sha256",
            "target_pdf_sha256",
        ):
            require_sha256(name, getattr(self, name))
        if self.source_page_count < 0 or self.target_page_count < 0:
            raise ValueError("artifact page counts must be non-negative")
        finding_ids = [item.finding_id for item in self.findings]
        if finding_ids != sorted(finding_ids) or len(finding_ids) != len(
            set(finding_ids)
        ):
            raise ValueError("artifact findings are not canonical and unique")
        expected_verdict = (
            ArtifactVerdict.BLOCK
            if any(
                item.severity in {ArtifactSeverity.ERROR, ArtifactSeverity.BLOCKING}
                for item in self.findings
            )
            else ArtifactVerdict.PASS
        )
        if self.verdict is not expected_verdict:
            raise ValueError("artifact verdict contradicts its findings")
        if canonical_json(json.loads(self.metrics_json)) != self.metrics_json:
            raise ValueError("artifact metrics JSON is not canonical")
        if self.report_id != digest(REPORT_NAMESPACE, self._identity_payload()):
            raise IdentityError("artifact report id mismatch")

    @property
    def metrics(self) -> dict[str, object]:
        value = json.loads(self.metrics_json)
        if not isinstance(value, dict):
            raise ValueError("artifact metrics are not an object")
        return value

    def _identity_payload(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "project_key": self.project_key,
            "source_pdf_sha256": self.source_pdf_sha256,
            "target_pdf_sha256": self.target_pdf_sha256,
            "profile": self.profile.as_payload(),
            "source_page_count": self.source_page_count,
            "target_page_count": self.target_page_count,
            "verdict": self.verdict.value,
            "findings": [item.as_payload() for item in self.findings],
            "metrics_json": self.metrics_json,
        }

    def as_payload(self) -> dict[str, object]:
        return {"report_id": self.report_id, **self._identity_payload()}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ArtifactReport":
        raw_profile = payload["profile"]
        raw_findings = payload["findings"]
        if not isinstance(raw_profile, dict) or not isinstance(raw_findings, list):
            raise ValueError("artifact report payload is malformed")
        if any(not isinstance(item, dict) for item in raw_findings):
            raise ValueError("artifact findings payload is malformed")
        return cls(
            report_id=str(payload["report_id"]),
            release_id=str(payload["release_id"]),
            project_key=str(payload["project_key"]),
            source_pdf_sha256=str(payload["source_pdf_sha256"]),
            target_pdf_sha256=str(payload["target_pdf_sha256"]),
            profile=ArtifactVerificationProfile.from_payload(raw_profile),
            source_page_count=int(payload["source_page_count"]),
            target_page_count=int(payload["target_page_count"]),
            verdict=ArtifactVerdict(str(payload["verdict"])),
            findings=tuple(ArtifactFinding.from_payload(item) for item in raw_findings),
            metrics_json=str(payload["metrics_json"]),
        )
