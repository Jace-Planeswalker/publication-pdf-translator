"""Adapt BabelDOC's v2 document contract to the project state authority."""

from __future__ import annotations

from pathlib import Path

from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    ApprovedTranslation as BabelDOCApprovedTranslation,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    BoxFingerprint as BabelDOCBoxFingerprint,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    DocumentTranslationBlockedError,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    DocumentTranslationContext,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    ParagraphRecord as BabelDOCParagraphRecord,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PlaceholderContract as BabelDOCPlaceholderContract,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedILArtifact,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedSnapshot as BabelDOCPreparedSnapshot,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedTranslationDocument,
)
from babeldoc.format.pdf.document_il.midend.document_translation_provider import (
    PreparedTranslationUnit,
)

from pubtrans.m0v2.artifacts import ArtifactRef
from pubtrans.m0v2.artifacts import PreparedArtifactStore
from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m0v2.errors import DocumentBlockedError
from pubtrans.m0v2.errors import SnapshotConflictError
from pubtrans.m0v2.model import BoxFingerprint
from pubtrans.m0v2.model import Disposition
from pubtrans.m0v2.model import ParagraphReason
from pubtrans.m0v2.model import ParagraphRecord
from pubtrans.m0v2.model import PlaceholderContract
from pubtrans.m0v2.model import PlaceholderKind
from pubtrans.m0v2.model import PlaceholderSpec
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m0v2.model import PreparedSnapshot
from pubtrans.m0v2.model import PreparedUnit
from pubtrans.m0v2.model import ProjectBinding
from pubtrans.m0v2.model import UnitLocator
from pubtrans.m0v2.store import ProjectStore


