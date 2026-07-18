"""Auditable terminology evidence import and safe cited-page harvesting."""

from __future__ import annotations

import html.parser
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from pubtrans.m0v2.canonical import digest
from pubtrans.m0v2.canonical import normalize_text
from pubtrans.m1.terminology import EvidenceKind
from pubtrans.m1.terminology import EvidenceStance
from pubtrans.m1.terminology import EvidenceTier

from .errors import TerminologyPlanningError
from .openai import WebCitation


@dataclass(frozen=True, slots=True)
class EvidenceMaterial:
    source_term: str
    sense_id: str
    target_form: str
    stance: EvidenceStance
    kind: EvidenceKind
    tier: EvidenceTier
    source_key: str
    source_uri: str
    source_title: str
    excerpt: str
    retrieved_on: str
    sense_match: bool
    domain_match: bool
    notes: str

    def __post_init__(self) -> None:
        for name in (
            "source_term",
            "sense_id",
            "target_form",
            "source_key",
            "source_uri",
            "source_title",
            "excerpt",
        ):
            value = getattr(self, name)
            if not value or value != normalize_text(value).strip():
                raise TerminologyPlanningError(
                    f"terminology evidence {name} is empty or non-canonical"
                )
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_key):
            raise TerminologyPlanningError(
                "terminology evidence source_key must be a lowercase SHA-256"
            )
        parsed = urllib.parse.urlparse(self.source_uri)
        if parsed.scheme not in {"http", "https", "urn"}:
            raise TerminologyPlanningError(
                "terminology evidence source_uri must be HTTP(S) or URN"
            )
        if parsed.scheme in {"http", "https"} and not parsed.hostname:
            raise TerminologyPlanningError(
                "terminology evidence web URI has no hostname"
            )
        try:
            date.fromisoformat(self.retrieved_on)
        except ValueError as exc:
            raise TerminologyPlanningError(
                "terminology evidence retrieved_on must use YYYY-MM-DD"
            ) from exc
        if self.target_form.casefold() not in self.excerpt.casefold():
            raise TerminologyPlanningError(
                "terminology evidence excerpt does not contain its target form"
            )
        if self.notes != normalize_text(self.notes).strip():
            raise TerminologyPlanningError(
                "terminology evidence notes are non-canonical"
            )

    def as_payload(self) -> dict[str, object]:
        return {
            "source_term": self.source_term,
            "sense_id": self.sense_id,
            "target_form": self.target_form,
            "stance": self.stance.value,
            "kind": self.kind.value,
            "tier": self.tier.value,
            "source_key": self.source_key,
            "source_uri": self.source_uri,
            "source_title": self.source_title,
            "excerpt": self.excerpt,
            "retrieved_on": self.retrieved_on,
            "sense_match": self.sense_match,
            "domain_match": self.domain_match,
            "notes": self.notes,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "EvidenceMaterial":
        return cls(
            source_term=str(payload["source_term"]),
            sense_id=str(payload["sense_id"]),
            target_form=str(payload["target_form"]),
            stance=EvidenceStance(str(payload["stance"])),
            kind=EvidenceKind(str(payload["kind"])),
            tier=EvidenceTier(str(payload["tier"])),
            source_key=str(payload["source_key"]),
            source_uri=str(payload["source_uri"]),
            source_title=str(payload["source_title"]),
            excerpt=str(payload["excerpt"]),
            retrieved_on=str(payload["retrieved_on"]),
            sense_match=bool(payload["sense_match"]),
            domain_match=bool(payload["domain_match"]),
            notes=str(payload["notes"]),
        )


class EvidenceCatalog:
    """User- or connector-supplied evidence; never a target-only glossary."""

    def __init__(self, entries: tuple[EvidenceMaterial, ...] = ()) -> None:
        provenance: dict[str, tuple[str, str]] = {}
        unique: dict[tuple[object, ...], EvidenceMaterial] = {}
        for item in entries:
            binding = (item.source_uri, item.retrieved_on)
            previous = provenance.setdefault(item.source_key, binding)
            if previous != binding:
                raise TerminologyPlanningError(
                    "one evidence source_key is bound to multiple sources"
                )
            identity = (
                item.source_term,
                item.sense_id,
                item.target_form,
                item.source_key,
                item.stance,
                item.excerpt,
            )
            unique[identity] = item
        self.entries = tuple(
            sorted(
                unique.values(),
                key=lambda item: (
                    item.source_term,
                    item.sense_id,
                    item.target_form,
                    item.source_key,
                ),
            )
        )

    def for_concept(
        self,
        *,
        source_term: str,
        sense_id: str,
    ) -> tuple[EvidenceMaterial, ...]:
        return tuple(
            item
            for item in self.entries
            if item.source_term == source_term and item.sense_id == sense_id
        )

    @classmethod
    def load(cls, path: str | Path | None) -> "EvidenceCatalog":
        if path is None:
            return cls()
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TerminologyPlanningError("cannot read terminology evidence") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("entries"), list
        ):
            raise TerminologyPlanningError(
                "terminology evidence must contain an entries array"
            )
        entries = payload["entries"]
        assert isinstance(entries, list)
        if any(not isinstance(item, dict) for item in entries):
            raise TerminologyPlanningError("terminology evidence entry is malformed")
        return cls(
            tuple(
                EvidenceMaterial.from_payload(item)
                for item in entries
                if isinstance(item, dict)
            )
        )


