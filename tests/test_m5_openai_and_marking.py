from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from m1_helpers import make_terminology
from m0v2_helpers import make_document
from pubtrans.m1.context import build_context_packages
from pubtrans.m1.errors import ServiceContractError
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.services import TermGuidance
from pubtrans.m2.errors import TransientServiceError
from pubtrans.m5.config import ProductConfig
from pubtrans.m5.errors import ProductConfigError
from pubtrans.m5.marking import decode_marked_target
from pubtrans.m5.marking import marker_close
from pubtrans.m5.marking import marker_open
from pubtrans.m5.openai import OpenAIResponsesClient
from pubtrans.m5.openai import WebResearchResult

from m1_helpers import make_plan


@dataclass
class RecordingTransport:
    response: dict[str, object]

    def __post_init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def config() -> ProductConfig:
    return ProductConfig.create(
        default_model="quality-model",
        role_models={ActorRole.FINAL_VERIFIER.value: "verification-model"},
    )


def response_with_text(text: str, *, annotations=None) -> dict[str, object]:
    return {
        "status": "completed",
        "output": [
            {"type": "reasoning", "content": []},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": annotations or [],
                    }
                ],
            },
        ],
    }


def test_product_config_is_secret_free_and_role_specific(monkeypatch) -> None:
    product = config()
    assert product.model_for(ActorRole.TRANSLATOR) == "quality-model"
    assert product.model_for(ActorRole.FINAL_VERIFIER) == "verification-model"
    payload = product.as_payload()
    assert "secret-value" not in json.dumps(payload)
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    assert product.api_key() == "secret-value"
    actor = product.actor(
        ActorRole.TRANSLATOR,
        prompt_revision="translation-v1",
        variant="literal-sense",
    )
    assert "secret-value" not in actor.settings_json


def test_product_config_rejects_cleartext_provider_transport() -> None:
    with pytest.raises(ProductConfigError, match="HTTPS"):
        ProductConfig.create(
            default_model="quality-model",
            base_url="http://provider.example.test/v1",
        )


def test_responses_client_uses_stateless_strict_schema() -> None:
    transport = RecordingTransport(response_with_text('{"answer":"ok"}'))
    product = config()
    client = OpenAIResponsesClient(
        api_key="test-key",
        transport=transport,
    )
    actor = product.actor(
        ActorRole.TRANSLATOR,
        prompt_revision="translation-v1",
    )
    result = client.structured(
        actor=actor,
        instructions="Return the requested object.",
        input_payload={"source": "hello"},
        schema_name="fixture",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )
    assert result == {"answer": "ok"}
    request = transport.calls[0]
    assert request["url"] == "https://api.openai.com/v1/responses"
    body = request["payload"]
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert body["text"]["format"]["type"] == "json_schema"


def test_web_research_keeps_only_provider_citations() -> None:
    text = "The term is attested."
    transport = RecordingTransport(
        response_with_text(
            text,
            annotations=[
                {
                    "type": "url_citation",
                    "url": "https://authority.test/term",
                    "title": "Authority",
                    "start_index": 0,
                    "end_index": 8,
                }
            ],
        )
    )
    product = config()
    client = OpenAIResponsesClient(api_key="test-key", transport=transport)
    result = client.research(
        actor=product.actor(
            ActorRole.TERMINOLOGY_RESEARCHER,
            prompt_revision="term-research-v1",
        ),
        instructions="Research terminology.",
        input_payload={"term": "entropy"},
    )
    assert isinstance(result, WebResearchResult)
    assert result.citations[0].url == "https://authority.test/term"
    assert result.citations[0].cited_text == text[:8]
    assert transport.calls[0]["payload"]["tools"] == [
        {"type": "web_search", "search_context_size": "high"}
    ]


def test_in_progress_response_is_retryable_instead_of_parsed_as_complete() -> None:
    transport = RecordingTransport({"status": "in_progress", "output": []})
    product = config()
    client = OpenAIResponsesClient(api_key="test-key", transport=transport)
    with pytest.raises(TransientServiceError, match="still in progress"):
        client.structured(
            actor=product.actor(
                ActorRole.TRANSLATOR,
                prompt_revision="translation-v1",
            ),
            instructions="Translate.",
            input_payload={"source": "hello"},
            schema_name="fixture",
            schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        )


def guidance_for_first_unit() -> tuple[TermGuidance, ...]:
    document = make_document(repeated=False)
    terminology = make_terminology(document)
    plan = make_plan(document, terminology)
    context = build_context_packages(
        document=document,
        terminology=terminology,
        plan=plan,
    )[0]
    stage_terms = []
    by_revision = {item.revision_id: item for item in terminology.terms}
    for directive in terminology.directives_for_unit(document.units[0].unit_key):
        stage_terms.append(
            TermGuidance(
                directive=directive,
                term=by_revision[directive.term_revision_id],
            )
        )
    assert context.relevant_directive_ids
    return tuple(stage_terms)


def test_markers_compute_exact_term_application_offsets() -> None:
    guidance = guidance_for_first_unit()
    key = guidance[0].directive.occurrence_key
    required = guidance[0].directive.required_rendering
    marked = f"你好，{marker_open(key)}{required}{marker_close(key)}。"
    decoded = decode_marked_target(marked, guidance)
    assert decoded.target_text == f"你好，{required}。"
    application = decoded.term_applications[0]
    assert decoded.target_text[application.target_start : application.target_end] == required


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda opening, required, closing: f"{opening}罕见误译{closing}", "differs"),
        (lambda opening, required, closing: required, "omitted"),
        (lambda opening, required, closing: f"{opening}{required}", "not closed"),
    ],
)
def test_markers_fail_closed_on_term_contract_damage(mutator, message: str) -> None:
    guidance = guidance_for_first_unit()
    key = guidance[0].directive.occurrence_key
    required = guidance[0].directive.required_rendering
    marked = mutator(marker_open(key), required, marker_close(key))
    with pytest.raises(ServiceContractError, match=message):
        decode_marked_target(marked, guidance)
