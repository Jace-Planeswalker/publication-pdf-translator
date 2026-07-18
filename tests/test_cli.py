from __future__ import annotations

import json

from pubtrans.cli import main


def test_status_uses_m0v2_store(tmp_path, capsys) -> None:
    assert main(["status", str(tmp_path)]) == 0

    status = json.loads(capsys.readouterr().out)
    assert status == {
        "active_artifact_report_id": None,
        "active_approvals": 0,
        "approval_revisions": 0,
        "blockers": 0,
        "latest_artifact_report_id": None,
        "latest_artifact_verdict": None,
        "pending": 0,
        "prepared_contexts": 0,
        "product_state": "NEW",
        "records": 0,
        "safe_exclusions": 0,
        "units": 0,
        "verification_report_path": None,
        "verified_output_pdf": None,
    }
    assert (tmp_path / "state" / "project.sqlite3").is_file()
