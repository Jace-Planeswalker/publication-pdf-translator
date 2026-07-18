from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pymupdf
import pytest

import pubtrans.cli as cli
import pubtrans.operator as operator
from pubtrans.m5.config import ProductConfig
from pubtrans.operator import OperatorBlockedError
from pubtrans.operator import OperatorConflictError
from pubtrans.operator import collect_project
from pubtrans.operator import controlled_run_inputs
from pubtrans.operator import doctor_project
from pubtrans.operator import initialize_project
from pubtrans.operator import load_project_manifest
from pubtrans.operator import project_status


def _write_pdf(path: Path, text: str = "A publication source") -> None:
    document = pymupdf.open()
    page = document.new_page(width=320, height=240)
    page.insert_text((36, 64), text)
    document.save(path)
    document.close()


def _config(model: str = "quality-model") -> ProductConfig:
    return ProductConfig.create(default_model=model)


def test_initialize_binds_inputs_and_is_idempotent(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    _write_pdf(source)

    initialized = initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
        skip_scanned_detection=True,
        primary_font_family="Source Han Serif SC",
    )

    assert initialized["state"] == "INITIALIZED"
    manifest = load_project_manifest(project)
    assert manifest.project_id == initialized["project_id"]
    assert manifest.source_name == "source.pdf"
    assert manifest.skip_scanned_detection is True
    assert controlled_run_inputs(project).primary_font_family == (
        "Source Han Serif SC"
    )
    assert project_status(project)["product_state"] == "INITIALIZED"
    assert not (project / "state" / "project.sqlite3").exists()

    existing = initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
        skip_scanned_detection=True,
        primary_font_family="Source Han Serif SC",
    )
    assert existing["state"] == "EXISTING"
    assert existing["project_id"] == initialized["project_id"]


def test_bound_input_mutation_blocks_resume(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    _write_pdf(source)
    initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
    )

    (project / "inputs" / "config.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(OperatorConflictError, match="digest differs"):
        load_project_manifest(project)
    status = project_status(project)
    assert status["product_state"] == "BLOCKED"
    assert status["project_id"] is None


def test_manifest_rejects_unknown_and_coerced_fields(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    _write_pdf(source)
    initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
    )
    manifest_path = project / "control" / "project.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["skip_scanned_detection"] = "false"
    payload["unexpected"] = "field"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(OperatorConflictError, match="fields do not match"):
        load_project_manifest(project)

    payload.pop("unexpected")
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(OperatorConflictError, match="not a boolean"):
        load_project_manifest(project)


def test_initialization_will_not_claim_a_directory_with_files(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    _write_pdf(source)
    (project / "inputs").mkdir(parents=True)
    (project / "inputs" / "unmanaged.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(OperatorConflictError, match="contains files"):
        initialize_project(
            source_pdf=source,
            project_directory=project,
            config=_config(),
        )


def test_git_location_must_be_ignored(tmp_path) -> None:
    source = tmp_path / "source.pdf"
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    _write_pdf(source)
    project = repository / "projects" / "book"

    with pytest.raises(OperatorBlockedError, match="inside Git"):
        initialize_project(
            source_pdf=source,
            project_directory=project,
            config=_config(),
        )

    (repository / ".gitignore").write_text("projects/\n", encoding="utf-8")
    result = initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
    )
    assert result["state"] == "INITIALIZED"


def test_doctor_reports_missing_credential_without_leaking_it(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    _write_pdf(source)
    initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
    )
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    result = doctor_project(project)

    checks = {item["name"]: item for item in result["checks"]}
    assert result["state"] == "BLOCKED"
    assert checks["project_integrity"]["status"] == "PASS"
    assert checks["provider_credential"] == {
        "name": "provider_credential",
        "status": "BLOCK",
        "message": "OPENAI_API_KEY is empty",
    }
    assert "quality-model" not in json.dumps(result)


def test_controlled_cli_uses_only_bound_inputs(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    _write_pdf(source)
    initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
        skip_scanned_detection=True,
    )
    captured: dict[str, object] = {}

    def fake_execute_runtime(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "_execute_runtime", fake_execute_runtime)

    assert cli.main(["resume", str(project)]) == 0
    assert captured["source_pdf"] == project / "inputs" / "source.pdf"
    assert captured["project"] == project
    assert isinstance(captured["config"], ProductConfig)
    assert captured["skip_scanned_detection"] is True


def test_collect_copies_only_a_released_verified_bundle(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    destination = tmp_path / "delivery"
    _write_pdf(source)
    initialize_project(
        source_pdf=source,
        project_directory=project,
        config=_config(),
    )
    output = project / "output" / "source.zh-Hans.verified.pdf"
    report = project / "output" / "verification-report.json"
    output.parent.mkdir()
    output.write_bytes(b"verified-pdf")
    report.write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    monkeypatch.setattr(
        operator,
        "project_status",
        lambda _project: {
            "product_state": "RELEASED",
            "verified_output_pdf": str(output),
            "verification_report_path": str(report),
        },
    )

    first = collect_project(project, destination)
    second = collect_project(project, destination)

    assert first == second
    assert Path(str(first["verified_output_pdf"])).read_bytes() == b"verified-pdf"
    delivery = json.loads(
        Path(str(first["delivery_manifest_path"])).read_text(encoding="utf-8")
    )
    assert delivery["state"] == "COLLECTED"
    assert {item["name"] for item in delivery["files"]} == {
        "source.zh-Hans.verified.pdf",
        "verification-report.json",
    }

    (destination / "source.zh-Hans.verified.pdf").write_bytes(b"changed")
    with pytest.raises(OperatorConflictError, match="different bytes"):
        collect_project(project, destination)
