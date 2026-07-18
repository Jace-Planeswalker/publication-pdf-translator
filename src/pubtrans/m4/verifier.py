"""Re-open, re-render, and audit the actual translated PDF artifact."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path

import pymupdf

from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m0v2.model import PreparedDocument
from pubtrans.m1.workflow import Release

from .errors import ArtifactVerificationError
from .model import ArtifactCategory
from .model import ArtifactFinding
from .model import ArtifactReport
from .model import ArtifactSeverity
from .model import ArtifactVerificationProfile


_PLACEHOLDER_RE = re.compile(r"\[\[PT2-[0-9a-f]{12}:[^\]]+\]\]")
_URL_RE = re.compile(r"https?://[^\s<>()]+")
_NUMBER_RE = re.compile(r"(?<![\w])\d+(?:[.,]\d+)*%?(?![\w])")
_EQUATION_RE = re.compile(
    r"\b[A-Za-z]\s*=\s*[A-Za-z0-9][A-Za-z0-9^+\-*/().]*"
)


class PDFArtifactVerifier:
    def __init__(
        self,
        profile: ArtifactVerificationProfile | None = None,
    ) -> None:
        self.profile = profile or ArtifactVerificationProfile.create()

    def verify(
        self,
        *,
        document: PreparedDocument,
        release: Release,
        source_pdf: str | Path,
        target_pdf: str | Path,
    ) -> ArtifactReport:
        source_path = Path(source_pdf)
        target_path = Path(target_pdf)
        if not source_path.is_file():
            raise ArtifactVerificationError("source PDF does not exist")
        if not target_path.is_file():
            raise ArtifactVerificationError("target PDF does not exist")
        source_sha = sha256_file(source_path)
        target_sha = sha256_file(target_path)
        if source_sha != document.project.original_pdf_sha256:
            raise ArtifactVerificationError(
                "source PDF digest differs from the prepared project"
            )
        if (
            release.project_key != document.project.project_key
            or release.snapshot_key != document.snapshot.snapshot_key
            or release.manifest_sha256 != document.manifest_sha256
        ):
            raise ArtifactVerificationError(
                "release does not belong to the prepared source document"
            )

        try:
            source = pymupdf.open(source_path)
        except Exception as exc:
            raise ArtifactVerificationError("source PDF cannot be opened") from exc
        try:
            if source.page_count != len(document.page_paragraph_counts):
                raise ArtifactVerificationError(
                    "prepared manifest page count differs from source PDF"
                )
            try:
                target = pymupdf.open(target_path)
            except Exception as exc:
                finding = ArtifactFinding.create(
                    category=ArtifactCategory.FILE_INTEGRITY,
                    severity=ArtifactSeverity.BLOCKING,
                    message="Target PDF cannot be parsed.",
                    evidence=type(exc).__name__,
                )
                return ArtifactReport.create(
                    release_id=release.release_id,
                    project_key=document.project.project_key,
                    source_pdf_sha256=source_sha,
                    target_pdf_sha256=target_sha,
                    profile=self.profile,
                    source_page_count=source.page_count,
                    target_page_count=0,
                    findings=(finding,),
                    metrics={"parse_error": type(exc).__name__},
                )
            try:
                return self._verify_open(
                    document=document,
                    release=release,
                    source=source,
                    target=target,
                    source_sha=source_sha,
                    target_sha=target_sha,
                )
            finally:
                target.close()
        finally:
            source.close()

    def _verify_open(
        self,
        *,
        document: PreparedDocument,
        release: Release,
        source: pymupdf.Document,
        target: pymupdf.Document,
        source_sha: str,
        target_sha: str,
    ) -> ArtifactReport:
        findings: list[ArtifactFinding] = []
        metrics: dict[str, object] = {
            "pages": [],
            "unit_literals_total": 0,
            "unit_literals_found": 0,
            "source_anchors_total": 0,
            "source_anchors_found": 0,
        }
        if self.profile.require_exact_page_count and source.page_count != target.page_count:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.PAGE_COUNT,
                    severity=ArtifactSeverity.BLOCKING,
                    message="Target page count differs from the source PDF.",
                    evidence=f"source={source.page_count}, target={target.page_count}",
                )
            )

        page_total = min(source.page_count, target.page_count)
        source_texts: list[str] = []
        target_texts: list[str] = []
        for page_ordinal in range(page_total):
            source_page = source[page_ordinal]
            target_page = target[page_ordinal]
            source_text = source_page.get_text("text", sort=True)
            target_text = target_page.get_text("text", sort=True)
            source_texts.append(source_text)
            target_texts.append(target_text)
            page_metrics = self._verify_page(
                page_ordinal=page_ordinal,
                source_page=source_page,
                target_page=target_page,
                source_text=source_text,
                target_text=target_text,
                findings=findings,
            )
            page_list = metrics["pages"]
            assert isinstance(page_list, list)
            page_list.append(page_metrics)

        while len(source_texts) < source.page_count:
            source_texts.append(source[len(source_texts)].get_text("text", sort=True))
        while len(target_texts) < target.page_count:
            target_texts.append(target[len(target_texts)].get_text("text", sort=True))

        self._verify_unit_literals(
            document=document,
            release=release,
            target_texts=target_texts,
            findings=findings,
            metrics=metrics,
        )
        self._verify_anchors(
            source_texts=source_texts,
            target_texts=target_texts,
            findings=findings,
            metrics=metrics,
        )
        return ArtifactReport.create(
            release_id=release.release_id,
            project_key=document.project.project_key,
            source_pdf_sha256=source_sha,
            target_pdf_sha256=target_sha,
            profile=self.profile,
            source_page_count=source.page_count,
            target_page_count=target.page_count,
            findings=findings,
            metrics=metrics,
        )

    def _verify_page(
        self,
        *,
        page_ordinal: int,
        source_page: pymupdf.Page,
        target_page: pymupdf.Page,
        source_text: str,
        target_text: str,
        findings: list[ArtifactFinding],
    ) -> dict[str, object]:
        source_rect = source_page.rect
        target_rect = target_page.rect
        width_delta = abs(source_rect.width - target_rect.width)
        height_delta = abs(source_rect.height - target_rect.height)
        if max(width_delta, height_delta) > self.profile.page_size_tolerance_points:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.PAGE_GEOMETRY,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Target page dimensions differ from the source.",
                    evidence=(
                        f"source={source_rect.width:.2f}x{source_rect.height:.2f}; "
                        f"target={target_rect.width:.2f}x{target_rect.height:.2f}"
                    ),
                )
            )

        source_images = _image_hashes(source_page)
        target_images = _image_hashes(target_page)
        if self.profile.require_image_hashes:
            missing_images = source_images - target_images
            if missing_images:
                findings.append(
                    ArtifactFinding.create(
                        category=ArtifactCategory.IMAGE_COVERAGE,
                        severity=ArtifactSeverity.BLOCKING,
                        page_ordinal=page_ordinal,
                        message="One or more source images are missing or changed.",
                        evidence=f"missing image instances={sum(missing_images.values())}",
                    )
                )

        if _visible(source_text, source_images) and not _visible(
            target_text,
            target_images,
        ):
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.BLANK_PAGE,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Source page has visible content but target page is blank.",
                )
            )

        if "\N{REPLACEMENT CHARACTER}" in target_text:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.CORRUPT_GLYPH,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Target text extraction contains replacement glyphs.",
                    evidence="U+FFFD",
                )
            )
        fonts = target_page.get_fonts(full=True)
        if target_text.strip() and not fonts:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.FONT,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Visible target text has no PDF font resources.",
                )
            )
        if any("notdef" in str(item).lower() for font in fonts for item in font):
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.FONT,
                    severity=ArtifactSeverity.ERROR,
                    page_ordinal=page_ordinal,
                    message="Target PDF references a .notdef font resource.",
                )
            )

        line_boxes = _line_boxes(target_page)
        tolerance = self.profile.page_size_tolerance_points
        allowed = pymupdf.Rect(
            target_rect.x0 - tolerance,
            target_rect.y0 - tolerance,
            target_rect.x1 + tolerance,
            target_rect.y1 + tolerance,
        )
        outside = [box for _block, box in line_boxes if not allowed.contains(box)]
        if outside:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.OUT_OF_BOUNDS,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Target text extends outside the page boundary.",
                    evidence=f"line boxes outside={len(outside)}",
                )
            )
        overlaps = _material_overlaps(
            line_boxes,
            threshold=self.profile.maximum_overlap_fraction,
        )
        if overlaps:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.OVERLAP,
                    severity=ArtifactSeverity.ERROR,
                    page_ordinal=page_ordinal,
                    message="Distinct target text blocks materially overlap.",
                    evidence=f"overlapping line pairs={overlaps}",
                )
            )

        try:
            source_ink = _ink_ratio(source_page, self.profile.raster_scale)
            target_ink = _ink_ratio(target_page, self.profile.raster_scale)
        except Exception as exc:
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.FILE_INTEGRITY,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Target page cannot be rasterized reliably.",
                    evidence=type(exc).__name__,
                )
            )
            source_ink = 0.0
            target_ink = 0.0
        if (
            source_ink >= self.profile.minimum_ink_ratio
            and target_ink < self.profile.minimum_ink_ratio
        ):
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.CONTENT_DENSITY,
                    severity=ArtifactSeverity.BLOCKING,
                    page_ordinal=page_ordinal,
                    message="Rendered target page has effectively no visible ink.",
                )
            )
        if (
            source_ink > 0
            and target_ink / source_ink < self.profile.minimum_relative_ink_ratio
        ):
            findings.append(
                ArtifactFinding.create(
                    category=ArtifactCategory.CONTENT_DENSITY,
                    severity=ArtifactSeverity.ERROR,
                    page_ordinal=page_ordinal,
                    message="Target page lost an implausibly large amount of visible content.",
                    evidence=(
                        f"source ink={source_ink:.6f}, target ink={target_ink:.6f}"
                    ),
                )
            )
        return {
            "page_ordinal": page_ordinal,
            "source_width": round(source_rect.width, 3),
            "source_height": round(source_rect.height, 3),
            "target_width": round(target_rect.width, 3),
            "target_height": round(target_rect.height, 3),
            "source_ink_ratio": round(source_ink, 8),
            "target_ink_ratio": round(target_ink, 8),
            "source_images": sum(source_images.values()),
            "target_images": sum(target_images.values()),
            "target_fonts": len(fonts),
            "target_lines": len(line_boxes),
        }

    def _verify_unit_literals(
        self,
        *,
        document: PreparedDocument,
        release: Release,
        target_texts: list[str],
        findings: list[ArtifactFinding],
        metrics: dict[str, object],
    ) -> None:
        if not self.profile.require_all_unit_literals:
            return
        unit_by_key = {item.unit_key: item for item in document.units}
        total = 0
        found = 0
        for outcome in release.outcomes:
            unit = unit_by_key.get(outcome.unit_key)
            if unit is None:
                raise ArtifactVerificationError("release contains an unknown unit")
            page_ordinal = unit.locator.page_ordinal
            page_text = (
                _search_text(target_texts[page_ordinal])
                if page_ordinal < len(target_texts)
                else ""
            )
            missing: list[str] = []
            for fragment in _literal_fragments(outcome.rendered_target.target_text):
                total += 1
                if _search_text(fragment) in page_text:
                    found += 1
                else:
                    missing.append(fragment)
            if missing:
                findings.append(
                    ArtifactFinding.create(
                        category=ArtifactCategory.TEXT_COVERAGE,
                        severity=ArtifactSeverity.BLOCKING,
                        page_ordinal=page_ordinal,
                        message="Approved target text is absent from the rendered page.",
                        evidence=" | ".join(item[:120] for item in missing[:3]),
                    )
                )
        metrics["unit_literals_total"] = total
        metrics["unit_literals_found"] = found

    def _verify_anchors(
        self,
        *,
        source_texts: list[str],
        target_texts: list[str],
        findings: list[ArtifactFinding],
        metrics: dict[str, object],
    ) -> None:
        if not self.profile.require_source_anchors:
            return
        total = 0
        found = 0
        for page_ordinal, source_text in enumerate(source_texts):
            target_text = (
                _search_text(target_texts[page_ordinal])
                if page_ordinal < len(target_texts)
                else ""
            )
            missing: list[str] = []
            for anchor in _anchors(source_text):
                total += 1
                if _search_text(anchor) in target_text:
                    found += 1
                else:
                    missing.append(anchor)
            if missing:
                findings.append(
                    ArtifactFinding.create(
                        category=ArtifactCategory.PROTECTED_ANCHOR,
                        severity=ArtifactSeverity.BLOCKING,
                        page_ordinal=page_ordinal,
                        message="Source number, URL, or equation is missing from target PDF.",
                        evidence=" | ".join(missing[:5]),
                    )
                )
        metrics["source_anchors_total"] = total
        metrics["source_anchors_found"] = found


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _search_text(value: str) -> str:
    value = normalize_text(value).replace("\N{NO-BREAK SPACE}", " ")
    return "".join(value.split())


def _literal_fragments(value: str) -> tuple[str, ...]:
    fragments = []
    for item in _PLACEHOLDER_RE.split(value):
        item = item.strip()
        searchable = _search_text(item)
        if len(searchable) >= 2 and any(character.isalnum() for character in searchable):
            fragments.append(item)
    return tuple(fragments)


def _anchors(value: str) -> tuple[str, ...]:
    items = {
        match.group(0).rstrip(".,;:")
        for pattern in (_URL_RE, _NUMBER_RE, _EQUATION_RE)
        for match in pattern.finditer(value)
    }
    return tuple(sorted(item for item in items if item))


def _image_hashes(page: pymupdf.Page) -> Counter[str]:
    result: Counter[str] = Counter()
    for item in page.get_image_info(hashes=True):
        digest_value = item.get("digest")
        if isinstance(digest_value, bytes):
            result[digest_value.hex()] += 1
        elif digest_value is not None:
            result[str(digest_value)] += 1
    return result


def _visible(text: str, images: Counter[str]) -> bool:
    return bool(text.strip() or sum(images.values()))


def _line_boxes(page: pymupdf.Page) -> list[tuple[int, pymupdf.Rect]]:
    result: list[tuple[int, pymupdf.Rect]] = []
    payload = page.get_text("dict", sort=True)
    for block_index, block in enumerate(payload.get("blocks", [])):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            box = line.get("bbox")
            if isinstance(box, (list, tuple)) and len(box) == 4:
                rect = pymupdf.Rect(box)
                if rect.width > 0 and rect.height > 0:
                    result.append((block_index, rect))
    return result


def _material_overlaps(
    lines: list[tuple[int, pymupdf.Rect]],
    *,
    threshold: float,
) -> int:
    overlaps = 0
    for index, (first_block, first) in enumerate(lines):
        for second_block, second in lines[index + 1 :]:
            if first_block == second_block:
                continue
            intersection = first & second
            if intersection.is_empty:
                continue
            smaller = min(first.get_area(), second.get_area())
            if smaller > 0 and intersection.get_area() / smaller > threshold:
                overlaps += 1
    return overlaps


def _ink_ratio(page: pymupdf.Page, scale: float) -> float:
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(scale, scale),
        colorspace=pymupdf.csGRAY,
        alpha=False,
    )
    samples = memoryview(pixmap.samples)
    if not samples:
        return 0.0
    ink = sum(1 for value in samples if value < 245)
    return ink / len(samples)
