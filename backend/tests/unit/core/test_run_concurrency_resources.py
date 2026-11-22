"""Explicit two-Run budget partition and independent shared/Run identities."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.models.run_resources import (
    NativeRuntimeSliceEvidence,
    RunConcurrencyAdmission,
)
from tests.factories.run_resources import native_runtime_slice_evidence as aggregate
from tests.factories.run_resources import run_concurrency_policy as policy
from tests.factories.run_resources import run_resource_envelope


@pytest.mark.parametrize(
    "field,value", [("cpu_millis", 751), ("memory_mib", 513), ("max_processes", 257)]
)
def test_workers_and_controller_reserve_cannot_oversubscribe_native_budget(field, value):
    fields = policy().worker_resources.model_dump()
    fields[field] = value
    with pytest.raises(ValidationError, match="exceed aggregate budget"):
        policy(worker_resources=fields)


@pytest.mark.parametrize("group", ["aggregate_resources", "worker_resources", "controller_reserve"])
def test_explicit_two_run_mode_requires_all_resource_dimensions(group):
    with pytest.raises(ValidationError, match="complete finite"):
        policy(**{group: {"cpu_millis": getattr(policy(), group).cpu_millis}})


def test_shared_budget_identity_does_not_conflate_original_run_scope():
    first = RunConcurrencyAdmission(
        policy=policy(),
        aggregate=aggregate(),
        fleet_run_id="run-a",
        worker_unit_name="oh-goon-run-a.scope",
        worker_scope_identity="sha256:" + "b" * 64,
    )
    second = RunConcurrencyAdmission(
        policy=policy(),
        aggregate=aggregate(),
        fleet_run_id="run-b",
        worker_unit_name="oh-goon-run-b.scope",
        worker_scope_identity="sha256:" + "c" * 64,
    )
    assert first.budget_identity == second.budget_identity
    assert first.identity != second.identity
    assert (
        RunConcurrencyAdmission.model_validate_json(
            first.model_dump_json(exclude_computed_fields=True)
        )
        == first
    )


def test_native_budget_facts_cannot_claim_different_effective_controls():
    fields = aggregate().model_dump()
    fields["cpu_max"] = "max 100000"
    with pytest.raises(ValidationError, match="differ from explicit"):
        NativeRuntimeSliceEvidence.model_validate(fields)
    fields = aggregate().model_dump()
    fields["memory_max_bytes"] += 4096
    with pytest.raises(ValidationError, match="differ from explicit"):
        NativeRuntimeSliceEvidence.model_validate(fields)


def test_worker_subject_is_explicit_without_rewriting_legacy_cli_receipt():
    original = run_resource_envelope()
    assert "subject" not in original.model_dump()
    worker = original.model_copy(update={"subject": "worker"})
    assert worker.model_dump()["subject"] == "worker"
    assert type(original).model_validate_json(worker.model_dump_json()) == worker
