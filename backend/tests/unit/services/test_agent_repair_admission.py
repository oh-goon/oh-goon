"""Repair permissions and legacy receipts retain explicit bounded authority."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_repair import AgentRepairBudget, AgentRepairContext
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, StepStatusCondition
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits
from oh_goon.core.time import utc_now
from oh_goon.services.fleet_plan_repair import validate_agent_repair_plan
from tests.integration.test_agent_bounded_repair import _repair_case


@pytest.mark.parametrize(
    "change",
    [
        {"max_attempts": 0},
        {"max_attempts": True},
        {"max_attempts": 33},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"step_timeout_seconds": float("nan")},
        {"step_timeout_seconds": True},
    ],
)
def test_invalid_limits_are_not_repair_authority(change):
    values = dict(max_attempts=2, timeout_seconds=30, step_timeout_seconds=10)
    with pytest.raises(ValidationError):
        AgentRepairBudget(**{**values, **change})


def test_attempt_metadata_cannot_exceed_budget_or_omit_parent():
    base = dict(
        repair_id="repair",
        attempt=2,
        max_attempts=2,
        limits=FleetPlanRunLimits(
            deadline_at=utc_now() + timedelta(seconds=30), step_timeout_seconds=10
        ),
    )
    with pytest.raises(ValidationError, match="previous Run"):
        AgentRepairContext(**base)
    with pytest.raises(ValidationError, match="exceeds"):
        AgentRepairContext(**{**base, "max_attempts": 1, "previous_run_id": "previous"})


@pytest.mark.parametrize(
    "attack", ["condition", "retry", "apply_path", "apply_wrapper", "missing_feedback"]
)
def test_unsafe_loop_shape_is_refused_before_execution(tmp_path, monkeypatch, attack):
    plan_path, _, options, handoff, _ = _repair_case(tmp_path, monkeypatch)
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    plan, _, _ = FleetPlanResolver(
        goon_templates=GoonTemplateStore()
    ).resolve_frozen_local_search_agent(
        plan_path,
        inputs=options["inputs"],
        repository_root=options["agent_repository_root"],
        skill_root=options["agent_skill_root"],
        allowed_acceptance=options["allowed_agent_acceptance"],
        handoff=handoff,
    )
    steps = list(plan.steps)
    if attack == "condition":
        steps[2] = steps[2].model_copy(
            update={"when": StepStatusCondition(step_id=steps[0].step_id, statuses=("FAILED",))}
        )
    elif attack == "retry":
        from oh_goon.core.models.fleet_plan_step_primitives import StepRetryPolicy

        steps[2] = steps[2].model_copy(
            update={
                "retry": StepRetryPolicy(
                    max_attempts=2,
                    known_transient_exit_codes=(2,),
                    reason="unbounded apply",
                    delays_seconds=(0,),
                )
            }
        )
    elif attack == "apply_path":
        argv = list(steps[2].argv)
        argv[-1] = "not-captured.txt"
        steps[2] = steps[2].model_copy(update={"argv": tuple(argv)})
    elif attack == "apply_wrapper":
        steps[2] = steps[2].model_copy(update={"argv": ("unrelated-runner", *steps[2].argv)})
    else:
        steps[3] = steps[3].model_copy(update={"outputs": ()})
    candidate = plan.model_copy(update={"steps": tuple(steps)})
    with pytest.raises(FleetPlanInvalid):
        validate_agent_repair_plan(candidate, handoff)
    assert not (tmp_path / "calls").exists()


def test_saved_version43_plan_keeps_its_original_identity_on_readback():
    payload = (
        Path(__file__).resolve().parents[2] / "fixtures/agent_repair_legacy_plan43.json"
    ).read_text()
    old = ResolvedFleetPlan.model_validate_json(payload)
    assert old.contract_version == 43
    reopened = ResolvedFleetPlan.model_validate_json(old.model_dump_json())
    assert reopened == old
    assert reopened.plan_id == old.plan_id
