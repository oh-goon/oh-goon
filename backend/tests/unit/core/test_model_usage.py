"""Reported usage preserves uncertainty, source semantics and source partition."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.models.model_usage import (
    MAX_MODEL_USAGE_COUNT,
    ModelUsageObservation,
    ModelUsageReport,
    reported_token_values,
)


def observation(**changes: object) -> ModelUsageObservation:
    return ModelUsageObservation.model_validate(
        {
            "observation_id": "request-observation-1",
            "source": "anthropic-messages",
            "scope": "request",
            "scope_identity": "request-1",
            "requested_model": "selected-model",
            "reported_model": "actual-model-revision",
            "input_semantics": "excludes_cache",
            "input_tokens": 7,
            "output_tokens": 0,
            "cache_read_tokens": 20,
            "completeness": "reported",
            **changes,
        }
    )


def test_usage_roundtrip_preserves_zero_unknown_and_disjoint_cache_without_inferred_total():
    report = ModelUsageReport(observations=(observation(),))
    restored = ModelUsageReport.model_validate_json(report.model_dump_json())
    assert restored == report
    value = restored.observations[0]
    assert value.input_tokens == 7 and value.output_tokens == 0
    assert value.cache_read_tokens == 20 and value.input_semantics == "excludes_cache"
    assert value.total_tokens is None and value.reasoning_tokens is None and value.cost is None
    assert value.requested_model != value.reported_model
    assert value.implementation_identity is None


@pytest.mark.parametrize("bad", [True, "42", 42.0, -1, MAX_MODEL_USAGE_COUNT + 1, float("nan")])
def test_optional_malformed_counts_remain_unknown_without_discarding_other_observations(bad):
    fields, completeness = reported_token_values({"input_tokens": 0, "output_tokens": bad})
    assert fields["input_tokens"] == 0
    assert fields["output_tokens"] is None
    assert fields["total_tokens"] is None
    assert completeness == "partial"
    with pytest.raises(ValidationError):
        observation(output_tokens=bad)


def test_missing_and_wholly_malformed_telemetry_have_different_explicit_uncertainty():
    absent, status = reported_token_values({})
    assert all(value is None for value in absent.values()) and status == "unknown"
    malformed, status = reported_token_values({"input_tokens": "not-a-measurement"})
    assert all(value is None for value in malformed.values()) and status == "malformed"
    boundary, status = reported_token_values({"input_tokens": MAX_MODEL_USAGE_COUNT})
    assert boundary["input_tokens"] == MAX_MODEL_USAGE_COUNT and status == "reported"


def test_report_refuses_repeated_source_scope_instead_of_counting_snapshots_twice():
    first = observation(scope="native_thread", source="codex-service")
    duplicate = first.model_copy(update={"observation_id": "second-callback", "input_tokens": 100})
    with pytest.raises(ValidationError, match="one observation per exact source scope"):
        ModelUsageReport(observations=(first, duplicate))
    separate = observation(observation_id="request-observation-2", scope_identity="request-2")
    assert len(ModelUsageReport(observations=(observation(), separate)).observations) == 2


def test_usage_schema_cannot_retain_arbitrary_provider_payload_or_cost_claim():
    with pytest.raises(ValidationError):
        observation(raw_response={"private_transcript": "untrusted"})
    with pytest.raises(ValidationError):
        observation(cost=0)
