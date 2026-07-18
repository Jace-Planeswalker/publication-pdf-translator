"""Deterministic project initialization, diagnostics, and delivery collection."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pubtrans import __version__
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import require_sha256
from pubtrans.m0v2.store import ProjectStore
from pubtrans.m4.artifacts import FinalPDFStore
from pubtrans.m4.store import VerificationStore
from pubtrans.m4.verifier import sha256_file
from pubtrans.m5.config import ProductConfig
from pubtrans.m5.evidence import EvidenceCatalog


PROJECT_CONTROL_SCHEMA = 1
RUNTIME_COMPATIBILITY = ".".join(__version__.split(".")[:2])
CONTROL_RELATIVE_PATH = "control/project.json"
SOURCE_RELATIVE_PATH = "inputs/source.pdf"
CONFIG_RELATIVE_PATH = "inputs/config.json"
EVIDENCE_RELATIVE_PATH = "inputs/evidence.json"


class OperatorError(RuntimeError):
    """Base class for operator-surface failures."""


class OperatorConflictError(OperatorError):
    """A durable project is bound to different immutable inputs."""


class OperatorBlockedError(OperatorError):
    """A deterministic precondition blocks the requested operation."""


@dataclass(frozen=True, slots=True)
class ProjectControlManifest:
    schema_version: int
    project_id: str
    runtime_compatibility: str
    source_name: str
    source_sha256: str
    source_size: int
    source_relative_path: str
    config_sha256: str
    config_relative_path: str
    evidence_sha256: str | None
    evidence_relative_path: str | None
    skip_scanned_detection: bool
    primary_font_family: str | None

    @classmethod
    def create(
        cls,
        *,
        source_name: str,
        source_sha256: str,
        source_size: int,
        config_sha256: str,
        evidence_sha256: str | None,
        skip_scanned_detection: bool,
        primary_font_family: str | None,
    ) -> "ProjectControlManifest":
        payload = {
            "runtime_compatibility": RUNTIME_COMPATIBILITY,
            "source_name": source_name,
            "source_sha256": source_sha256,
            "source_size": source_size,
            "config_sha256": config_sha256,
            "evidence_sha256": evidence_sha256,
            "skip_scanned_detection": bool(skip_scanned_detection),
            "primary_font_family": primary_font_family,
        }
        return cls(
            schema_version=PROJECT_CONTROL_SCHEMA,
            project_id=digest("pubtrans.operator-project/v1", payload),
            source_relative_path=SOURCE_RELATIVE_PATH,
            config_relative_path=CONFIG_RELATIVE_PATH,
            evidence_relative_path=(
                EVIDENCE_RELATIVE_PATH if evidence_sha256 is not None else None
            ),
            **payload,
        )

    def __post_init__(self) -> None:
        if self.schema_version != PROJECT_CONTROL_SCHEMA:
            raise OperatorConflictError("unsupported project-control schema")
        require_sha256("operator project id", self.project_id)
        require_sha256("operator source digest", self.source_sha256)
        require_sha256("operator config digest", self.config_sha256)
        if self.evidence_sha256 is not None:
            require_sha256("operator evidence digest", self.evidence_sha256)
        if self.runtime_compatibility != RUNTIME_COMPATIBILITY:
            raise OperatorConflictError(
                "project runtime compatibility differs from this pubtrans release"
            )
        if not self.source_name or Path(self.source_name).name != self.source_name:
            raise OperatorConflictError("project source name is invalid")
        if self.source_size < 1:
            raise OperatorConflictError("project source size is invalid")
        if self.source_relative_path != SOURCE_RELATIVE_PATH:
            raise OperatorConflictError("project source path is not canonical")
        if self.config_relative_path != CONFIG_RELATIVE_PATH:
            raise OperatorConflictError("project config path is not canonical")
        expected_evidence = (
            EVIDENCE_RELATIVE_PATH if self.evidence_sha256 is not None else None
        )
        if self.evidence_relative_path != expected_evidence:
            raise OperatorConflictError("project evidence path is not canonical")
        if self.primary_font_family is not None:
            normalized = self.primary_font_family.strip()
            if not normalized or normalized != self.primary_font_family:
                raise OperatorConflictError("primary font family is non-canonical")

        expected = digest(
            "pubtrans.operator-project/v1",
            {
                "runtime_compatibility": self.runtime_compatibility,
                "source_name": self.source_name,
                "source_sha256": self.source_sha256,
                "source_size": self.source_size,
                "config_sha256": self.config_sha256,
                "evidence_sha256": self.evidence_sha256,
                "skip_scanned_detection": self.skip_scanned_detection,
                "primary_font_family": self.primary_font_family,
            },
        )
        if self.project_id != expected:
            raise OperatorConflictError("operator project id does not match its inputs")

    def as_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "runtime_compatibility": self.runtime_compatibility,
            "source_name": self.source_name,
            "source_sha256": self.source_sha256,
            "source_size": self.source_size,
            "source_relative_path": self.source_relative_path,
            "config_sha256": self.config_sha256,
            "config_relative_path": self.config_relative_path,
            "evidence_sha256": self.evidence_sha256,
            "evidence_relative_path": self.evidence_relative_path,
            "skip_scanned_detection": self.skip_scanned_detection,
            "primary_font_family": self.primary_font_family,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ProjectControlManifest":
        expected_fields = set(cls.__dataclass_fields__)
        if set(payload) != expected_fields:
            raise OperatorConflictError(
                "project control manifest fields do not match its schema"
            )
        string_fields = (
            "project_id",
            "runtime_compatibility",
            "source_name",
            "source_sha256",
            "source_relative_path",
            "config_sha256",
            "config_relative_path",
        )
        if any(not isinstance(payload[name], str) for name in string_fields):
            raise OperatorConflictError(
                "project control manifest contains a non-string field"
            )
        if type(payload["schema_version"]) is not int:
            raise OperatorConflictError("project control schema version is not an integer")
        if type(payload["source_size"]) is not int:
            raise OperatorConflictError("project source size is not an integer")
        if type(payload["skip_scanned_detection"]) is not bool:
            raise OperatorConflictError(
                "project scanned-detection policy is not a boolean"
            )
        evidence_digest = payload.get("evidence_sha256")
        evidence_path = payload.get("evidence_relative_path")
        font = payload.get("primary_font_family")
        for name, value in (
            ("evidence_sha256", evidence_digest),
            ("evidence_relative_path", evidence_path),
            ("primary_font_family", font),
        ):
            if value is not None and not isinstance(value, str):
                raise OperatorConflictError(
                    f"project control manifest {name} is not a string or null"
                )
        return cls(
            schema_version=payload["schema_version"],
            project_id=payload["project_id"],
            runtime_compatibility=payload["runtime_compatibility"],
            source_name=payload["source_name"],
            source_sha256=payload["source_sha256"],
            source_size=payload["source_size"],
            source_relative_path=payload["source_relative_path"],
            config_sha256=payload["config_sha256"],
            config_relative_path=payload["config_relative_path"],
            evidence_sha256=evidence_digest,
            evidence_relative_path=evidence_path,
            skip_scanned_detection=payload["skip_scanned_detection"],
            primary_font_family=font,
        )


@dataclass(frozen=True, slots=True)
class ControlledRunInputs:
    project: Path
    source_pdf: Path
    config: Path
    evidence: Path | None
    skip_scanned_detection: bool
    primary_font_family: str | None


def initialize_project(
    *,
    source_pdf: str | Path,
    project_directory: str | Path,
    config: ProductConfig,
    evidence_path: str | Path | None = None,
    skip_scanned_detection: bool = False,
    primary_font_family: str | None = None,
) -> dict[str, object]:
    source = Path(source_pdf).resolve()
    project = Path(project_directory).resolve()
    if not source.is_file():
        raise OperatorBlockedError("source PDF does not exist")
    _validate_pdf(source)
    _require_safe_project_location(project)

    source_digest = sha256_file(source)
    source_size = source.stat().st_size
    config_payload = config.as_payload()
    config_bytes = _json_bytes(config_payload)
    config_digest = hashlib.sha256(config_bytes).hexdigest()

    evidence_bytes: bytes | None = None
    evidence_digest: str | None = None
    if evidence_path is not None:
        evidence = EvidenceCatalog.load(evidence_path)
        evidence_bytes = _json_bytes(
            {"entries": [item.as_payload() for item in evidence.entries]}
        )
        evidence_digest = hashlib.sha256(evidence_bytes).hexdigest()

    font = primary_font_family.strip() if primary_font_family else None
    manifest = ProjectControlManifest.create(
        source_name=source.name,
        source_sha256=source_digest,
        source_size=source_size,
        config_sha256=config_digest,
        evidence_sha256=evidence_digest,
        skip_scanned_detection=skip_scanned_detection,
        primary_font_family=font,
    )

    manifest_path = project / CONTROL_RELATIVE_PATH
    if manifest_path.is_file():
        existing = load_project_manifest(project)
        if existing != manifest:
            raise OperatorConflictError(
                "project is already initialized with different immutable inputs"
            )
        return _initialization_payload(project, existing, "EXISTING")

    _require_initializable_directory(project)
    project.mkdir(parents=True, exist_ok=True)
    _atomic_copy(source, project / SOURCE_RELATIVE_PATH)
    _atomic_write(project / CONFIG_RELATIVE_PATH, config_bytes)
    if evidence_bytes is not None:
        _atomic_write(project / EVIDENCE_RELATIVE_PATH, evidence_bytes)
    _atomic_write(manifest_path, _json_bytes(manifest.as_payload()))
    load_project_manifest(project)
    return _initialization_payload(project, manifest, "INITIALIZED")


def load_project_manifest(
    project_directory: str | Path,
) -> ProjectControlManifest:
    project = Path(project_directory).resolve()
    path = project / CONTROL_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OperatorConflictError("cannot read project control manifest") from exc
    if not isinstance(payload, dict):
        raise OperatorConflictError("project control manifest must be an object")
    try:
        manifest = ProjectControlManifest.from_payload(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise OperatorConflictError("project control manifest is malformed") from exc
    _verify_bound_file(
        project / manifest.source_relative_path,
        expected_sha256=manifest.source_sha256,
        expected_size=manifest.source_size,
        label="bound source PDF",
    )
    _verify_bound_file(
        project / manifest.config_relative_path,
        expected_sha256=manifest.config_sha256,
        label="bound product config",
    )
    ProductConfig.load(project / manifest.config_relative_path)
    if manifest.evidence_relative_path is not None:
        assert manifest.evidence_sha256 is not None
        evidence = project / manifest.evidence_relative_path
        _verify_bound_file(
            evidence,
            expected_sha256=manifest.evidence_sha256,
            label="bound terminology evidence",
        )
        EvidenceCatalog.load(evidence)
    _validate_pdf(project / manifest.source_relative_path)
    return manifest


def controlled_run_inputs(project_directory: str | Path) -> ControlledRunInputs:
    project = Path(project_directory).resolve()
    manifest = load_project_manifest(project)
    return ControlledRunInputs(
        project=project,
        source_pdf=project / manifest.source_relative_path,
        config=project / manifest.config_relative_path,
        evidence=(
            project / manifest.evidence_relative_path
            if manifest.evidence_relative_path is not None
            else None
        ),
        skip_scanned_detection=manifest.skip_scanned_detection,
        primary_font_family=manifest.primary_font_family,
    )


def project_status(project_directory: str | Path) -> dict[str, object]:
    project = Path(project_directory).resolve()
    manifest_path = project / CONTROL_RELATIVE_PATH
    manifest: ProjectControlManifest | None = None
    control_error: str | None = None
    if manifest_path.exists():
        try:
            manifest = load_project_manifest(project)
        except Exception as exc:
            control_error = _bounded(str(exc))

    state_directory = project / "state"
    database = state_directory / "project.sqlite3"
    if not database.is_file():
        payload = _empty_status()
        payload["product_state"] = (
            "BLOCKED"
            if control_error is not None
            else "INITIALIZED"
            if manifest is not None
            else "UNINITIALIZED"
        )
        return _add_control_status(
            payload,
            project=project,
            manifest=manifest,
            control_error=control_error,
        )

    artifacts = PreparedArtifactStore(state_directory / "prepared-artifacts")
    with ProjectStore(database, artifacts) as state:
        payload = dict(state.status())
        tables = {
            str(row[0])
            for row in state.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        latest_report = None
        active_report_id = None
        if "m4_artifact_report" in tables:
            latest_report = state.connection.execute(
                "SELECT report_id, verdict, target_pdf_sha256 "
                "FROM m4_artifact_report ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        if "m4_active_artifact" in tables:
            active = state.connection.execute(
                "SELECT report_id FROM m4_active_artifact WHERE singleton = 1"
            ).fetchone()
            if active is not None:
                active_report_id = str(active["report_id"])
        active_release = False
        if "m1_active_release" in tables:
            active_release = (
                state.connection.execute(
                    "SELECT COUNT(*) FROM m1_active_release"
                ).fetchone()[0]
                > 0
            )

    if control_error is not None:
        product_state = "BLOCKED"
    elif active_report_id is not None:
        product_state = "RELEASED"
    elif latest_report is not None:
        product_state = "BLOCKED"
    elif active_release:
        product_state = "VERIFYING"
    elif int(payload["units"]) > 0:
        product_state = "IN_PROGRESS"
    else:
        product_state = "NEW"

    payload.update(
        {
            "product_state": product_state,
            "active_artifact_report_id": active_report_id,
            "latest_artifact_report_id": (
                str(latest_report["report_id"])
                if latest_report is not None
                else None
            ),
            "latest_artifact_verdict": (
                str(latest_report["verdict"])
                if latest_report is not None
                else None
            ),
        }
    )
    report_path = project / "output" / "verification-report.json"
    payload["verification_report_path"] = (
        str(report_path) if report_path.is_file() else None
    )
    outputs = sorted((project / "output").glob("*.verified.pdf"))
    output = outputs[0] if len(outputs) == 1 else None
    payload["verified_output_pdf"] = str(output) if output is not None else None

    if active_report_id is not None:
        try:
            with VerificationStore(
                database,
                artifacts,
                FinalPDFStore(state_directory / "verified-final-pdfs"),
            ) as verification:
                active_artifact = verification.load_active_artifact()
            if active_artifact is None:
                raise RuntimeError("active final artifact is missing")
            report, _reference = active_artifact
            if output is None or sha256_file(output) != report.target_pdf_sha256:
                raise RuntimeError("published output PDF is missing or differs")
        except Exception as exc:
            payload["product_state"] = "BLOCKED"
            payload["artifact_integrity_error"] = _bounded(str(exc))

    return _add_control_status(
        payload,
        project=project,
        manifest=manifest,
        control_error=control_error,
    )


def doctor_project(project_directory: str | Path) -> dict[str, object]:
    project = Path(project_directory).resolve()
    checks: list[dict[str, object]] = []

    python_ok = (3, 10) <= sys.version_info[:2] < (3, 14)
    _append_check(
        checks,
        "python",
        "PASS" if python_ok else "BLOCK",
        f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    )
    _append_check(checks, "runtime", "PASS", f"pubtrans {__version__}")

    manifest: ProjectControlManifest | None = None
    config: ProductConfig | None = None
    try:
        manifest = load_project_manifest(project)
        config = ProductConfig.load(project / manifest.config_relative_path)
        _append_check(
            checks,
            "project_integrity",
            "PASS",
            f"project {manifest.project_id} is content-bound",
        )
        with pymupdf.open(project / manifest.source_relative_path) as document:
            pages = document.page_count
        _append_check(checks, "source_pdf", "PASS", f"readable PDF with {pages} pages")
    except Exception as exc:
        _append_check(checks, "project_integrity", "BLOCK", _bounded(str(exc)))

    location = _project_location_check(project)
    checks.append(location)

    if config is not None:
        credential = os.environ.get(config.api_key_env, "").strip()
        _append_check(
            checks,
            "provider_credential",
            "PASS" if credential else "BLOCK",
            (
                f"{config.api_key_env} is present"
                if credential
                else f"{config.api_key_env} is empty"
            ),
        )

    try:
        from babeldoc.format.pdf.document_il.midend.document_translation_provider import (  # noqa: E501
            DocumentTranslationProvider,
        )
        from babeldoc.format.pdf.translation_config import TranslationConfig

        if "document_translation_provider" not in TranslationConfig.__dataclass_fields__:
            raise RuntimeError("installed BabelDOC lacks the provider seam")
        if not isinstance(DocumentTranslationProvider, type):
            raise RuntimeError("installed BabelDOC provider contract is malformed")
        _append_check(
            checks,
            "babeldoc_provider",
            "PASS",
            "audited document translation provider seam is available",
        )
    except Exception as exc:
        _append_check(checks, "babeldoc_provider", "BLOCK", _bounded(str(exc)))

    font = manifest.primary_font_family if manifest is not None else None
    if font:
        matcher = shutil.which("fc-match")
        if matcher is None:
            _append_check(
                checks,
                "primary_font",
                "WARN",
                "fc-match is unavailable; BabelDOC must validate the requested font",
            )
        else:
            result = subprocess.run(
                [matcher, "--format=%{family}", font],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            matched = result.stdout.strip()
            _append_check(
                checks,
                "primary_font",
                "PASS" if result.returncode == 0 and matched else "BLOCK",
                matched or f"font family {font} was not resolved",
            )
    else:
        _append_check(
            checks,
            "primary_font",
            "WARN",
            "no primary font override; BabelDOC automatic font selection will be used",
        )

    disk_target = project if project.exists() else project.parent
    try:
        free = shutil.disk_usage(disk_target).free
        if free < 256 * 1024 * 1024:
            status = "BLOCK"
        elif free < 2 * 1024**3:
            status = "WARN"
        else:
            status = "PASS"
        _append_check(
            checks,
            "disk_space",
            status,
            f"{free // (1024 * 1024)} MiB free",
        )
    except OSError as exc:
        _append_check(checks, "disk_space", "BLOCK", _bounded(str(exc)))

    blocked = any(item["status"] == "BLOCK" for item in checks)
    status_payload = project_status(project)
    return {
        "state": "BLOCKED" if blocked else "PASS",
        "runtime_version": __version__,
        "project": str(project),
        "project_id": manifest.project_id if manifest is not None else None,
        "product_state": status_payload["product_state"],
        "checks": checks,
    }


def collect_project(
    project_directory: str | Path,
    destination_directory: str | Path,
) -> dict[str, object]:
    project = Path(project_directory).resolve()
    destination = Path(destination_directory).resolve()
    manifest = load_project_manifest(project)
    status = project_status(project)
    if status["product_state"] != "RELEASED":
        raise OperatorBlockedError("only a RELEASED project can be collected")
    output_value = status.get("verified_output_pdf")
    report_value = status.get("verification_report_path")
    if not isinstance(output_value, str) or not isinstance(report_value, str):
        raise OperatorBlockedError("released project is missing verified deliverables")
    output = Path(output_value)
    report = Path(report_value)
    _require_safe_project_location(destination)
    destination.mkdir(parents=True, exist_ok=True)
    output_target = destination / output.name
    report_target = destination / "verification-report.json"
    _copy_if_compatible(output, output_target)
    _copy_if_compatible(report, report_target)
    delivery = {
        "schema_version": 1,
        "state": "COLLECTED",
        "project_id": manifest.project_id,
        "runtime_version": __version__,
        "source_pdf_sha256": manifest.source_sha256,
        "files": [
            {
                "name": output_target.name,
                "sha256": sha256_file(output_target),
                "size": output_target.stat().st_size,
            },
            {
                "name": report_target.name,
                "sha256": sha256_file(report_target),
                "size": report_target.stat().st_size,
            },
        ],
    }
    delivery_path = destination / "delivery-manifest.json"
    _write_if_compatible(delivery_path, _json_bytes(delivery))
    return {
        **delivery,
        "destination": str(destination),
        "verified_output_pdf": str(output_target),
        "verification_report_path": str(report_target),
        "delivery_manifest_path": str(delivery_path),
    }


def _initialization_payload(
    project: Path,
    manifest: ProjectControlManifest,
    state: str,
) -> dict[str, object]:
    return {
        "state": state,
        "project": str(project),
        "project_id": manifest.project_id,
        "runtime_version": __version__,
        "source_pdf": str(project / manifest.source_relative_path),
        "config": str(project / manifest.config_relative_path),
        "evidence": (
            str(project / manifest.evidence_relative_path)
            if manifest.evidence_relative_path is not None
            else None
        ),
    }


def _empty_status() -> dict[str, object]:
    return {
        "active_artifact_report_id": None,
        "active_approvals": 0,
        "approval_revisions": 0,
        "blockers": 0,
        "latest_artifact_report_id": None,
        "latest_artifact_verdict": None,
        "pending": 0,
        "prepared_contexts": 0,
        "records": 0,
        "safe_exclusions": 0,
        "units": 0,
        "verification_report_path": None,
        "verified_output_pdf": None,
    }


def _add_control_status(
    payload: dict[str, object],
    *,
    project: Path,
    manifest: ProjectControlManifest | None,
    control_error: str | None,
) -> dict[str, object]:
    payload.update(
        {
            "project": str(project),
            "runtime_version": __version__,
            "control_manifest_path": (
                str(project / CONTROL_RELATIVE_PATH)
                if (project / CONTROL_RELATIVE_PATH).is_file()
                else None
            ),
            "project_id": manifest.project_id if manifest is not None else None,
            "control_integrity_error": control_error,
        }
    )
    return payload


def _validate_pdf(path: Path) -> None:
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise OperatorBlockedError("source PDF is password protected")
            if document.page_count < 1:
                raise OperatorBlockedError("source PDF has no pages")
    except OperatorError:
        raise
    except Exception as exc:
        raise OperatorBlockedError("source PDF cannot be opened") from exc


def _verify_bound_file(
    path: Path,
    *,
    expected_sha256: str,
    label: str,
    expected_size: int | None = None,
) -> None:
    if not path.is_file():
        raise OperatorConflictError(f"{label} is missing")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise OperatorConflictError(f"{label} size differs")
    if sha256_file(path) != expected_sha256:
        raise OperatorConflictError(f"{label} digest differs")


def _require_initializable_directory(project: Path) -> None:
    if not project.exists():
        return
    unexpected = sorted(
        str(item.relative_to(project))
        for item in project.rglob("*")
        if item.is_file() or item.is_symlink()
    )
    if unexpected:
        raise OperatorConflictError(
            "project directory contains files but has no control manifest: "
            + ", ".join(unexpected)
        )


def _project_location_check(project: Path) -> dict[str, object]:
    probe = project
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        root_result = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": "git_safety",
            "status": "WARN",
            "message": f"Git safety could not be checked: {_bounded(str(exc))}",
        }
    if root_result.returncode != 0:
        return {
            "name": "git_safety",
            "status": "PASS",
            "message": "project directory is outside a Git worktree",
        }
    root = Path(root_result.stdout.strip()).resolve()
    ignore_result = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "--quiet", str(project)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if ignore_result.returncode == 0:
        return {
            "name": "git_safety",
            "status": "PASS",
            "message": "project directory is ignored by its enclosing Git worktree",
        }
    return {
        "name": "git_safety",
        "status": "BLOCK",
        "message": "project directory is inside Git and is not ignored",
    }


def _require_safe_project_location(project: Path) -> None:
    check = _project_location_check(project)
    if check["status"] == "BLOCK":
        raise OperatorBlockedError(str(check["message"]))


def _append_check(
    checks: list[dict[str, object]],
    name: str,
    status: str,
    message: str,
) -> None:
    checks.append({"name": name, "status": status, "message": message})


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_if_compatible(source: Path, destination: Path) -> None:
    if destination.exists():
        if destination.is_file() and sha256_file(destination) == sha256_file(source):
            return
        raise OperatorConflictError(
            "delivery file already exists with different bytes: "
            f"{destination.name}"
        )
    _atomic_copy(source, destination)


def _write_if_compatible(destination: Path, payload: bytes) -> None:
    if destination.exists():
        if destination.is_file() and destination.read_bytes() == payload:
            return
        raise OperatorConflictError(
            "delivery manifest already exists with different bytes: "
            f"{destination.name}"
        )
    _atomic_write(destination, payload)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bounded(value: str) -> str:
    return " ".join(value.split())[:500]
