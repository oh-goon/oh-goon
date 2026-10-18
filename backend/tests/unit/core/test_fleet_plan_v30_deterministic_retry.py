from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.fleet_plan_retry import retry_decision
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan, StepRetryPolicy
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ExecutionPurpose,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCapabilities,
    UnrestrictedHostPlanEgress,
)


def _execution() -> CicdExecutionPolicy:
    return CicdExecutionPolicy(
        purpose=ExecutionPurpose.CICD,
        trust_repository_code=True,
        resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=512, max_processes=64),
    )


def test_retry_policy_requires_reason_and_complete_declared_schedule() -> None:
    with pytest.raises(ValidationError, match="explicit reason"):
        StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(75,),
            delays_seconds=(1,),
        )
    with pytest.raises(ValidationError, match="exactly 2 declared"):
        StepRetryPolicy(
            max_attempts=3,
            known_transient_exit_codes=(75,),
            reason="temporary dependency service failure",
            delays_seconds=(1,),
        )


def test_retry_policy_has_no_hidden_backoff_or_jitter() -> None:
    policy = StepRetryPolicy(
        max_attempts=4,
        known_transient_exit_codes=(75, 124),
        reason="known completed transient failure",
        delays_seconds=(1, 3, 9),
    )
    assert [policy.delay_after(attempt) for attempt in (1, 2, 3)] == [1, 3, 9]


def test_retry_decision_only_replays_declared_completed_exit_codes() -> None:
    policy = StepRetryPolicy(
        max_attempts=3,
        known_transient_exit_codes=(75,),
        reason="temporary upstream unavailable",
        delays_seconds=(2, 7),
    )
    scheduled = retry_decision(policy, attempt_number=1, exit_code=75)
    assert scheduled.disposition == "SCHEDULED"
    assert scheduled.delay_seconds == 2
    assert scheduled.next_attempt == 2
    assert scheduled.policy_reason == "temporary upstream unavailable"

    undeclared = retry_decision(policy, attempt_number=1, exit_code=1)
    assert undeclared.disposition == "EXIT_CODE_NOT_DECLARED"
    assert not hasattr(undeclared, "next_attempt")

    exhausted = retry_decision(policy, attempt_number=3, exit_code=75)
    assert exhausted.disposition == "ATTEMPTS_EXHAUSTED"
    assert exhausted.policy_reason == "temporary upstream unavailable"



def test_outer_retry_is_rejected_outside_attempt_private_strict_cicd() -> None:
    step = ResolvedCommandStep(
        step_id="local",
        argv=("local-check",),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(),
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(75,),
            reason="local lock contention",
            delays_seconds=(1,),
        ),
    )

    with pytest.raises(ValidationError, match="outside strict CI/CD"):
        ResolvedFleetPlan.create(
            name="retry-direct",
            description="",
            execution=MaintenanceExecutionPolicy(),
            steps=(step,),
        )

def test_outer_retry_is_rejected_when_step_has_network_authority() -> None:
    step = ResolvedCommandStep(
        step_id="networked",
        argv=("uv", "sync", "--frozen"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(
            egress=UnrestrictedHostPlanEgress(),
        ),
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(75,),
            reason="temporary network failure",
            delays_seconds=(1,),
        ),
    )
    with pytest.raises(ValidationError, match="cannot use automatic retries with network egress"):
        ResolvedFleetPlan.create(
            name="retry-network",
            description="",
            execution=_execution(),
            steps=(step,),
        )


def test_retry_codes_cannot_also_be_success_codes() -> None:
    step = ResolvedCommandStep(
        step_id="local",
        argv=("local-check",),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(),
        success_codes=(0, 75),
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(75,),
            reason="local lock contention is replay-safe",
            delays_seconds=(1,),
        ),
    )

    with pytest.raises(ValidationError, match="retry codes cannot also be success codes: 75"):
        ResolvedFleetPlan.create(
            name="retry-overlap",
            description="",
            execution=_execution(),
            steps=(step,),
        )
