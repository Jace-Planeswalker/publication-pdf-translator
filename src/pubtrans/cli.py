"""Small M0 v2 command surface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .m0v2.artifacts import PreparedArtifactStore
from .m0v2.store import ProjectStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pubtrans")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show stored unit status")
    status.add_argument("project", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        database = args.project / "state" / "project.sqlite3"
        artifacts = PreparedArtifactStore(database.with_suffix(".sqlite3.artifacts"))
        with ProjectStore(database, artifacts) as state:
            print(json.dumps(state.status(), ensure_ascii=False, sort_keys=True))
        return 0
    raise AssertionError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
