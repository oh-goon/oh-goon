from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.game_tape_projection import project_game_tape
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.models.schedule import ScheduleCreate
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunStatus
from tests.factories.admitted_execution import admitted_fleet_plan_execution
from tests.factories.schedules import scheduled_plan

RETRYING_PLAN = """version: 1
execution: {purpose: cicd}
steps:
  - id: check
    run: "true"
    retry:
      max_attempts: 2
      known_transient_exit_codes: [75]
      reason: The fixture models one declared transient process failure.
      delays_seconds: [0]
"""


def _resolved_target(name: str) -> FleetPlanTarget:
    plan = scheduled_plan(name)
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=ExecutionIdentity(digest=build_execution_lock(plan).digest),
    )


def test_canonical_fixture_crosses_locked_admission_and_execution_boundary(
    tmp_path: Path,
) -> None:
    executed = admitted_fleet_plan_execution(tmp_path)

    assert executed.detail.run.status is FleetRunStatus.COMPLETED
    assert isinstance(executed.detail.execution, StrictFleetExecutionAdmission)
    assert executed.detail.run.target == executed.target
    assert executed.operation.target == executed.target
    assert [request.argv[-1] for request in executed.process_requests] == ["true"]
    assert executed.remaining_effect_attempts == 0

    tape = project_game_tape(executed.detail)
    assert tape.execution == executed.detail.execution
    assert tape.execution_reference == executed.target.execution_reference


def test_canonical_fixture_observes_declared_physical_retry_outcomes(tmp_path: Path) -> None:
    executed = admitted_fleet_plan_execution(
        tmp_path,
        plan_text=RETRYING_PLAN,
        returncodes=(75, 0),
    )

    step = executed.operation.evidence.plan.steps[0]
    assert [request.argv[-1] for request in executed.process_requests] == ["true", "true"]
    assert [attempt.returncode for attempt in step.attempts] == [75, 0]
    assert executed.detail.run.status is FleetRunStatus.COMPLETED


def test_reconstructed_target_rejects_digest_from_other_resolved_plan() -> None:
    plan = scheduled_plan("identity-a")
    other = ResolvedFleetPlan.create(
        execution=plan.execution,
        name="identity-b",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("true",),
            ),
        ),
    )

    with pytest.raises(ValidationError, match="does not match the resolved Plan semantics"):
        FleetPlanTarget(
            kind="fleet_plan",
            plan=plan,
            source=PlanFileSource(sha256="0" * 64),
            identity=ExecutionIdentity(digest=build_execution_lock(other).digest),
        )


def test_schedule_transports_verified_target_without_duplicate_execution_fields() -> None:
    target = _resolved_target("boundary")
    request = ScheduleCreate(
        name="Boundary",
        repository_ids=("repo-a",),
        repository_instance_ids={"repo-a": "instance-boundary-proof"},
        target=target,
        interval_minutes=60,
    )
    payload = request.model_dump(mode="json")

    assert payload["target"]["identity"]["digest"] == target.identity.digest
    assert "plan" not in payload
    assert "plan_source" not in payload
    assert "execution_digest" not in payload


def test_verified_identity_is_required_for_every_fleet_plan_target() -> None:
    with pytest.raises(ValidationError, match="identity"):
        FleetPlanTarget(
            kind="fleet_plan",
            plan=scheduled_plan("missing-identity"),
            source=PlanFileSource(sha256="0" * 64),
        )
