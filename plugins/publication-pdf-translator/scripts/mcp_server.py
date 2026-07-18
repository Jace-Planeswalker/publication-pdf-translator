"""Minimal stdio MCP server for the publication translation operator adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import adapter  # noqa: E402


SERVER_NAME = "publication-pdf-translator"
SERVER_VERSION = "0.3.0"


def _path_property(description: str) -> dict[str, object]:
    return {"type": "string", "description": description}


COMMON_RUNTIME_PROPERTY = {
    "runtime_home": _path_property(
        "Optional absolute runtime directory; defaults to "
        "PUBTRANS_RUNTIME_HOME or ~/.local/share/publication-pdf-translator."
    )
}


TOOLS: list[dict[str, object]] = [
    {
        "name": "pubtrans_bootstrap",
        "description": (
            "Install or verify the pinned, isolated publication-pdf-translator "
            "runtime. Call once before other tools."
        ),
        "inputSchema": {
            "type": "object",
            "properties": COMMON_RUNTIME_PROPERTY,
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Bootstrap publication translator",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "pubtrans_init",
        "description": (
            "Create a durable project bound to a source PDF, product config or "
            "model settings, and optional terminology evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **COMMON_RUNTIME_PROPERTY,
                "source_pdf": _path_property("Absolute path to the source PDF."),
                "project": _path_property(
                    "Absolute path to the durable project directory."
                ),
                "config": _path_property(
                    "Absolute path to a secret-free product config JSON."
                ),
                "model": {
                    "type": "string",
                    "description": "Model name used when config is omitted.",
                },
                "evidence": _path_property(
                    "Optional absolute path to terminology evidence JSON."
                ),
                "source_language": {"type": "string", "default": "en"},
                "target_language": {"type": "string", "default": "zh-Hans"},
                "reasoning_effort": {
                    "type": "string",
                    "enum": ["none", "low", "medium", "high", "xhigh"],
                    "default": "high",
                },
                "no_web_research": {"type": "boolean", "default": False},
                "skip_scanned_detection": {
                    "type": "boolean",
                    "default": False,
                },
                "primary_font_family": {"type": "string"},
            },
            "required": ["source_pdf", "project"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Initialize publication project",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "pubtrans_doctor",
        "description": (
            "Read-only preflight of project integrity, provider seam, credential "
            "presence, font resolution, Git safety, and disk space."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **COMMON_RUNTIME_PROPERTY,
                "project": _path_property("Absolute project directory."),
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Check publication project",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "pubtrans_start",
        "description": (
            "Start or resume the initialized build as a durable background job "
            "after repeating preflight checks."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **COMMON_RUNTIME_PROPERTY,
                "project": _path_property("Absolute project directory."),
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Start publication translation",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    },
    {
        "name": "pubtrans_poll",
        "description": (
            "Poll the active background job and independently revalidate durable "
            "project and final-artifact state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **COMMON_RUNTIME_PROPERTY,
                "project": _path_property("Absolute project directory."),
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Poll publication translation",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "pubtrans_status",
        "description": (
            "Read durable project state and revalidate any released PDF bytes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **COMMON_RUNTIME_PROPERTY,
                "project": _path_property("Absolute project directory."),
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Inspect publication project",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "pubtrans_collect",
        "description": (
            "Copy a RELEASED PDF and verification report into a content-addressed "
            "delivery bundle. Refuses all non-released projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                **COMMON_RUNTIME_PROPERTY,
                "project": _path_property("Absolute project directory."),
                "destination": _path_property(
                    "Absolute destination directory for verified deliverables."
                ),
            },
            "required": ["project", "destination"],
            "additionalProperties": False,
        },
        "annotations": {
            "title": "Collect verified publication",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


def _call_bootstrap(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.bootstrap(arguments.get("runtime_home"))


def _call_init(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.initialize(**arguments)


def _call_doctor(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.doctor(**arguments)


def _call_start(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.start(**arguments)


def _call_poll(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.poll(**arguments)


def _call_status(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.status(**arguments)


def _call_collect(arguments: dict[str, object]) -> dict[str, object]:
    return adapter.collect(**arguments)


CALLS: dict[str, Callable[[dict[str, object]], dict[str, object]]] = {
    "pubtrans_bootstrap": _call_bootstrap,
    "pubtrans_init": _call_init,
    "pubtrans_doctor": _call_doctor,
    "pubtrans_start": _call_start,
    "pubtrans_poll": _call_poll,
    "pubtrans_status": _call_status,
    "pubtrans_collect": _call_collect,
}


def _result(payload: dict[str, object], *, is_error: bool = False) -> dict[str, object]:
    result: dict[str, object] = {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            }
        ],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _handle(message: dict[str, object]) -> dict[str, object] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        params = message.get("params")
        protocol = (
            params.get("protocolVersion")
            if isinstance(params, dict)
            else "2025-06-18"
        )
        if not isinstance(protocol, str) or not protocol:
            protocol = "2025-06-18"
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOLS},
        }
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            return _protocol_error(request_id, -32602, "params must be an object")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in CALLS:
            return _protocol_error(request_id, -32602, "unknown tool name")
        if not isinstance(arguments, dict):
            return _protocol_error(request_id, -32602, "arguments must be an object")
        try:
            payload = CALLS[name](arguments)
            tool_result = _result(payload)
        except adapter.AdapterError as exc:
            tool_result = _result(exc.payload, is_error=True)
        except (TypeError, ValueError) as exc:
            tool_result = _result(
                {
                    "state": "BLOCKED",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                },
                is_error=True,
            )
        except Exception as exc:
            tool_result = _result(
                {
                    "state": "BLOCKED",
                    "error_type": type(exc).__name__,
                    "message": "unexpected adapter failure",
                },
                is_error=True,
            )
        return {"jsonrpc": "2.0", "id": request_id, "result": tool_result}
    return _protocol_error(request_id, -32601, "method not found")


def _protocol_error(
    request_id: object,
    code: int,
    message: str,
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def main() -> int:
    for raw_line in sys.stdin:
        try:
            message = json.loads(raw_line)
            if not isinstance(message, dict):
                raise ValueError("request must be an object")
            response = _handle(message)
        except (ValueError, json.JSONDecodeError) as exc:
            response = _protocol_error(None, -32700, str(exc)[:500])
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