class SQLiteDocumentTranslationProvider:
    """Persist prepared artifacts/manifests and resolve active approvals.

    The provider never translates text. A first BabelDOC invocation durably
    captures the exact prepared artifact and complete paragraph manifest, then
    fails closed until the application has recorded one active approval for
    every translatable unit. Later invocations restore the captured artifact.
    """

    def __init__(
        self,
        database_path: str | Path,
        artifact_directory: str | Path | None = None,
    ):
        self.database_path = Path(database_path)
        if artifact_directory is None:
            artifact_directory = self.database_path.with_suffix(
                self.database_path.suffix + ".artifacts"
            )
        self.artifact_store = PreparedArtifactStore(artifact_directory)

    def _store(self) -> ProjectStore:
        return ProjectStore(self.database_path, self.artifact_store)

    @staticmethod
    def _project(context: DocumentTranslationContext) -> ProjectBinding:
        project = ProjectBinding.create(
            original_pdf_sha256=context.original_pdf_sha256,
            source_language=context.source_language,
            target_language=context.target_language,
            profile_name=context.profile_name,
        )
        if project.project_key != context.project_key:
            raise SnapshotConflictError(
                "BabelDOC and project runtime derived different project keys"
            )
        return project

    @staticmethod
    def _context_payload(
        context: DocumentTranslationContext,
    ) -> dict[str, object]:
        return {
            "context_key": context.context_key,
            "project_key": context.project_key,
            "original_pdf_sha256": context.original_pdf_sha256,
            "prepared_pdf_sha256": context.prepared_pdf_sha256,
            "source_language": context.source_language,
            "target_language": context.target_language,
            "profile_name": context.profile_name,
            "engine_name": context.engine_name,
            "engine_version": context.engine_version,
            "engine_commit": context.engine_commit,
            "extraction_profile": context.extraction_profile,
            "extraction_profile_sha256": context.extraction_profile_sha256,
            "part_key": context.part_key,
        }

    def load_prepared_artifact(
        self,
        context: DocumentTranslationContext,
    ) -> PreparedILArtifact | None:
        expected_context = self._context_payload(context)
        with self._store() as store:
            loaded = store.load_prepared_artifact(context.context_key)
        if loaded is None:
            return None
        stored_context, reference = loaded
        if canonical_json(stored_context) != canonical_json(expected_context):
            raise SnapshotConflictError(
                "stored prepared context differs from BabelDOC context"
            )
        try:
            xml = self.artifact_store.get(reference).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SnapshotConflictError(
                "prepared artifact is not valid UTF-8 XML"
            ) from exc
        artifact = PreparedILArtifact(
            context_key=context.context_key,
            sha256=reference.sha256,
            xml=xml,
        )
        if artifact.sha256 != reference.sha256:
            raise SnapshotConflictError("prepared artifact digest mismatch")
        return artifact

    def save_prepared_artifact(
        self,
        context: DocumentTranslationContext,
        artifact: PreparedILArtifact,
    ) -> None:
        if artifact.context_key != context.context_key:
            raise SnapshotConflictError(
                "BabelDOC attempted to save an artifact for another context"
            )
        reference = self.artifact_store.put(artifact.xml.encode("utf-8"))
        if reference.sha256 != artifact.sha256:
            raise SnapshotConflictError(
                "BabelDOC artifact and content-addressed digest differ"
            )
        project = self._project(context)
        with self._store() as store:
            store.register_prepared_artifact(
                project=project,
                context_key=context.context_key,
                part_key=context.part_key,
                context_payload=self._context_payload(context),
                artifact=reference,
            )

    def translate_document(
        self,
        document: PreparedTranslationDocument,
    ) -> tuple[BabelDOCApprovedTranslation, ...]:
        context = document.snapshot.context
        with self._store() as store:
            loaded = store.load_prepared_artifact(context.context_key)
            if loaded is None:
                raise SnapshotConflictError(
                    "prepared document has no durably registered artifact"
                )
            stored_context, artifact = loaded
            if canonical_json(stored_context) != canonical_json(
                self._context_payload(context)
            ):
                raise SnapshotConflictError(
                    "prepared document context changed after artifact capture"
                )
            core_document = self._to_core_document(document, artifact)
            store.register_document(core_document, artifact)
            try:
                approvals = store.resolve(core_document)
            except DocumentBlockedError as exc:
                raise DocumentTranslationBlockedError(str(exc)) from exc

        return tuple(
            BabelDOCApprovedTranslation(
                approval_id=approval.approval_id,
                unit_key=approval.unit_key,
                unit_revision=approval.unit_revision,
                target_text=approval.target_text,
                target_sha256=approval.target_sha256,
            )
            for approval in approvals
        )

    def _to_core_document(
        self,
        document: PreparedTranslationDocument,
        artifact: ArtifactRef,
    ) -> PreparedDocument:
        context = document.snapshot.context
        project = self._project(context)
        snapshot = self._snapshot(project, document.snapshot, artifact)
        records = tuple(
            self._record(snapshot, record) for record in document.records
        )
        return PreparedDocument.create(
            project=project,
            snapshot=snapshot,
            page_paragraph_counts=document.page_paragraph_counts,
            records=records,
        )

    @staticmethod
    def _snapshot(
        project: ProjectBinding,
        external: BabelDOCPreparedSnapshot,
        artifact: ArtifactRef,
    ) -> PreparedSnapshot:
        context = external.context
        if external.artifact_sha256 != artifact.sha256:
            raise SnapshotConflictError(
                "BabelDOC snapshot and registered artifact digests differ"
            )
        snapshot = PreparedSnapshot.create(
            project=project,
            prepared_pdf_sha256=context.prepared_pdf_sha256,
            engine_name=context.engine_name,
            engine_version=context.engine_version,
            engine_commit=context.engine_commit,
            extraction_profile=context.extraction_profile,
            part_key=context.part_key,
            artifact_sha256=artifact.sha256,
        )
        if snapshot.snapshot_key != external.snapshot_key:
            raise SnapshotConflictError(
                "BabelDOC and project runtime derived different snapshot keys"
            )
        return snapshot

    @classmethod
    def _record(
        cls,
        snapshot: PreparedSnapshot,
        external: BabelDOCParagraphRecord,
    ) -> ParagraphRecord:
        locator = UnitLocator(
            external.locator.page_ordinal,
            external.locator.paragraph_ordinal,
        )
        box = cls._box(external.box)
        unit = (
            cls._unit(snapshot, external.unit)
            if external.unit is not None
            else None
        )
        return ParagraphRecord.create(
            snapshot_key=snapshot.snapshot_key,
            locator=locator,
            disposition=Disposition(external.disposition),
            reason=ParagraphReason(external.reason),
            source_text=external.source_text,
            layout_label=external.layout_label,
            vertical=external.vertical,
            box=box,
            unit=unit,
        )

    @classmethod
    def _unit(
        cls,
        snapshot: PreparedSnapshot,
        external: PreparedTranslationUnit,
    ) -> PreparedUnit:
        box = cls._box(external.box)
        if box is None:  # pragma: no cover - BabelDOC type-level guard
            raise SnapshotConflictError("translatable unit has no geometry")
        unit = PreparedUnit.create(
            snapshot_key=snapshot.snapshot_key,
            locator=UnitLocator(
                external.locator.page_ordinal,
                external.locator.paragraph_ordinal,
            ),
            source_text=external.source_text,
            placeholders=cls._placeholders(external.placeholders),
            layout_label=external.layout_label,
            vertical=external.vertical,
            box=box,
        )
        if (
            unit.unit_key != external.unit_key
            or unit.unit_revision != external.unit_revision
            or unit.source_sha256 != external.source_sha256
        ):
            raise SnapshotConflictError(
                "BabelDOC and project runtime derived different unit identity"
            )
        return unit

    @staticmethod
    def _box(
        external: BabelDOCBoxFingerprint | None,
    ) -> BoxFingerprint | None:
        if external is None:
            return None
        return BoxFingerprint(
            x0=external.x0,
            y0=external.y0,
            x1=external.x1,
            y1=external.y1,
        )

    @staticmethod
    def _placeholders(
        external: BabelDOCPlaceholderContract,
    ) -> PlaceholderContract:
        contract = PlaceholderContract.create(
            external.namespace,
            tuple(
                PlaceholderSpec(
                    kind=PlaceholderKind(spec.kind),
                    open_token=spec.open_token,
                    close_token=spec.close_token,
                )
                for spec in external.specs
            ),
        )
        if contract.signature != external.signature:
            raise SnapshotConflictError(
                "BabelDOC and project placeholder signatures differ"
            )
        return contract
