from __future__ import annotations

import json

from pubtrans.cli import main


def test_status_uses_m0v2_store(tmp_path, capsys) -> None:
    assert main(["status", str(tmp_path)]) == 0

    status = json.loads(capsys.readouterr().out)
    assert status == {
        "active_approvals": 0,
        "approval_revisions": 0,
        "blockers": 0,
        "pending": 0,
        "prepared_contexts": 0,
        "records": 0,
        "safe_exclusions": 0,
        "units": 0,
    }
    assert (tmp_path / "state" / "project.sqlite3").is_file()
