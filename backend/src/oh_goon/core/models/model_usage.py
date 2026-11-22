"""Bounded reported model measurements; never billing or execution authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, TypedDict

from pydantic import Field, field_validator, model_validator

from oh_goon.core.models.base import ImmutableModel

MAX_MODEL_USAGE_COUNT = 9_007_199_254_740_991
MAX_MODEL_USAGE_RECEIPT_BYTES = 512 * 1024
ModelUsageCompleteness = Literal["reported", "partial", "malformed", "unknown"]
ModelUsageCount = Annotated[int, Field(strict=True, ge=0, le=MAX_MODEL_USAGE_COUNT)]
ModelUsageTokenName = Literal[
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
]
_TOKEN_FIELDS: tuple[ModelUsageTokenName, ...] = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "total_tokens",
)


class ModelUsageTokenFields(TypedDict):
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None


def reported_token_values(
    values: Mapping[str, object],
) -> tuple[ModelUsageTokenFields, ModelUsageCompleteness]:
    """Keep independently valid allowlisted counts, without arithmetic or coercion.

    Callers map their selected producer's fields into these names. Missing/null
    quantities stay unknown; malformed optional fields do not change work results.
    """
    result: ModelUsageTokenFields = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
    }
    malformed = False
    known = False
    for name in _TOKEN_FIELDS:
        value = values.get(name)
        if value is None:
            continue
        if type(value) is int and 0 <= value <= MAX_MODEL_USAGE_COUNT:
            result[name] = value
            known = True
        else:
            malformed = True
    if malformed:
        return result, "partial" if known else "malformed"
    return result, "reported" if known else "unknown"


class ModelUsageObservation(ImmutableModel):
    """One request measurement or one native cumulative snapshot.

    A cumulative snapshot replaces the preceding observation for that native
    scope; it is never an additive delta. Cache/reasoning quantities retain the
    producer's inclusion semantics. No missing total or monetary cost is inferred.
    The accepting Attempt or cancellation event supplies execution provenance.
    """

    observation_id: str = Field(min_length=1, max_length=160)
    source: Literal[
        "ollama",
        "openai-chat-completions",
        "openai-responses",
        "anthropic-messages",
        "codex-service",
    ]
    scope: Literal["request", "native_thread"]
    scope_identity: str = Field(min_length=1, max_length=160)
    implementation_identity: str | None = Field(default=None, min_length=1, max_length=160)
    requested_model: str | None = Field(default=None, min_length=1, max_length=256)
    reported_model: str | None = Field(default=None, min_length=1, max_length=256)
    input_semantics: Literal["includes_cache", "excludes_cache", "unknown"] = "unknown"
    output_semantics: Literal["includes_reasoning", "unknown"] = "unknown"
    unit: Literal["tokens"] = "tokens"
    input_tokens: ModelUsageCount | None = None
    output_tokens: ModelUsageCount | None = None
    cache_read_tokens: ModelUsageCount | None = None
    cache_write_tokens: ModelUsageCount | None = None
    reasoning_tokens: ModelUsageCount | None = None
    total_tokens: ModelUsageCount | None = None
    completeness: ModelUsageCompleteness
    # None is intentional: selected APIs supply no qualified monetary charge.
    # A native catalogue calculation is not a producer-reported bill.
    cost: None = None

    @field_validator(
        "observation_id",
        "scope_identity",
        "implementation_identity",
        "requested_model",
        "reported_model",
    )
    @classmethod
    def bounded_identity(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or not value.isprintable()):
            raise ValueError("Model usage identities must be bounded printable values")
        return value


class ModelUsageReport(ImmutableModel):
    """Allowlisted observations, retained once per original physical attempt.

    Absent reports (including historical Attempts) mean unknown, never zero.
    Completeness describes supplied telemetry, not completeness of model work.
    """

    observations: tuple[ModelUsageObservation, ...] = Field(min_length=1, max_length=128)
    truncated: bool = False

    @model_validator(mode="after")
    def distinct_observations(self) -> ModelUsageReport:
        identifiers = [item.observation_id for item in self.observations]
        scopes = [(item.source, item.scope, item.scope_identity) for item in self.observations]
        if len(set(identifiers)) != len(identifiers) or len(set(scopes)) != len(scopes):
            raise ValueError("Usage reports retain one observation per exact source scope")
        return self
