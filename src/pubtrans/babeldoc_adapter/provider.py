"""Adapt BabelDOC's document-level contract to the project state store."""

from __future__ import annotations

from pathlib import Path

from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    ApprovedTranslation as BabelDOCApprovedTranslation,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    DocumentTranslationContext,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedTranslationUnit,
)

from pubtrans.models import PreparedUnit
from pubtrans.provider import ApprovedMapProvider
from pubtrans.state import ProjectState


class SQLiteDocumentTranslationProvider:
    """Capture BabelDOC units and return only persisted approved translations.

    The first invocation records the complete extracted unit set. If review has
    not populated an exact approved map yet, resolution fails closed. A later
    invocation against the same project database resumes from those stable IDs.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)

    def translate_document(
        self,
        units: tuple[PreparedTranslationUnit, ...],
        context: DocumentTranslationContext,
    ) -> list[BabelDOCApprovedTranslation]:
        core_units = [self._to_core(unit, context) for unit in units]
        with ProjectState(self.database_path) as state:
            state.register_units(core_units)

        approvals = ApprovedMapProvider(self.database_path).resolve(core_units)
        return [
            BabelDOCApprovedTranslation(
                unit_id=approval.unit_id,
                source_sha256=approval.source_sha256,
                placeholder_signature=approval.placeholder_signature,
                target_text=approval.target_text,
            )
            for approval in approvals
        ]

    @staticmethod
    def _to_core(
        unit: PreparedTranslationUnit,
        context: DocumentTranslationContext,
    ) -> PreparedUnit:
        return PreparedUnit(
            unit_id=unit.unit_id,
            document_sha256=context.document_sha256,
            page_number=unit.page_number,
            paragraph_debug_id=unit.paragraph_debug_id,
            reading_order=unit.reading_order,
            source_text=unit.source_text,
            source_sha256=unit.source_sha256,
            placeholder_tokens=unit.required_placeholders,
            placeholder_pairs=unit.paired_placeholders,
            placeholder_signature=unit.placeholder_signature,
            layout_label=unit.layout_label,
        )
