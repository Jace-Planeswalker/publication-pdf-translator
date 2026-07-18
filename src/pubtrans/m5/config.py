"""Secret-free product configuration and immutable actor profiles."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pubtrans.m0v2.canonical import canonical_json
from pubtrans.m1.plan import ActorRole
from pubtrans.m1.plan import ActorSpec

from .errors import ProductConfigError


_ROLES = tuple(ActorRole)


@dataclass(frozen=True, slots=True)
class ProductConfig:
    source_language: str
    target_language: str
    profile_name: str
    provider: str
    base_url: str
    api_key_env: str
    default_model: str
    role_models_json: str
    reasoning_effort: str
    request_timeout_seconds: float
    enable_web_research: bool
    max_planning_calls: int
    max_translation_calls: int
    global_review_chunk_characters: int
    max_estimated_tokens: int
    max_estimated_microusd: int

    @classmethod
    def create(
        cls,
        *,
        source_language: str = "en",
        target_language: str = "zh-Hans",
        profile_name: str = "publication",
        provider: str = "openai-responses",
        base_url: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        default_model: str,
        role_models: dict[str, str] | None = None,
        reasoning_effort: str = "high",
        request_timeout_seconds: float = 300.0,
        enable_web_research: bool = True,
        max_planning_calls: int = 500,
        max_translation_calls: int = 100_000,
        global_review_chunk_characters: int = 40_000,
        max_estimated_tokens: int = 1_000_000_000,
        max_estimated_microusd: int = 1_000_000_000,
    ) -> "ProductConfig":
        role_models = role_models or {}
        unknown = set(role_models) - {item.value for item in _ROLES}
        if unknown:
            raise ProductConfigError(
                f"unknown actor role model overrides: {sorted(unknown)}"
            )
        return cls(
            source_language=source_language.strip(),
            target_language=target_language.strip(),
            profile_name=profile_name.strip(),
            provider=provider.strip(),
            base_url=base_url.rstrip("/"),
            api_key_env=api_key_env.strip(),
            default_model=default_model.strip(),
            role_models_json=canonical_json(role_models),
            reasoning_effort=reasoning_effort.strip(),
            request_timeout_seconds=float(request_timeout_seconds),
            enable_web_research=bool(enable_web_research),
            max_planning_calls=int(max_planning_calls),
            max_translation_calls=int(max_translation_calls),
            global_review_chunk_characters=int(global_review_chunk_characters),
            max_estimated_tokens=int(max_estimated_tokens),
            max_estimated_microusd=int(max_estimated_microusd),
        )

    def __post_init__(self) -> None:
        for name in (
            "source_language",
            "target_language",
            "profile_name",
            "provider",
            "base_url",
            "api_key_env",
            "default_model",
        ):
            if not getattr(self, name):
                raise ProductConfigError(f"{name} must not be empty")
        if self.provider != "openai-responses":
            raise ProductConfigError("only openai-responses is implemented")
        if not self.base_url.startswith("https://"):
            raise ProductConfigError("provider base URL must use HTTPS")
        if self.reasoning_effort not in {"none", "low", "medium", "high", "xhigh"}:
            raise ProductConfigError("unsupported reasoning effort")
        if self.request_timeout_seconds <= 0:
            raise ProductConfigError("request timeout must be positive")
        for name in (
            "max_planning_calls",
            "max_translation_calls",
            "max_estimated_tokens",
            "max_estimated_microusd",
        ):
            if getattr(self, name) < 0:
                raise ProductConfigError(f"{name} must be non-negative")
        if self.global_review_chunk_characters < 4_000:
            raise ProductConfigError(
                "global review chunks must allow at least 4000 characters"
            )
        parsed = json.loads(self.role_models_json)
        if not isinstance(parsed, dict) or canonical_json(parsed) != self.role_models_json:
            raise ProductConfigError("role model map is not canonical")
        if set(parsed) - {item.value for item in _ROLES}:
            raise ProductConfigError("role model map contains an unknown role")
        if any(not isinstance(value, str) or not value.strip() for value in parsed.values()):
            raise ProductConfigError("role model override must be a non-empty string")

    @property
    def role_models(self) -> dict[str, str]:
        result = json.loads(self.role_models_json)
        assert isinstance(result, dict)
        return {str(key): str(value) for key, value in result.items()}

    def model_for(self, role: ActorRole) -> str:
        return self.role_models.get(role.value, self.default_model)

    def actor(
        self,
        role: ActorRole,
        *,
        prompt_revision: str,
        variant: str = "primary",
    ) -> ActorSpec:
        return ActorSpec.create(
            role=role,
            provider=self.provider,
            model=self.model_for(role),
            prompt_revision=prompt_revision,
            settings={
                "reasoning_effort": self.reasoning_effort,
                "variant": variant,
                "store": False,
            },
        )

    def api_key(self) -> str:
        value = os.environ.get(self.api_key_env, "").strip()
        if not value:
            raise ProductConfigError(
                f"provider credential environment variable {self.api_key_env} is empty"
            )
        return value

    def as_payload(self) -> dict[str, object]:
        return {
            "source_language": self.source_language,
            "target_language": self.target_language,
            "profile_name": self.profile_name,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "default_model": self.default_model,
            "role_models": self.role_models,
            "reasoning_effort": self.reasoning_effort,
            "request_timeout_seconds": self.request_timeout_seconds,
            "enable_web_research": self.enable_web_research,
            "max_planning_calls": self.max_planning_calls,
            "max_translation_calls": self.max_translation_calls,
            "global_review_chunk_characters": self.global_review_chunk_characters,
            "max_estimated_tokens": self.max_estimated_tokens,
            "max_estimated_microusd": self.max_estimated_microusd,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ProductConfig":
        raw_roles = payload.get("role_models", {})
        if not isinstance(raw_roles, dict):
            raise ProductConfigError("role_models must be a JSON object")
        return cls.create(
            source_language=str(payload.get("source_language", "en")),
            target_language=str(payload.get("target_language", "zh-Hans")),
            profile_name=str(payload.get("profile_name", "publication")),
            provider=str(payload.get("provider", "openai-responses")),
            base_url=str(payload.get("base_url", "https://api.openai.com/v1")),
            api_key_env=str(payload.get("api_key_env", "OPENAI_API_KEY")),
            default_model=str(payload["default_model"]),
            role_models={str(key): str(value) for key, value in raw_roles.items()},
            reasoning_effort=str(payload.get("reasoning_effort", "high")),
            request_timeout_seconds=float(
                payload.get("request_timeout_seconds", 300.0)
            ),
            enable_web_research=bool(payload.get("enable_web_research", True)),
            max_planning_calls=int(payload.get("max_planning_calls", 500)),
            max_translation_calls=int(payload.get("max_translation_calls", 100_000)),
            global_review_chunk_characters=int(
                payload.get("global_review_chunk_characters", 40_000)
            ),
            max_estimated_tokens=int(
                payload.get("max_estimated_tokens", 1_000_000_000)
            ),
            max_estimated_microusd=int(
                payload.get("max_estimated_microusd", 1_000_000_000)
            ),
        )

    @classmethod
    def load(cls, path: str | Path) -> "ProductConfig":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProductConfigError("cannot read product configuration") from exc
        if not isinstance(payload, dict):
            raise ProductConfigError("product configuration must be a JSON object")
        return cls.from_payload(payload)
