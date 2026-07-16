"""Transactional SQLite storage for prepared and approved units."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .errors import StateConflictError
from .models import ApprovedTranslation
from .models import PreparedUnit
from .validation import validate_approved_translations


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS unit (
    unit_id TEXT PRIMARY KEY,
    document_sha256 TEXT NOT NULL,
    page_number INTEGER NOT NULL,
    paragraph_debug_id TEXT NOT NULL,
    reading_order INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    placeholder_tokens_json TEXT NOT NULL,
    placeholder_pairs_json TEXT NOT NULL,
    placeholder_signature TEXT NOT NULL,
    layout_label TEXT
);
CREATE TABLE IF NOT EXISTS approval (
    unit_id TEXT PRIMARY KEY REFERENCES unit(unit_id) ON DELETE RESTRICT,
    source_sha256 TEXT NOT NULL,
    placeholder_signature TEXT NOT NULL,
    target_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    unit_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class ProjectState:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "ProjectState":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def register_units(self, units: Iterable[PreparedUnit]) -> None:
        units = list(units)
        unit_by_id = {unit.unit_id: unit for unit in units}
        if len(unit_by_id) != len(units):
            raise StateConflictError("duplicate unit ids in document snapshot")
        document_hashes = {unit.document_sha256 for unit in units}
        if len(document_hashes) > 1:
            raise StateConflictError("one unit snapshot cannot mix documents")

        with self.connection:
            existing_ids = {
                row[0] for row in self.connection.execute("SELECT unit_id FROM unit")
            }
            incoming_ids = set(unit_by_id)
            if existing_ids and existing_ids != incoming_ids:
                raise StateConflictError(
                    "prepared unit set changed; refusing to mix document snapshots"
                )

            for unit in units:
                existing = self.connection.execute(
                    "SELECT * FROM unit WHERE unit_id = ?",
                    (unit.unit_id,),
                ).fetchone()
                stored_values = (
                    (
                        existing["document_sha256"],
                        existing["page_number"],
                        existing["paragraph_debug_id"],
                        existing["reading_order"],
                        existing["source_text"],
                        existing["source_sha256"],
                        existing["placeholder_tokens_json"],
                        existing["placeholder_pairs_json"],
                        existing["placeholder_signature"],
                        existing["layout_label"],
                    )
                    if existing
                    else None
                )
                incoming_values = (
                    unit.document_sha256,
                    unit.page_number,
                    unit.paragraph_debug_id,
                    unit.reading_order,
                    unit.source_text,
                    unit.source_sha256,
                    json.dumps(unit.placeholder_tokens, ensure_ascii=False),
                    json.dumps(unit.placeholder_pairs, ensure_ascii=False),
                    unit.placeholder_signature,
                    unit.layout_label,
                )
                if stored_values is not None:
                    if stored_values != incoming_values:
                        raise StateConflictError(
                            f"immutable unit changed: {unit.unit_id}"
                        )
                    continue
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO unit (
                        unit_id, document_sha256, page_number,
                        paragraph_debug_id, reading_order, source_text,
                        source_sha256, placeholder_tokens_json,
                        placeholder_pairs_json, placeholder_signature, layout_label
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.unit_id,
                        unit.document_sha256,
                        unit.page_number,
                        unit.paragraph_debug_id,
                        unit.reading_order,
                        unit.source_text,
                        unit.source_sha256,
                        json.dumps(unit.placeholder_tokens, ensure_ascii=False),
                        json.dumps(unit.placeholder_pairs, ensure_ascii=False),
                        unit.placeholder_signature,
                        unit.layout_label,
                    ),
                )
                self.connection.execute(
                    "INSERT INTO event(event_type, unit_id) VALUES (?, ?)",
                    ("unit_registered", unit.unit_id),
                )

    def record_approvals(
        self,
        units: Iterable[PreparedUnit],
        approvals: Iterable[ApprovedTranslation],
    ) -> None:
        units = list(units)
        approvals = list(approvals)
        approval_by_id = validate_approved_translations(units, approvals)
        with self.connection:
            for unit in units:
                approval = approval_by_id[unit.unit_id]
                existing = self.connection.execute(
                    "SELECT source_sha256, placeholder_signature, target_text "
                    "FROM approval WHERE unit_id = ?",
                    (unit.unit_id,),
                ).fetchone()
                incoming = (
                    approval.source_sha256,
                    approval.placeholder_signature,
                    approval.target_text,
                )
                if existing is not None:
                    stored = tuple(existing)
                    if stored != incoming:
                        raise StateConflictError(
                            f"approved translation is immutable: {unit.unit_id}"
                        )
                    continue
                self.connection.execute(
                    """
                    INSERT INTO approval (
                        unit_id, source_sha256, placeholder_signature, target_text
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        approval.unit_id,
                        approval.source_sha256,
                        approval.placeholder_signature,
                        approval.target_text,
                    ),
                )
                self.connection.execute(
                    "INSERT INTO event(event_type, unit_id) VALUES (?, ?)",
                    ("unit_approved", unit.unit_id),
                )

    def load_units(self) -> list[PreparedUnit]:
        rows = self.connection.execute(
            "SELECT * FROM unit ORDER BY page_number, reading_order"
        ).fetchall()
        return [
            PreparedUnit(
                unit_id=row["unit_id"],
                document_sha256=row["document_sha256"],
                page_number=row["page_number"],
                paragraph_debug_id=row["paragraph_debug_id"],
                reading_order=row["reading_order"],
                source_text=row["source_text"],
                source_sha256=row["source_sha256"],
                placeholder_tokens=tuple(json.loads(row["placeholder_tokens_json"])),
                placeholder_pairs=tuple(
                    tuple(pair) for pair in json.loads(row["placeholder_pairs_json"])
                ),
                placeholder_signature=row["placeholder_signature"],
                layout_label=row["layout_label"],
            )
            for row in rows
        ]

    def load_approvals(self) -> list[ApprovedTranslation]:
        rows = self.connection.execute(
            "SELECT * FROM approval ORDER BY unit_id"
        ).fetchall()
        return [
            ApprovedTranslation(
                unit_id=row["unit_id"],
                source_sha256=row["source_sha256"],
                placeholder_signature=row["placeholder_signature"],
                target_text=row["target_text"],
            )
            for row in rows
        ]

    def status(self) -> dict[str, int]:
        unit_count = self.connection.execute("SELECT COUNT(*) FROM unit").fetchone()[0]
        approval_count = self.connection.execute(
            "SELECT COUNT(*) FROM approval"
        ).fetchone()[0]
        return {
            "units": unit_count,
            "approved": approval_count,
            "pending": unit_count - approval_count,
        }
