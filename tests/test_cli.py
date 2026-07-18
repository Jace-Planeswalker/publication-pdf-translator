from __future__ import annotations

import json

import pymupdf

from pubtrans import __version__
from pubtrans.cli import main


def test_status_is_read_only_for_an_uninitialized_project(tmp_path, capsys) -> None:
    assert main(["status", str(tmp_path)]) == 0

    status = json.loads(capsys.readouterr().out)
    assert status == {
        "active_artifact_report_id": None,
        "active_approvals": 0,
        "approval_revisions": 0,
        "blockers": 0,
        "control_integrity_error": None,
        "control_manifest_path": None,
        "latest_artifact_report_id": None,
        "latest_artifact_verdict": None,
        "pending": 0,
        "prepared_contexts": 0,
        "product_state": "UNINITIALIZED",
        "project": str(tmp_path),
        "project_id": None,
        "records": 0,
        "runtime_version": __version__,
        "safe_exclusions": 0,
        "units": 0,
        "verification_report_path": None,
        "verified_output_pdf": None,
    }
    assert not (tmp_path / "state" / "project.sqlite3").exists()


def test_version_is_available_without_a_subcommand(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    assert capsys.readouterr().out.strip() == __version__


def test_init_command_creates_a_content_bound_project(tmp_path, capsys) -> None:
    source = tmp_path / "source.pdf"
    project = tmp_path / "project"
    document = pymupdf.open()
    document.new_page().insert_text((36, 64), "Operator CLI source")
    document.save(source)
    document.close()

    assert (
        main(
            [
                "init",
                str(source),
                "--project",
                str(project),
                "--model",
                "quality-model",
                "--no-web-research",
            ]
        )
        == 0
    )
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["state"] == "INITIALIZED"

    assert main(["status", str(project)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["product_state"] == "INITIALIZED"
    assert status["project_id"] == initialized["project_id"]
