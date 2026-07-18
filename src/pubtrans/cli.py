"""One-command product surface plus truthful project status."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .m0v2.artifacts import PreparedArtifactStore
from .m0v2.store import ProjectStore
from .m4.artifacts import FinalPDFStore
from .m4.store import VerificationStore
from .m4.verifier import sha256_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pubtrans")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show stored unit status")
    status.add_argument("project", type=Path)

    translate = subparsers.add_parser(
        "translate",
        help="create or resume a verified publication PDF translation",
    )
    translate.add_argument("source_pdf", type=Path)
    translate.add_argument("--project", type=Path)
    translate.add_argument("--config", type=Path)
    translate.add_argument("--model")
    translate.add_argument("--source-language", default="en")
    translate.add_argument("--target-language", default="zh-Hans")
    translate.add_argument("--api-key-env", default="OPENAI_API_KEY")
    translate.add_argument("--base-url", default="https://api.openai.com/v1")
    translate.add_argument("--reasoning-effort", default="high")
    translate.add_argument("--evidence", type=Path)
    translate.add_argument("--no-web-research", action="store_true")
    translate.add_argument("--skip-scanned-detection", action="store_true")
    translate.add_argument("--primary-font-family")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        print(
            json.dumps(
                _project_status(args.project),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "translate":
        return _translate(args)
    raise AssertionError(f"unhandled command: {args.command}")


def _translate(args: argparse.Namespace) -> int:
    from .m5.config import ProductConfig
    from .m5.evidence import EvidenceCatalog
    from .m5.openai import OpenAIResponsesClient
    from .m5.runtime import PublicationTranslationRuntime

    try:
        if args.config is not None:
            config = ProductConfig.load(args.config)
        else:
            if not args.model:
                raise ValueError("--model is required when --config is not supplied")
            config = ProductConfig.create(
                source_language=args.source_language,
                target_language=args.target_language,
                default_model=args.model,
                api_key_env=args.api_key_env,
                base_url=args.base_url,
                reasoning_effort=args.reasoning_effort,
                enable_web_research=not args.no_web_research,
            )
        client = OpenAIResponsesClient(
            api_key=config.api_key(),
            base_url=config.base_url,
            timeout_seconds=config.request_timeout_seconds,
        )
        project = args.project or args.source_pdf.with_suffix(".pubtrans")
        result = PublicationTranslationRuntime(
            config=config,
            structured_client=client,
            research_client=client if config.enable_web_research else None,
            evidence_catalog=EvidenceCatalog.load(args.evidence),
            skip_scanned_detection=args.skip_scanned_detection,
            primary_font_family=args.primary_font_family,
        ).run(source_pdf=args.source_pdf, project_directory=project)
        print(json.dumps(result.as_payload(), ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        payload = {
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
        return 2


def _project_status(project: Path) -> dict[str, object]:
    project = project.resolve()
    state_directory = project / "state"
    database = state_directory / "project.sqlite3"
    artifacts = PreparedArtifactStore(state_directory / "prepared-artifacts")
    with ProjectStore(database, artifacts) as state:
        payload: dict[str, object] = dict(state.status())
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

    if active_report_id is not None:
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
            payload["artifact_integrity_error"] = _sanitize(str(exc))
    return payload


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
