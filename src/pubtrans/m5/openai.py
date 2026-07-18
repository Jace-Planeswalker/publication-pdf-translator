"""Minimal stateless OpenAI Responses API adapter with strict output parsing."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m1.plan import ActorSpec
from pubtrans.m2.errors import PermanentServiceError
from pubtrans.m2.errors import RateLimitServiceError
from pubtrans.m2.errors import TransientServiceError

from .errors import ModelResponseError


@dataclass(frozen=True, slots=True)
class WebCitation:
    url: str
    title: str
    cited_text: str


@dataclass(frozen=True, slots=True)
class WebResearchResult:
    text: str
    citations: tuple[WebCitation, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "text": self.text,
            "citations": [
                {
                    "url": item.url,
                    "title": item.title,
                    "cited_text": item.cited_text,
                }
                for item in self.citations
            ],
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "WebResearchResult":
        raw = payload.get("citations")
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise ModelResponseError("web research citations are malformed")
        return cls(
            text=str(payload["text"]),
            citations=tuple(
                WebCitation(
                    url=str(item["url"]),
                    title=str(item["title"]),
                    cited_text=str(item["cited_text"]),
                )
                for item in raw
            ),
        )


class JSONTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]: ...


class UrllibJSONTransport:
    def post(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]:
        request = urllib.request.Request(
            url,
            data=canonical_json(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After")
            try:
                retry_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_seconds = None
            if exc.code == 429:
                raise RateLimitServiceError(
                    "OpenAI rate limit",
                    retry_after_seconds=retry_seconds,
                ) from exc
            if exc.code in {408, 409} or exc.code >= 500:
                raise TransientServiceError(f"OpenAI HTTP {exc.code}") from exc
            raise PermanentServiceError(f"OpenAI HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientServiceError("OpenAI transport failure") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TransientServiceError("OpenAI returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise TransientServiceError("OpenAI returned a non-object response")
        return decoded


class StructuredModelClient(Protocol):
    def structured(
        self,
        *,
        actor: ActorSpec,
        instructions: str,
        input_payload: object,
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]: ...


class ResearchModelClient(Protocol):
    def research(
        self,
        *,
        actor: ActorSpec,
        instructions: str,
        input_payload: object,
    ) -> WebResearchResult: ...


class OpenAIResponsesClient:
    """Use `/v1/responses`, `text.format`, and optional hosted web search."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 300.0,
        transport: JSONTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("OpenAI timeout must be positive")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.transport = transport or UrllibJSONTransport()

    def structured(
        self,
        *,
        actor: ActorSpec,
        instructions: str,
        input_payload: object,
        schema_name: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        body = self._base_body(actor=actor, instructions=instructions)
        body.update(
            {
                "input": canonical_json(input_payload),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            }
        )
        response = self._post(body)
        text, _annotations = _output_text(response)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ModelResponseError("structured response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ModelResponseError("structured response is not a JSON object")
        return payload

    def research(
        self,
        *,
        actor: ActorSpec,
        instructions: str,
        input_payload: object,
    ) -> WebResearchResult:
        body = self._base_body(actor=actor, instructions=instructions)
        body.update(
            {
                "input": canonical_json(input_payload),
                "tools": [
                    {
                        "type": "web_search",
                        "search_context_size": "high",
                    }
                ],
                "include": ["web_search_call.action.sources"],
            }
        )
        response = self._post(body)
        text, annotations = _output_text(response)
        citations: list[WebCitation] = []
        seen: set[str] = set()
        for annotation in annotations:
            citation = annotation.get("url_citation", annotation)
            if not isinstance(citation, dict):
                continue
            url = str(citation.get("url", "")).strip()
            if not url or url in seen:
                continue
            seen.add(url)
            start = _safe_int(citation.get("start_index"), 0)
            end = _safe_int(citation.get("end_index"), start)
            cited_text = text[start:end] if 0 <= start < end <= len(text) else ""
            citations.append(
                WebCitation(
                    url=url,
                    title=str(citation.get("title", url)),
                    cited_text=cited_text,
                )
            )
        return WebResearchResult(text=text, citations=tuple(citations))

    def _base_body(
        self,
        *,
        actor: ActorSpec,
        instructions: str,
    ) -> dict[str, object]:
        settings = json.loads(actor.settings_json)
        if not isinstance(settings, dict):
            raise ModelResponseError("actor settings are not a JSON object")
        body: dict[str, object] = {
            "model": actor.model,
            "instructions": instructions,
            "store": False,
        }
        effort = str(settings.get("reasoning_effort", "high"))
        if effort != "none":
            body["reasoning"] = {"effort": effort}
        return body

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        response = self.transport.post(
            url=f"{self.base_url}/responses",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=self.timeout_seconds,
        )
        error = response.get("error")
        if error:
            raise PermanentServiceError("OpenAI response contains an error")
        status = str(response.get("status", "completed"))
        if status == "in_progress":
            raise TransientServiceError("OpenAI response is still in progress")
        if status != "completed":
            raise ModelResponseError(f"OpenAI response status is {status}")
        return response


def _output_text(
    response: dict[str, object],
) -> tuple[str, tuple[dict[str, object], ...]]:
    output = response.get("output")
    if not isinstance(output, list):
        raise ModelResponseError("OpenAI response has no output list")
    texts: list[str] = []
    annotations: list[dict[str, object]] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "refusal":
                raise PermanentServiceError("model refused the requested operation")
            if part.get("type") != "output_text":
                continue
            texts.append(str(part.get("text", "")))
            raw_annotations = part.get("annotations", [])
            if isinstance(raw_annotations, list):
                annotations.extend(
                    value for value in raw_annotations if isinstance(value, dict)
                )
    text = "".join(texts).strip()
    if not text:
        raise ModelResponseError("OpenAI response contains no output text")
    return text, tuple(annotations)


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
