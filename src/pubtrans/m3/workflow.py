"""Application orchestration for the two-pass BabelDOC provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Generic
from typing import Protocol
from typing import TypeVar

from pubtrans.babeldoc_adapter import SQLiteDocumentTranslationProvider
from pubtrans.m0v2.errors import ApprovalSetError
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m1.kernel import TranslationKernel
from pubtrans.m1.services import ServiceBundle
from pubtrans.m1.workflow import Release
from pubtrans.m2.store import RecoveryStore
from pubtrans.planning import PlannedTranslation

from .errors import PDFLoopContractError


T = TypeVar("T")


class RenderPhase(str, Enum):
    PREPARE = "PREPARE"
    FINAL = "FINAL"


class TranslationPlanner(Protocol):
    def plan(
        self,
        document: PreparedDocument,
        store: RecoveryStore,
    ) -> PlannedTranslation: ...


class KernelServiceFactory(Protocol):
    def create(
        self,
        *,
        store: RecoveryStore,
        document: PreparedDocument,
        planned: PlannedTranslation,
    ) -> ServiceBundle: ...


class BabelDOCRenderPass(Protocol[T]):
    def __call__(
        self,
        provider: SQLiteDocumentTranslationProvider,
        phase: RenderPhase,
    ) -> T: ...


@dataclass(frozen=True, slots=True)
class PDFLoopResult(Generic[T]):
    release: Release
    rendered: T
    prepared_capture_was_needed: bool
    database_path: Path


class BabelDOCPDFLoop:
    """Drive prepare, semantic release, and final render without manual approval."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        planner: TranslationPlanner,
        service_factory: KernelServiceFactory,
        artifact_directory: str | Path | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.planner = planner
        self.service_factory = service_factory
        self.artifact_directory = (
            Path(artifact_directory) if artifact_directory is not None else None
        )

    def run(self, render_pass: BabelDOCRenderPass[T]) -> PDFLoopResult[T]:
        provider = SQLiteDocumentTranslationProvider(
            self.database_path,
            self.artifact_directory,
        )
        capture_needed = False
        prepared_rendered: T | None = None
        try:
            prepared_rendered = render_pass(provider, RenderPhase.PREPARE)
        except ApprovalSetError:
            capture_needed = True

        with RecoveryStore(
            self.database_path,
            provider.artifact_store,
        ) as store:
            document = store.load_document()
            artifact = store.load_artifact_reference()
            provider.artifact_store.verify(artifact)
            previously_rendered_release = store.load_active_release()
            planned = self.planner.plan(document, store)
            planned.validate(document)
            services = self.service_factory.create(
                store=store,
                document=document,
                planned=planned,
            )
            release = TranslationKernel(store, services).run(
                document=document,
                terminology=planned.terminology,
                plan=planned.plan,
                activate=True,
            )

        if prepared_rendered is not None and previously_rendered_release == release:
            rendered = prepared_rendered
        else:
            rendered = render_pass(provider, RenderPhase.FINAL)

        with RecoveryStore(
            self.database_path,
            provider.artifact_store,
        ) as store:
            document = store.load_document()
            if store.resolve(document) != release.approvals:
                raise PDFLoopContractError(
                    "BabelDOC resolved approvals differ from the kernel release"
                )
            if store.load_active_release() != release:
                raise PDFLoopContractError(
                    "active M1 release changed during final PDF rendering"
                )

        return PDFLoopResult(
            release=release,
            rendered=rendered,
            prepared_capture_was_needed=capture_needed,
            database_path=self.database_path,
        )
