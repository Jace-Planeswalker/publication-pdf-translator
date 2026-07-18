"""Operator CLI for initializing, running, diagnosing, and collecting projects."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pubtrans import __version__
from pubtrans.m5.config import ProductConfig
from pubtrans.m5.evidence import EvidenceCatalog
from pubtrans.operator import collect_project
from pubtrans.operator import controlled_run_inputs
from pubtrans.operator import doctor_project
from pubtrans.operator import initialize_project
from pubtrans.operator import project_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pubtrans")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="inspect durable project state")
    status.add_argument("project", type=Path)

    doctor = subparsers.add_parser(
        "doctor",
        help="check runtime, project, provider, credential, font, and disk readiness",
    )
    doctor.add_argument("project", type=Path)

    initialize = subparsers.add_parser(
        "init",
        help="create an immutable, resumable operator project",
    )
    initialize.add_argument("source_pdf", type=Path)
    initialize.add_argument("--project", type=Path, required=True)
    _add_configuration_arguments(initialize, require_one=True)
    initialize.add_argument("--evidence", type=Path)
    initialize.add_argument("--skip-scanned-detection", action="store_true")
    initialize.add_argument("--primary-font-family")

    for command in ("run", "resume"):
        controlled = subparsers.add_parser(
            command,
            help="run or resume an initialized project to a verified final PDF",
        )
        controlled.add_argument("project", type=Path)

    collect = subparsers.add_parser(
        "collect",
        help="copy a RELEASED PDF and report into a verified delivery bundle",
    )
    collect.add_argument("project", type=Path)
    collect.add_argument("--destination", type=Path, required=True)

    translate = subparsers.add_parser(
        "translate",
        help="legacy-compatible one-command create or resume operation",
    )
    translate.add_argument("source_pdf", type=Path)
    translate.add_argument("--project", type=Path)
    _add_configuration_arguments(translate, require_one=False)
    translate.add_argument("--evidence", type=Path)
    translate.add_argument("--skip-scanned-detection", action="store_true")
    translate.add_argument("--primary-font-family")

    return parser


def _add_configuration_arguments(
    parser: argparse.ArgumentParser,
    *,
    require_one: bool,
) -> None:
    group = parser.add_mutually_exclusive_group(required=require_one)
    group.add_argument("--config", type=Path)
    group.add_argument("--model")
    parser.add_argument("--source-language", default="en")
    parser.add_argument("--target-language", default="zh-Hans")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--no-web-research", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            _emit(project_status(args.project))
            return 0
        if args.command == "doctor":
            result = doctor_project(args.project)
            _emit(result)
            return 0 if result["state"] == "PASS" else 2
        if args.command == "init":
            config = _config_from_args(args)
            _emit(
                initialize_project(
                    source_pdf=args.source_pdf,
                    project_directory=args.project,
                    config=config,
                    evidence_path=args.evidence,
                    skip_scanned_detection=args.skip_scanned_detection,
                    primary_font_family=args.primary_font_family,
                )
            )
            return 0
        if args.command in {"run", "resume"}:
            return _run_controlled(args.project)
        if args.command == "collect":
            _emit(collect_project(args.project, args.destination))
            return 0
        if args.command == "translate":
            return _translate(args)
        raise AssertionError(f"unhandled command: {args.command}")
    except Exception as exc:
        _emit_error(exc)
        return 2


def _config_from_args(args: argparse.Namespace) -> ProductConfig:
    if args.config is not None:
        return ProductConfig.load(args.config)
    if not args.model:
        raise ValueError("--model is required when --config is not supplied")
    return ProductConfig.create(
        source_language=args.source_language,
        target_language=args.target_language,
        default_model=args.model,
        api_key_env=args.api_key_env,
        base_url=args.base_url,
        reasoning_effort=args.reasoning_effort,
        enable_web_research=not args.no_web_research,
    )


def _translate(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    project = args.project or args.source_pdf.with_suffix(".pubtrans")
    return _execute_runtime(
        source_pdf=args.source_pdf,
        project=project,
        config=config,
        evidence=args.evidence,
        skip_scanned_detection=args.skip_scanned_detection,
        primary_font_family=args.primary_font_family,
    )


def _run_controlled(project: Path) -> int:
    inputs = controlled_run_inputs(project)
    return _execute_runtime(
        source_pdf=inputs.source_pdf,
        project=inputs.project,
        config=ProductConfig.load(inputs.config),
        evidence=inputs.evidence,
        skip_scanned_detection=inputs.skip_scanned_detection,
        primary_font_family=inputs.primary_font_family,
    )


def _execute_runtime(
    *,
    source_pdf: Path,
    project: Path,
    config: ProductConfig,
    evidence: Path | None,
    skip_scanned_detection: bool,
    primary_font_family: str | None,
) -> int:
    from pubtrans.m5.openai import OpenAIResponsesClient
    from pubtrans.m5.runtime import PublicationTranslationRuntime

    client = OpenAIResponsesClient(
        api_key=config.api_key(),
        base_url=config.base_url,
        timeout_seconds=config.request_timeout_seconds,
    )
    result = PublicationTranslationRuntime(
        config=config,
        structured_client=client,
        research_client=client if config.enable_web_research else None,
        evidence_catalog=EvidenceCatalog.load(evidence),
        skip_scanned_detection=skip_scanned_detection,
        primary_font_family=primary_font_family,
    ).run(source_pdf=source_pdf, project_directory=project)
    _emit(result.as_payload())
    return 0


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _emit_error(exc: Exception) -> None:
    payload: dict[str, object] = {
        "state": "BLOCKED",
        "error_type": type(exc).__name__,
        "message": _sanitize(str(exc)),
    }
    report = getattr(exc, "report", None)
    if report is not None:
        payload["artifact_report_id"] = str(report.report_id)
        payload["artifact_findings"] = len(report.findings)
    report_path = getattr(exc, "report_path", None)
    if report_path is not None:
        payload["verification_report_path"] = str(report_path)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def _sanitize(value: str) -> str:
    result = value[:2000]
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
