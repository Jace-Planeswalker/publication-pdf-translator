"""Dependency-free operator adapter for the publication translation runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import venv
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Iterator


RUNTIME_VERSION = "0.3.0"
RELEASE_REF = f"release/v{RUNTIME_VERSION}"
RUNTIME_REQUIREMENT = (
    "publication-pdf-translator[babeldoc] @ "
    "git+https://github.com/Jace-Planeswalker/"
    f"publication-pdf-translator.git@{RELEASE_REF}"
)
DEFAULT_RUNTIME_HOME = Path("~/.local/share/publication-pdf-translator")


class AdapterError(RuntimeError):
    """A safe, structured adapter failure."""

    def __init__(
        self,
        message: str,
        *,
        payload: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.payload = payload or {
            "state": "BLOCKED",
            "error_type": type(self).__name__,
            "message": message,
        }


def bootstrap(runtime_home: str | Path | None = None) -> dict[str, object]:
    """Install the immutable application release into an isolated runtime."""

    home = _runtime_home(runtime_home)
    safety = _git_safety(home)
    if safety["status"] == "BLOCK":
        raise AdapterError(str(safety["message"]))
    target = _version_directory(home)
    existing = _validated_runtime_python(target)
    if existing is not None:
        return _runtime_payload(home, existing, "EXISTING")
    if target.exists():
        raise AdapterError(
            "the pinned runtime directory exists but does not contain pubtrans "
            f"{RUNTIME_VERSION}; choose a clean runtime_home"
        )

    (home / "versions").mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(home / "locks" / f"{RUNTIME_VERSION}.lock"):
        existing = _validated_runtime_python(target)
        if existing is not None:
            return _runtime_payload(home, existing, "EXISTING")
        if target.exists():
            raise AdapterError(
                "the pinned runtime directory became incompatible during bootstrap"
            )
        staging = home / "versions" / (
            f".{RUNTIME_VERSION}.install-{uuid.uuid4().hex}"
        )
        try:
            _install_runtime(staging)
            installed = _validated_runtime_python(staging)
            if installed is None:
                raise AdapterError("installed runtime failed its version self-check")
            _atomic_json(
                staging / "runtime.json",
                {
                    "schema_version": 1,
                    "runtime_version": RUNTIME_VERSION,
                    "release_ref": RELEASE_REF,
                    "requirement": RUNTIME_REQUIREMENT,
                },
            )
            os.replace(staging, target)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
    installed = _validated_runtime_python(target)
    if installed is None:
        raise AdapterError("runtime was installed but cannot be reopened")
    return _runtime_payload(home, installed, "INSTALLED")


def initialize(
    *,
    source_pdf: str | Path,
    project: str | Path,
    config: str | Path | None = None,
    model: str | None = None,
    evidence: str | Path | None = None,
    source_language: str = "en",
    target_language: str = "zh-Hans",
    reasoning_effort: str = "high",
    no_web_research: bool = False,
    skip_scanned_detection: bool = False,
    primary_font_family: str | None = None,
    runtime_home: str | Path | None = None,
) -> dict[str, object]:
    """Create an input-bound resumable project."""

    if (config is None) == (model is None):
        raise AdapterError("exactly one of config or model must be supplied")
    source_path = _absolute_path(source_pdf, "source_pdf")
    project_path = _absolute_path(project, "project")
    arguments = [
        "init",
        str(source_path),
        "--project",
        str(project_path),
    ]
    if config is not None:
        arguments.extend(["--config", str(_absolute_path(config, "config"))])
    else:
        assert model is not None
        arguments.extend(
            [
                "--model",
                _nonempty(model, "model"),
                "--source-language",
                _nonempty(source_language, "source_language"),
                "--target-language",
                _nonempty(target_language, "target_language"),
                "--reasoning-effort",
                _nonempty(reasoning_effort, "reasoning_effort"),
            ]
        )
        if no_web_research:
            arguments.append("--no-web-research")
    if evidence is not None:
        arguments.extend(["--evidence", str(_absolute_path(evidence, "evidence"))])
    if skip_scanned_detection:
        arguments.append("--skip-scanned-detection")
    if primary_font_family is not None:
        arguments.extend(
            [
                "--primary-font-family",
                _nonempty(primary_font_family, "primary_font_family"),
            ]
        )
    return _run_cli(runtime_home, arguments, timeout=300)


def doctor(
    project: str | Path,
    runtime_home: str | Path | None = None,
) -> dict[str, object]:
    """Run preflight checks without mutating project state."""

    project_path = _absolute_path(project, "project")
    return _run_cli(
        runtime_home,
        ["doctor", str(project_path)],
        timeout=120,
        allow_nonzero=True,
    )


def status(
    project: str | Path,
    runtime_home: str | Path | None = None,
) -> dict[str, object]:
    """Read and revalidate durable project state."""

    project_path = _absolute_path(project, "project")
    return _run_cli(runtime_home, ["status", str(project_path)], timeout=120)


def start(
    project: str | Path,
    runtime_home: str | Path | None = None,
) -> dict[str, object]:
    """Start or resume a durable build in a background job."""

    project_path = _absolute_path(project, "project")
    current = status(project_path, runtime_home)
    if current.get("product_state") == "RELEASED":
        return {"state": "RELEASED", "started": False, **current}
    diagnosis = doctor(project_path, runtime_home)
    if diagnosis.get("state") != "PASS":
        raise AdapterError(
            "project preflight is blocked",
            payload={"state": "BLOCKED", "doctor": diagnosis},
        )

    runtime_python = _require_runtime_python(runtime_home)
    jobs = project_path / "control" / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    with _exclusive_lock(jobs / "start.lock"):
        current = status(project_path, runtime_home)
        if current.get("product_state") == "RELEASED":
            return {"state": "RELEASED", "started": False, **current}
        active = _load_active_job(jobs)
        if active is not None and active.get("state") == "RUNNING":
            pid = active.get("runner_pid")
            if isinstance(pid, int) and _pid_alive(pid):
                return _job_result(active, current, started=False)
            active = {
                **active,
                "state": "ORPHANED",
                "completed_at": _utc_now(),
            }
            _atomic_json(_job_record_path(jobs, active), active)

        job_id = uuid.uuid4().hex
        record_path = jobs / f"{job_id}.json"
        stdout_path = jobs / f"{job_id}.stdout.jsonl"
        stderr_path = jobs / f"{job_id}.stderr.jsonl"
        record: dict[str, object] = {
            "schema_version": 1,
            "state": "STARTING",
            "job_id": job_id,
            "project": str(project_path),
            "runtime_version": RUNTIME_VERSION,
            "runtime_python": str(runtime_python),
            "started_at": _utc_now(),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
        }
        _atomic_json(record_path, record)
        try:
            runner = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "_run_job",
                    "--runtime-python",
                    str(runtime_python),
                    "--project",
                    str(project_path),
                    "--record",
                    str(record_path),
                    "--job-id",
                    job_id,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            record.update(
                {
                    "state": "FAILED",
                    "completed_at": _utc_now(),
                    "runner_error": "background runner could not start",
                }
            )
            _atomic_json(record_path, record)
            raise AdapterError("background runner could not start") from exc
        record.update({"state": "RUNNING", "runner_pid": runner.pid})
        _atomic_json(record_path, record)
        _atomic_json(
            jobs / "active.json",
            {"schema_version": 1, "job_id": job_id},
        )
    return _job_result(record, current, started=True)


def poll(
    project: str | Path,
    runtime_home: str | Path | None = None,
) -> dict[str, object]:
    """Poll a background job and revalidate the product state."""

    project_path = _absolute_path(project, "project")
    current = status(project_path, runtime_home)
    jobs = project_path / "control" / "jobs"
    active = _load_active_job(jobs)
    if active is None:
        return {
            "state": (
                "RELEASED"
                if current.get("product_state") == "RELEASED"
                else "IDLE"
            ),
            "job": None,
            **current,
        }
    if active.get("state") == "RUNNING":
        pid = active.get("runner_pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            active = {
                **active,
                "state": "ORPHANED",
                "completed_at": _utc_now(),
            }
            _atomic_json(_job_record_path(jobs, active), active)
    return _job_result(active, current, started=False)


def collect(
    *,
    project: str | Path,
    destination: str | Path,
    runtime_home: str | Path | None = None,
) -> dict[str, object]:
    """Collect only the content-verified released PDF and report."""

    project_path = _absolute_path(project, "project")
    destination_path = _absolute_path(destination, "destination")
    return _run_cli(
        runtime_home,
        [
            "collect",
            str(project_path),
            "--destination",
            str(destination_path),
        ],
        timeout=300,
    )


def _install_runtime(staging: Path) -> None:
    venv.EnvBuilder(with_pip=True).create(staging)
    python = _python_in(staging)
    result = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            RUNTIME_REQUIREMENT,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise AdapterError(
            f"pinned runtime installation failed with exit code {result.returncode}"
        )


def _run_cli(
    runtime_home: str | Path | None,
    arguments: list[str],
    *,
    timeout: int,
    allow_nonzero: bool = False,
) -> dict[str, object]:
    python = _require_runtime_python(runtime_home)
    return _invoke_python(
        python,
        arguments,
        timeout=timeout,
        allow_nonzero=allow_nonzero,
    )


def _invoke_python(
    python: Path,
    arguments: list[str],
    *,
    timeout: int,
    allow_nonzero: bool = False,
) -> dict[str, object]:
    try:
        result = subprocess.run(
            [str(python), "-m", "pubtrans", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AdapterError(f"runtime command could not complete: {exc}") from exc
    payload = _last_json_object(result.stdout, result.stderr)
    if result.returncode != 0 and not allow_nonzero:
        if payload is None:
            raise AdapterError(
                f"runtime command failed with exit code {result.returncode}"
            )
        raise AdapterError(
            str(payload.get("message", "runtime command was blocked")),
            payload=payload,
        )
    if payload is None:
        raise AdapterError("runtime command returned no structured JSON result")
    return payload


def _runtime_home(value: str | Path | None) -> Path:
    if value is None:
        configured = os.environ.get("PUBTRANS_RUNTIME_HOME", "").strip()
        candidate = Path(configured) if configured else DEFAULT_RUNTIME_HOME
        return candidate.expanduser().resolve()
    return _absolute_path(value, "runtime_home")


def _version_directory(home: Path) -> Path:
    return home / "versions" / RUNTIME_VERSION


def _require_runtime_python(runtime_home: str | Path | None) -> Path:
    home = _runtime_home(runtime_home)
    python = _validated_runtime_python(_version_directory(home))
    if python is None:
        raise AdapterError(
            f"pubtrans {RUNTIME_VERSION} is not bootstrapped; run "
            "pubtrans_bootstrap first"
        )
    return python


def _validated_runtime_python(version_directory: Path) -> Path | None:
    python = _python_in(version_directory)
    if not python.is_file():
        return None
    try:
        result = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import pubtrans; print(pubtrans.__version__)",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or result.stdout.strip() != RUNTIME_VERSION:
        return None
    return python.absolute()


def _python_in(directory: Path) -> Path:
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def _runtime_payload(home: Path, python: Path, state: str) -> dict[str, object]:
    return {
        "state": state,
        "runtime_version": RUNTIME_VERSION,
        "release_ref": RELEASE_REF,
        "runtime_home": str(home),
        "runtime_python": str(python),
    }


def _absolute_path(value: str | Path, label: str) -> Path:
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise AdapterError(f"{label} must be an absolute path")
    return raw.resolve()


def _nonempty(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise AdapterError(f"{label} must not be empty")
    return normalized


def _last_json_object(*streams: str) -> dict[str, object] | None:
    for stream in streams:
        for line in reversed(stream.splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
    return None


def _job_result(
    record: dict[str, object],
    current: dict[str, object],
    *,
    started: bool,
) -> dict[str, object]:
    product_state = current.get("product_state")
    if product_state == "RELEASED":
        state = "RELEASED"
    elif product_state == "BLOCKED" or record.get("state") == "COMPLETED":
        state = "BLOCKED"
    else:
        state = str(record["state"])
    return {
        "state": state,
        "started": started,
        "product_state": product_state,
        "project": current.get("project"),
        "project_id": current.get("project_id"),
        "verified_output_pdf": current.get("verified_output_pdf"),
        "verification_report_path": current.get("verification_report_path"),
        "job": {
            key: record.get(key)
            for key in (
                "job_id",
                "state",
                "runner_pid",
                "started_at",
                "completed_at",
                "exit_code",
                "stdout_path",
                "stderr_path",
            )
            if record.get(key) is not None
        },
    }


def _load_active_job(jobs: Path) -> dict[str, object] | None:
    active_path = jobs / "active.json"
    if not active_path.is_file():
        return None
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        job_id = active["job_id"]
        if not _valid_job_id(job_id):
            raise ValueError("invalid job id")
        record_path = jobs / f"{job_id}.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AdapterError("active job metadata is malformed") from exc
    if not isinstance(record, dict) or record.get("job_id") != job_id:
        raise AdapterError("active job record does not match its pointer")
    return record


def _job_record_path(jobs: Path, record: dict[str, object]) -> Path:
    job_id = record.get("job_id")
    if not _valid_job_id(job_id):
        raise AdapterError("job record has an invalid identifier")
    return jobs / f"{job_id}.json"


def _run_job(
    *,
    runtime_python: Path,
    project: Path,
    record_path: Path,
    job_id: str,
) -> int:
    try:
        record = _wait_for_running_record(record_path, job_id)
        if not isinstance(record, dict) or record.get("job_id") != job_id:
            raise AdapterError("job identity does not match its record")
        stdout_path = Path(str(record["stdout_path"]))
        stderr_path = Path(str(record["stderr_path"]))
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            result = subprocess.run(
                [str(runtime_python), "-m", "pubtrans", "run", str(project)],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
        record.update(
            {
                "state": "COMPLETED" if result.returncode == 0 else "FAILED",
                "exit_code": result.returncode,
                "completed_at": _utc_now(),
            }
        )
        _atomic_json(record_path, record)
        return result.returncode
    except Exception as exc:
        try:
            record = (
                record
                if isinstance(record, dict)
                else {"schema_version": 1, "job_id": job_id}
            )
        except UnboundLocalError:
            record = {"schema_version": 1, "job_id": job_id}
        record.update(
            {
                "state": "FAILED",
                "completed_at": _utc_now(),
                "runner_error": str(exc)[:500],
            }
        )
        _atomic_json(record_path, record)
        return 2


def _wait_for_running_record(
    record_path: Path,
    job_id: str,
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("job_id") != job_id:
            raise AdapterError("job identity does not match its record")
        if record.get("state") == "RUNNING":
            return record
        if record.get("state") != "STARTING":
            raise AdapterError("job left STARTING before its runner was ready")
        time.sleep(0.01)
    raise AdapterError("job runner timed out waiting for its launch record")


def _valid_job_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        if _stale_lock(path):
            path.unlink(missing_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        else:
            raise AdapterError(f"operation lock is already held: {path.name}") from exc
    try:
        payload = json.dumps({"pid": os.getpid(), "created": time.time()}).encode()
        os.write(descriptor, payload)
        os.close(descriptor)
        yield
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        path.unlink(missing_ok=True)


def _stale_lock(path: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        created = float(payload["created"])
        pid = int(payload["pid"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return time.time() - created > 7200 and not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _git_safety(path: Path) -> dict[str, str]:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        root = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {
            "status": "WARN",
            "message": "Git safety could not be checked for runtime_home",
        }
    if root.returncode != 0:
        return {"status": "PASS", "message": "runtime_home is outside Git"}
    ignored = subprocess.run(
        [
            "git",
            "-C",
            root.stdout.strip(),
            "check-ignore",
            "--no-index",
            "--quiet",
            str(path),
        ],
        check=False,
        capture_output=True,
        timeout=10,
    )
    if ignored.returncode == 0:
        return {"status": "PASS", "message": "runtime_home is ignored by Git"}
    return {
        "status": "BLOCK",
        "message": "runtime_home is inside Git and is not ignored",
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _build_internal_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("operation", choices=["_run_job"])
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    return parser


if __name__ == "__main__":
    arguments = _build_internal_parser().parse_args()
    raise SystemExit(
        _run_job(
            runtime_python=arguments.runtime_python,
            project=arguments.project,
            record_path=arguments.record,
            job_id=arguments.job_id,
        )
    )
