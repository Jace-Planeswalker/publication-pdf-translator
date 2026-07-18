from __future__ import annotations

import pytest

from pubtrans.m0v2.errors import IdentityError
from pubtrans.m4.model import ArtifactCategory
from pubtrans.m4.model import ArtifactFinding
from pubtrans.m4.model import ArtifactReport
from pubtrans.m4.model import ArtifactSeverity
from pubtrans.m4.model import ArtifactVerdict
from pubtrans.m4.model import ArtifactVerificationProfile


def test_artifact_model_is_deterministic_and_round_trips() -> None:
    profile = ArtifactVerificationProfile.create(raster_scale=0.75)
    finding = ArtifactFinding.create(
        category=ArtifactCategory.OVERLAP,
        severity=ArtifactSeverity.WARNING,
        page_ordinal=2,
        message="Review a possible overlap.",
        evidence="pair=3",
    )
    report = ArtifactReport.create(
        release_id="1" * 64,
        project_key="2" * 64,
        source_pdf_sha256="3" * 64,
        target_pdf_sha256="4" * 64,
        profile=profile,
        source_page_count=3,
        target_page_count=3,
        findings=(finding,),
        metrics={"pages": 3},
    )
    assert report.verdict is ArtifactVerdict.PASS
    assert ArtifactReport.from_payload(report.as_payload()) == report
    assert ArtifactReport.from_payload(report.as_payload()).report_id == report.report_id


def test_error_or_blocking_finding_blocks_release() -> None:
    finding = ArtifactFinding.create(
        category=ArtifactCategory.TEXT_COVERAGE,
        severity=ArtifactSeverity.ERROR,
        message="Approved text is absent.",
    )
    report = ArtifactReport.create(
        release_id="1" * 64,
        project_key="2" * 64,
        source_pdf_sha256="3" * 64,
        target_pdf_sha256="4" * 64,
        profile=ArtifactVerificationProfile.create(),
        source_page_count=1,
        target_page_count=1,
        findings=(finding,),
        metrics={},
    )
    assert report.verdict is ArtifactVerdict.BLOCK


def test_tampered_report_identity_is_rejected() -> None:
    report = ArtifactReport.create(
        release_id="1" * 64,
        project_key="2" * 64,
        source_pdf_sha256="3" * 64,
        target_pdf_sha256="4" * 64,
        profile=ArtifactVerificationProfile.create(),
        source_page_count=1,
        target_page_count=1,
        findings=(),
        metrics={},
    )
    payload = report.as_payload()
    payload["target_page_count"] = 2
    with pytest.raises(IdentityError, match="report id mismatch"):
        ArtifactReport.from_payload(payload)
