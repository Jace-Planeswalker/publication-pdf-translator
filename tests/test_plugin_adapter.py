from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "publication-pdf-translator"
SCRIPTS = PLUGIN / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[name] = module
    specification.loader.exec_module(module)
    return module


adapter = _load_module("pubtrans_plugin_adapter", SCRIPTS / "adapter.py")


def _write_fake_runtime(runtime_home: Path) -> Path:
    python = runtime_home / "versions" / "0.3.0" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys
import time

arguments = sys.argv[1:]
if arguments[:2] == ["-I", "-c"]:
    print("0.3.0")
    raise SystemExit(0)
if arguments[:2] != ["-m", "pubtrans"]:
    raise SystemExit(3)
command = arguments[2]
project = pathlib.Path(arguments[3])
if command == "status":
    released = (project / "released.marker").is_file()
    print(json.dumps({
        "product_state": "RELEASED" if released else "INITIALIZED",
        "project": str(project),
        "project_id": "fake-project",
        "verified_output_pdf": str(project / "verified.pdf") if released else None,
        "verification_report_path": str(project / "report.json") if released else None,
    }))
elif command == "doctor":
    print(json.dumps({"state": "PASS", "project": str(project), "checks": []}))
elif command == "run":
    time.sleep(0.05)
    (project / "released.marker").write_text("released", encoding="utf-8")
    print(json.dumps({"state": "RELEASED"}))
else:
    print(json.dumps({"state": "BLOCKED", "message": "unsupported fake command"}))
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return python


def test_bootstrap_accepts_only_the_pinned_valid_runtime(tmp_path) -> None:
    runtime_home = tmp_path / "runtime"
    runtime_python = _write_fake_runtime(runtime_home)

    result = adapter.bootstrap(runtime_home)

    assert result == {
        "state": "EXISTING",
        "runtime_version": "0.3.0",
        "release_ref": "release/v0.3.0",
        "runtime_home": str(runtime_home),
        "runtime_python": str(runtime_python),
    }
    assert "@release/v0.3.0" in adapter.RUNTIME_REQUIREMENT
    assert "publication-pdf-translator[babeldoc]" in adapter.RUNTIME_REQUIREMENT


def test_adapter_rejects_relative_operator_paths(tmp_path) -> None:
    runtime_home = tmp_path / "runtime"
    _write_fake_runtime(runtime_home)

    with pytest.raises(adapter.AdapterError, match="absolute path"):
        adapter.status("relative/project", runtime_home)


def test_start_and_poll_use_a_durable_background_job(tmp_path) -> None:
    runtime_home = tmp_path / "runtime"
    _write_fake_runtime(runtime_home)
    project = tmp_path / "project"
    (project / "control").mkdir(parents=True)

    started = adapter.start(project, runtime_home)
    assert started["state"] == "RUNNING"
    assert started["started"] is True

    result: dict[str, object] = {}
    for _attempt in range(100):
        result = adapter.poll(project, runtime_home)
        if result["state"] == "RELEASED":
            break
        time.sleep(0.01)

    assert result["state"] == "RELEASED"
    assert result["product_state"] == "RELEASED"
    assert result["job"]["job_id"] == started["job"]["job_id"]
    assert Path(result["job"]["stdout_path"]).is_file()
    assert json.loads(
        (project / "control" / "jobs" / "active.json").read_text(
            encoding="utf-8"
        )
    )["job_id"] == started["job"]["job_id"]


def test_stdio_mcp_exposes_and_calls_operator_tools(tmp_path) -> None:
    runtime_home = tmp_path / "runtime"
    _write_fake_runtime(runtime_home)
    project = tmp_path / "project"
    project.mkdir()
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "pubtrans_status",
                "arguments": {"project": str(project)},
            },
        },
    ]
    environment = os.environ.copy()
    environment["PUBTRANS_RUNTIME_HOME"] = str(runtime_home)

    completed = subprocess.run(
        [sys.executable, "-I", str(SCRIPTS / "mcp_server.py")],
        input="".join(json.dumps(item) + "\n" for item in requests),
        check=True,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.stderr == ""
    assert responses[0]["result"]["serverInfo"] == {
        "name": "publication-pdf-translator",
        "version": "0.3.0",
    }
    tool_names = {item["name"] for item in responses[1]["result"]["tools"]}
    assert tool_names == {
        "pubtrans_bootstrap",
        "pubtrans_collect",
        "pubtrans_doctor",
        "pubtrans_init",
        "pubtrans_poll",
        "pubtrans_start",
        "pubtrans_status",
    }
    result = responses[2]["result"]
    assert result["structuredContent"]["product_state"] == "INITIALIZED"
    assert result["content"][0]["type"] == "text"


def test_plugin_bundle_declares_one_canonical_skill_and_local_mcp() -> None:
    manifest = json.loads(
        (PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    mcp = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    skill = (
        PLUGIN / "skills" / "translate-publication-pdf" / "SKILL.md"
    ).read_text(encoding="utf-8")
    metadata = (
        PLUGIN
        / "skills"
        / "translate-publication-pdf"
        / "agents"
        / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert manifest["version"].split("+")[0] == "0.3.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["capabilities"] == ["Read", "Write"]
    server = mcp["mcpServers"]["publication-pdf-translator"]
    assert server["command"] == "python3"
    assert server["args"] == ["scripts/mcp_server.py"]
    assert server["cwd"] == "."
    assert "OPENAI_API_KEY" in server["env_vars"]
    assert marketplace["name"] == "publication-pdf-translator"
    assert marketplace["plugins"] == [
        {
            "name": "publication-pdf-translator",
            "source": {
                "source": "local",
                "path": "./plugins/publication-pdf-translator",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ]
    for tool in (
        "pubtrans_bootstrap",
        "pubtrans_init",
        "pubtrans_doctor",
        "pubtrans_start",
        "pubtrans_poll",
        "pubtrans_status",
        "pubtrans_collect",
    ):
        assert f"`{tool}`" in skill
    assert "value: \"publication-pdf-translator\"" in metadata
    assert "transport: \"stdio\"" in metadata
