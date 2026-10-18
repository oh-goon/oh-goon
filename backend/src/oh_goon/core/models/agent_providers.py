"""Explicit model connections, independent of provider servers and Agent execution."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, JsonValue, SecretStr, field_validator, model_validator

from oh_goon.core.errors import InvalidRequest
from oh_goon.core.models.base import ImmutableModel

ProviderApi = Literal["ollama", "openai-chat-completions", "openai-responses", "anthropic-messages"]
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_OWNED_HEADERS = {
    "host",
    "content-length",
    "content-type",
    "transfer-encoding",
    "connection",
    "accept-encoding",
    "proxy-authorization",
}
_SECRET_HEADERS = {"authorization", "proxy-authorization", "x-api-key", "cookie"}
_OWNED_PARAMETERS = {
    "model",
    "messages",
    "input",
    "stream",
    "max_tokens",
    "max_completion_tokens",
    "max_output_tokens",
}


class AgentProviderError(InvalidRequest):
    """Safe provider/configuration refusal; messages never contain credential values."""

    code = "AGENT_PROVIDER_ERROR"


def _http_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("endpoint must be an HTTP(S) URL with a valid port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(ch.isspace() or ord(ch) < 32 for ch in value)
        or "\\" in value
        or port == 0
    ):
        raise ValueError("endpoint must be an HTTP(S) URL without credentials, query or fragment")
    return value.rstrip("/")


def _header_value(value: str) -> str:
    if not value or len(value) > 8192 or any(ord(ch) < 32 or ord(ch) > 126 for ch in value):
        raise ValueError("header values must be bounded printable ASCII")
    return value


class AgentProvider(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    api: ProviderApi
    base_url: str = Field(min_length=1, max_length=2048)
    api_key_env: str | None = None
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    headers_from_env: dict[str, str] = Field(default_factory=dict, max_length=32)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600, allow_inf_nan=False)
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    max_response_bytes: int = Field(default=1_048_576, ge=1024, le=16_777_216)
    proxy_url: str | None = None
    ca_file: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("base_url", "proxy_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        return _http_url(value) if value is not None else None

    @field_validator("api_key_env")
    @classmethod
    def validate_key_reference(cls, value: str | None) -> str | None:
        if value is not None and _ENV_NAME.fullmatch(value) is None:
            raise ValueError("api_key_env must name an environment variable")
        return value

    @model_validator(mode="after")
    def validate_headers(self) -> AgentProvider:
        seen = set()
        auth_header = "x-api-key" if self.api == "anthropic-messages" else "authorization"
        if self.api_key_env is not None:
            seen.add(auth_header)
        for headers, from_env in ((self.headers, False), (self.headers_from_env, True)):
            for name, value in headers.items():
                lower = name.lower()
                if _HEADER_NAME.fullmatch(name) is None or lower in _OWNED_HEADERS or lower in seen:
                    raise ValueError(
                        "header names must be unique and leave HTTP envelope fields to the client"
                    )
                if from_env:
                    if _ENV_NAME.fullmatch(value) is None:
                        raise ValueError(
                            "headers_from_env must reference environment variable names"
                        )
                else:
                    if lower in _SECRET_HEADERS:
                        raise ValueError(
                            "credential headers require api_key_env or headers_from_env"
                        )
                    _header_value(value)
                seen.add(lower)
        return self


class AgentModel(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider: str
    model: str = Field(min_length=1, max_length=240)
    max_output_tokens: int = Field(default=1024, ge=1, le=1_048_576)
    chat_token_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=32)

    @field_validator("model")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if value != value.strip() or any(ord(ch) < 32 for ch in value):
            raise ValueError("model must be a non-empty printable provider model ID")
        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if _OWNED_PARAMETERS.intersection(value):
            raise ValueError(
                "native parameters cannot replace model, messages, input, stream or token limits"
            )
        try:
            encoded = json.dumps(value, allow_nan=False).encode()
        except ValueError as exc:
            raise ValueError("native parameters must contain finite JSON values") from exc
        if len(encoded) > 65_536:
            raise ValueError("native parameters exceed 65536 bytes")
        return value


@dataclass(frozen=True, slots=True)
class ResolvedAgentModel:
    """Selected connection snapshot; consumers never reread config or environment."""

    profile: str
    provider_name: str
    provider: AgentProvider
    model: str
    max_output_tokens: int
    chat_token_parameter: str
    parameters_json: bytes
    headers: tuple[tuple[str, SecretStr], ...] = field(repr=False)


class AgentProvidersConfig(ImmutableModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: int = Field(default=1, ge=1, le=1)
    default_model: str | None = None
    providers: dict[str, AgentProvider] = Field(default_factory=dict, max_length=64)
    models: dict[str, AgentModel] = Field(default_factory=dict, max_length=256)

    @model_validator(mode="after")
    def validate_references(self) -> AgentProvidersConfig:
        if any(_NAME.fullmatch(name) is None for name in (*self.providers, *self.models)):
            raise ValueError(
                "provider and model profile names must use letters, numbers, underscores or hyphens"
            )
        if any(model.provider not in self.providers for model in self.models.values()):
            raise ValueError("every model profile must reference a declared provider")
        if self.default_model is not None and self.default_model not in self.models:
            raise ValueError("default_model must reference a declared model profile")
        return self

    def resolve(self, profile: str | None, *, environment: Mapping[str, str]) -> ResolvedAgentModel:
        selected = profile if profile is not None else self.default_model
        if selected is None or selected not in self.models:
            raise AgentProviderError(
                "Select a configured model profile with --model or default_model"
            )
        model = self.models[selected]
        provider = self.providers[model.provider]
        headers = dict(provider.headers)

        def credential(name: str) -> str:
            value = environment.get(name)
            if not value:
                raise AgentProviderError(f"Selected provider requires environment variable {name}")
            try:
                return _header_value(value)
            except ValueError as exc:
                raise AgentProviderError(
                    "Selected provider credential is not a valid HTTP header value"
                ) from exc

        for name, variable in provider.headers_from_env.items():
            headers[name] = credential(variable)
        if provider.api_key_env is not None:
            key = credential(provider.api_key_env)
            if provider.api == "anthropic-messages":
                headers["x-api-key"] = key
            else:
                headers["Authorization"] = f"Bearer {key}"
        return ResolvedAgentModel(
            profile=selected,
            provider_name=model.provider,
            provider=provider.model_copy(deep=True),
            model=model.model,
            max_output_tokens=model.max_output_tokens,
            chat_token_parameter=model.chat_token_parameter,
            parameters_json=json.dumps(model.parameters, allow_nan=False).encode(),
            headers=tuple((name, SecretStr(value)) for name, value in headers.items()),
        )


@dataclass(frozen=True, slots=True)
class AgentMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class AgentReply:
    profile: str
    provider: str
    model: str
    text: str
    finish_reason: str
    response_model: str | None = None
