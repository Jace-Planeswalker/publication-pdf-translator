from __future__ import annotations

from pubtrans.m0v2.canonical import sha256_bytes
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


ARTIFACT_BYTES = b"<prepared-document version='1'/>\n"
PDF_SHA = "a" * 64
PREPARED_PDF_SHA = "b" * 64
ENGINE_COMMIT = "17480db9df92ddcb37349ce34b312335226e8ec9"


def make_project(*, pdf_sha: str = PDF_SHA) -> ProjectBinding:
    return ProjectBinding.create(
        original_pdf_sha256=pdf_sha,
        source_language="en",
        target_language="zh-Hans",
        profile_name="publication",
    )


def make_snapshot(
    project: ProjectBinding,
    *,
    profile: dict[str, object] | None = None,
    artifact: bytes = ARTIFACT_BYTES,
    part_key: str = "whole-document",
) -> PreparedSnapshot:
    return PreparedSnapshot.create(
        project=project,
        prepared_pdf_sha256=PREPARED_PDF_SHA,
        engine_name="BabelDOC",
        engine_version="0.6.4",
        engine_commit=ENGINE_COMMIT,
        extraction_profile=profile or {"pages": "all", "rich_text": True},
        part_key=part_key,
        artifact_sha256=sha256_bytes(artifact),
    )


def make_contract(snapshot: PreparedSnapshot, *, two_styles: bool = False):
    namespace = f"PT2-{snapshot.snapshot_key[:12]}"
    style_one = PlaceholderSpec(
        kind=PlaceholderKind.RICH_STYLE,
        open_token=f"[[{namespace}:S:0001:OPEN]]",
        close_token=f"[[{namespace}:S:0001:CLOSE]]",
    )
    formula = PlaceholderSpec(
        kind=PlaceholderKind.FORMULA,
        open_token=f"[[{namespace}:F:0002]]",
    )
    specs = [style_one, formula]
    if two_styles:
        specs.append(
            PlaceholderSpec(
                kind=PlaceholderKind.RICH_STYLE,
                open_token=f"[[{namespace}:S:0003:OPEN]]",
                close_token=f"[[{namespace}:S:0003:CLOSE]]",
            )
        )
    return PlaceholderContract.create(namespace, specs)


def source_for(contract: PlaceholderContract, *, text: str = "world") -> str:
    style = contract.specs[0]
    formula = contract.specs[1]
    return (
        f"Hello {style.open_token}{text}{style.close_token} "
        f"equals {formula.open_token}."
    )


def target_for(contract: PlaceholderContract, *, text: str = "世界") -> str:
    style = contract.specs[0]
    formula = contract.specs[1]
    return (
        f"你好，{style.open_token}{text}{style.close_token}"
        f"等于{formula.open_token}。"
    )


def make_document(
    *,
    repeated: bool = True,
    blocker: bool = False,
    project: ProjectBinding | None = None,
    snapshot: PreparedSnapshot | None = None,
) -> PreparedDocument:
    project = project or make_project()
    snapshot = snapshot or make_snapshot(project)
    contract = make_contract(snapshot)
    box = BoxFingerprint.create(10, 20, 200, 40)

    source = source_for(contract)
    unit_a = PreparedUnit.create(
        snapshot_key=snapshot.snapshot_key,
        locator=UnitLocator(0, 0),
        source_text=source,
        placeholders=contract,
        layout_label="text",
        vertical=False,
        box=box,
    )
    records = [
        ParagraphRecord.create(
            snapshot_key=snapshot.snapshot_key,
            locator=unit_a.locator,
            disposition=Disposition.TRANSLATABLE,
            reason=ParagraphReason.TEXT,
            source_text=source,
            layout_label="text",
            vertical=False,
            box=box,
            unit=unit_a,
        )
    ]

    if repeated:
        unit_b = PreparedUnit.create(
            snapshot_key=snapshot.snapshot_key,
            locator=UnitLocator(0, 1),
            source_text=source,
            placeholders=contract,
            layout_label="text",
            vertical=False,
            box=BoxFingerprint.create(10, 50, 200, 70),
        )
        records.append(
            ParagraphRecord.create(
                snapshot_key=snapshot.snapshot_key,
                locator=unit_b.locator,
                disposition=Disposition.TRANSLATABLE,
                reason=ParagraphReason.TEXT,
                source_text=source,
                layout_label="text",
                vertical=False,
                box=unit_b.box,
                unit=unit_b,
            )
        )

    records.append(
        ParagraphRecord.create(
            snapshot_key=snapshot.snapshot_key,
            locator=UnitLocator(0, len(records)),
            disposition=Disposition.SAFE_EXCLUSION,
            reason=ParagraphReason.PURE_NUMERIC,
            source_text="2026",
            layout_label="text",
            vertical=False,
            box=BoxFingerprint.create(10, 80, 80, 95),
        )
    )

    if blocker:
        records.append(
            ParagraphRecord.create(
                snapshot_key=snapshot.snapshot_key,
                locator=UnitLocator(0, len(records)),
                disposition=Disposition.BLOCKER,
                reason=ParagraphReason.VERTICAL_TEXT_UNSUPPORTED,
                source_text="Meaningful vertical text",
                layout_label="text",
                vertical=True,
                box=BoxFingerprint.create(220, 20, 240, 200),
            )
        )
    return PreparedDocument.create(
        project=project,
        snapshot=snapshot,
        page_paragraph_counts=(len(records),),
        records=records,
    )