class PageFetcher(Protocol):
    def fetch_text(self, url: str) -> str | None: ...


class SafeHTTPPageFetcher:
    """Fetch bounded public HTML/text while rejecting local-network targets."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        maximum_bytes: int = 2_000_000,
        opener=None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.maximum_bytes = maximum_bytes
        self.opener = opener or urllib.request.build_opener(_NoRedirectHandler())

    def fetch_text(self, url: str) -> str | None:
        current = url
        for _redirect in range(4):
            parsed = urllib.parse.urlparse(current)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                return None
            try:
                port = parsed.port
            except ValueError:
                return None
            if port not in {None, 80, 443} or not _public_hostname(parsed.hostname):
                return None
            request = urllib.request.Request(
                current,
                headers={
                    "User-Agent": "publication-pdf-translator/0.2 research"
                },
            )
            try:
                response = self.opener.open(
                    request,
                    timeout=self.timeout_seconds,
                )
            except urllib.error.HTTPError as exc:
                if exc.code not in {301, 302, 303, 307, 308}:
                    return None
                location = exc.headers.get("Location")
                if not location:
                    return None
                current = urllib.parse.urljoin(current, location)
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                return None
            with response:
                content_type = response.headers.get_content_type()
                if content_type not in {
                    "text/html",
                    "text/plain",
                    "application/xhtml+xml",
                }:
                    return None
                raw = response.read(self.maximum_bytes + 1)
                if len(raw) > self.maximum_bytes:
                    return None
                charset = response.headers.get_content_charset() or "utf-8"
            break
        else:
            return None
        try:
            decoded = raw.decode(charset, errors="replace")
        except LookupError:
            decoded = raw.decode("utf-8", errors="replace")
        if content_type == "text/plain":
            return _collapse(decoded)
        parser = _VisibleHTMLParser()
        parser.feed(decoded)
        return _collapse(" ".join(parser.parts))


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, _file_pointer, _code, _message, _headers, _url):
        return None


def harvest_citations(
    *,
    source_term: str,
    sense_id: str,
    target_forms: tuple[str, ...],
    citations: tuple[WebCitation, ...],
    retrieved_on: str,
    fetcher: PageFetcher,
) -> tuple[EvidenceMaterial, ...]:
    result: list[EvidenceMaterial] = []
    for citation in citations:
        page_text = fetcher.fetch_text(citation.url)
        if not page_text:
            continue
        kind, tier = _classify_uri(citation.url)
        source_key = digest("pubtrans.term-source/v1", {"uri": citation.url})
        for target in target_forms:
            excerpt = _excerpt(page_text, target)
            if excerpt is None:
                continue
            result.append(
                EvidenceMaterial(
                    source_term=source_term,
                    sense_id=sense_id,
                    target_form=target,
                    stance=EvidenceStance.SUPPORTS,
                    kind=kind,
                    tier=tier,
                    source_key=source_key,
                    source_uri=citation.url,
                    source_title=citation.title or citation.url,
                    excerpt=excerpt,
                    retrieved_on=retrieved_on,
                    sense_match=False,
                    domain_match=False,
                    notes=(
                        "Fetched from a provider-cited page; independent review must "
                        "confirm sense, domain and stance."
                    ),
                )
            )
    unique = {(
        item.target_form,
        item.source_key,
        item.excerpt,
    ): item for item in result}
    return tuple(
        sorted(unique.values(), key=lambda item: (item.target_form, item.source_key))
    )


class _VisibleHTMLParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._hidden:
            self._hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden and data.strip():
            self.parts.append(data)


def _collapse(value: str) -> str:
    return " ".join(normalize_text(value).split())


def _excerpt(text: str, target: str, radius: int = 180) -> str | None:
    match = re.search(re.escape(target), text, re.IGNORECASE)
    if match is None:
        return None
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return text[start:end].strip()


def _classify_uri(url: str) -> tuple[EvidenceKind, EvidenceTier]:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "termonline.cn" or host.endswith(".termonline.cn"):
        return EvidenceKind.AUTHORITY_TERMBANK, EvidenceTier.A_AUTHORITY
    if host == "cnterm.cn" or host.endswith(".cnterm.cn"):
        return EvidenceKind.AUTHORITY_TERMBANK, EvidenceTier.A_AUTHORITY
    if host == "gov.cn" or host.endswith(".gov.cn"):
        return EvidenceKind.OFFICIAL_NAMING, EvidenceTier.B_DOMAIN
    return EvidenceKind.CORPUS_ATTESTATION, EvidenceTier.C_CORPUS


def _public_hostname(hostname: str) -> bool:
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    for address in addresses:
        raw = address[4][0]
        try:
            value = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if not value.is_global:
            return False
    return bool(addresses)
