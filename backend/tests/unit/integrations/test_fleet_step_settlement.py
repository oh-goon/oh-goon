"""Step consumers use accepted semantics, independent of native transport shape."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    GitResultContract,
    ResolvedCommandStep,
    ResolvedDependencyInventoryStep,
    ResolvedGitStep,
)
from oh_goon.core.models.fleet_plan_evidence import PlanStepSettlementEvidence
from oh_goon.engine.command import CommandCancelledError, CommandResult, CommandSupervisorReceipt
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations._fleet_plan_step_evidence import (
    attempt_evidence,
    cancellation_evidence,
    step_evidence,
)
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    from_command,
    settle_step_execution,
    step_succeeded,
)


@pytest.mark.parametrize(
    "code,reason,completion,quiescent,expected,cleanup",
    [
        (0, "none", "process_exit", True, "SUCCESS", "VERIFIED"),
        (17, "none", "process_exit", True, "FAILED", "VERIFIED"),
        (0, "cancel", "process_exit", True, "CANCELLED", "VERIFIED"),
        (0, "cancel", "service_shutdown", True, "SUCCESS", "VERIFIED"),
        (17, "cancel", "service_shutdown", True, "FAILED", "VERIFIED"),
        (124, "cancel", "service_shutdown", True, "TIMED_OUT", "VERIFIED"),
        (0, "timeout", "process_exit", True, "TIMED_OUT", "VERIFIED"),
        (0, "output_limit", "process_exit", True, "OUTPUT_LIMITED", "VERIFIED"),
        (0, "authority", "process_exit", True, "AUTHORITY_LOST", "VERIFIED"),
        (0, "output_cleanup", "process_exit", False, "INCOMPLETE", "UNVERIFIED"),
        (0, "output_cleanup", "process_exit", True, "SUCCESS", "VERIFIED"),
        (0, "none", "process_exit", None, "INCOMPLETE", "UNAVAILABLE"),
    ],
)
def test_original_step_accepts_every_completion_and_retains_physical_facts(
    code, reason, completion, quiescent, expected, cleanup
):
    step = ResolvedCommandStep(step_id="work", argv=("native-work",))
    supervisor = (
        CommandSupervisorReceipt(
            process_group_cleanup_requested=reason != "none",
            cleanup_reason=reason,
            final_process_group_quiescent=quiescent,
        )
        if quiescent is not None
        else None
    )
    outcome = StepOutcome(
        argv=("native-work",), returncode=code, supervisor=supervisor, completion=completion
    )
    attempt = attempt_evidence(
        step=step,
        attempt=1,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
    )
    result = step_evidence(
        step=step,
        outcome=outcome,
        attempts=(attempt,),
        artifacts=(),
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
    )
    assert result.settlement.outcome == expected
    assert result.settlement.cleanup == cleanup
    assert result.settlement.owner == "fleet_step_settlement"
    assert result.settlement.observed_returncode is None
    if supervisor is not None:
        assert result.attempts[0].supervisor.cleanup_reason == reason
    reopened = type(result).model_validate_json(
        result.model_dump_json(exclude_computed_fields=True)
    )
    assert reopened.settlement == result.settlement
    assert step_succeeded(step, outcome) is (expected == "SUCCESS")
    if expected != "SUCCESS":
        assert result.status == "FAILED"
        assert result.returncode != 0
    else:
        assert result.status == "COMPLETED"


def test_cleanup_failure_keeps_observed_exit_separate_from_rejected_step():
    physical = CommandResult(
        returncode=0,
        stdout="",
        stderr="",
        argv=("native-work",),
        supervisor=CommandSupervisorReceipt(final_process_group_quiescent=False),
    )
    outcome = from_command(physical)
    assert outcome.returncode != 0
    assert outcome.observed_returncode == 0
    # A later output-contract failure cannot rewrite the original physical observation.
    assert replace(outcome, returncode=81).observed_returncode == 0
    attempt = attempt_evidence(
        step=ResolvedCommandStep(step_id="work", argv=("native-work",)),
        attempt=1,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
    )
    assert attempt.returncode != 0 and attempt.observed_returncode == 0


@pytest.mark.parametrize("missing", ["supervisor", "reason", "request", "identity"])
def test_cancellation_without_bound_valid_supervision_retains_uncertainty(missing):
    step = ResolvedCommandStep(step_id="native", argv=("native-work",))
    error = OperationCancelledError("cancelled")
    if missing != "supervisor":
        error.__cause__ = CommandCancelledError(
            "private raw control error",
            stdout="PRIVATE_NATIVE_OUTPUT",
            physical_execution_id=None if missing == "identity" else "original-physical",
            supervisor=CommandSupervisorReceipt(
                process_group_cleanup_requested=missing != "request",
                cleanup_reason="invalid" if missing == "reason" else "cancel",
            ),
        )
    receipt = cancellation_evidence(step, error)
    assert receipt.settlement.outcome == "INCOMPLETE"
    assert receipt.settlement.cleanup == "UNAVAILABLE"
    assert receipt.settlement.reason == "SUPERVISION_UNAVAILABLE"
    assert "PRIVATE_NATIVE_OUTPUT" not in receipt.model_dump_json()


def test_cancellation_receipt_rejects_upgrading_missing_cleanup_to_verified():
    from oh_goon.core.models.fleet_plan_evidence import PlanStepCancellationEvidence

    with pytest.raises(ValidationError, match="original verified cleanup"):
        PlanStepCancellationEvidence(
            settlement=PlanStepSettlementEvidence(
                outcome="CANCELLED", reason="CALLER_CANCELLED", cleanup="VERIFIED"
            ),
            physical_execution_id="claimed-original-physical",
        )


def test_process_free_inventory_succeeds_without_a_fabricated_supervisor(tmp_path):
    from oh_goon.integrations.fleet_plan_dependency_observation import execute_dependency_inventory
    from tests.factories.dependency_observations import dependency_snapshot

    dependency_snapshot(tmp_path, ("requests", "2.0"))
    step = ResolvedDependencyInventoryStep(step_id="inventory")
    outcome = execute_dependency_inventory(tmp_path, step)
    settled = settle_step_execution(step, outcome)
    assert step_succeeded(step, outcome)
    assert outcome.supervisor is None and outcome.observed_returncode is None
    assert settled.outcome == "SUCCESS" and settled.cleanup == "VERIFIED"
    assert settled.reason == "ACCEPTED_IN_PROCESS"


def test_process_free_completion_cannot_hide_a_process_or_failed_resource_cleanup():
    with pytest.raises(ValueError, match="owned process boundary"):
        StepOutcome(
            argv=("inventory",),
            returncode=0,
            completion="in_process",
            supervisor=CommandSupervisorReceipt(),
        )
    outcome = StepOutcome(
        argv=("inventory",), returncode=0, completion="in_process", resource_cleanup="unverified"
    )
    step = ResolvedDependencyInventoryStep(step_id="inventory")
    assert not step_succeeded(step, outcome)
    assert settle_step_execution(step, outcome).cleanup == "UNVERIFIED"


@pytest.mark.parametrize("completion", ["unhandled-new-completion", "", True, None])
def test_unknown_completion_cannot_fall_through_to_success(completion):
    with pytest.raises(FleetPlanInvalid, match="completion variant"):
        StepOutcome(
            argv=("work",),
            returncode=0,
            completion=completion,
            supervisor=CommandSupervisorReceipt(),
        )


@pytest.mark.parametrize("cleanup", ["unhandled-new-cleanup", "", True, None])
def test_unknown_resource_cleanup_cannot_fall_through_to_success(cleanup):
    with pytest.raises(FleetPlanInvalid, match="resource cleanup variant"):
        StepOutcome(
            argv=("work",),
            returncode=0,
            resource_cleanup=cleanup,
            supervisor=CommandSupervisorReceipt(),
        )


def test_native_command_cannot_claim_the_inventory_no_process_contract():
    with pytest.raises(FleetPlanInvalid, match="does not own"):
        settle_step_execution(
            ResolvedCommandStep(step_id="work", argv=("work",)),
            StepOutcome(argv=(), returncode=0, completion="in_process"),
        )


@pytest.mark.parametrize("code", [126, 127])
def test_unknown_process_boundary_does_not_acquire_known_refusal_by_exit_code(code):
    step = ResolvedCommandStep(step_id="work", argv=("work",))
    settled = settle_step_execution(step, StepOutcome(argv=step.argv, returncode=code))
    assert settled.outcome == "INCOMPLETE" and settled.cleanup == "UNAVAILABLE"


@pytest.mark.parametrize(
    "change",
    [
        {"returncode": 0},
        {"returncode": 17},
        {"skipped": True},
        {"supervisor": CommandSupervisorReceipt()},
        {"physical_execution_id": "claimed"},
        {"observed_returncode": 127},
        {"resource_cleanup": "unverified"},
    ],
)
def test_known_launch_refusal_cannot_claim_success_or_an_owned_process(change):
    with pytest.raises(FleetPlanInvalid, match="Launch refusal"):
        StepOutcome(**{"argv": ("work",), "returncode": 127, "completion": "not_started", **change})


def test_git_logical_rejection_retains_its_actual_successful_process_exit(tmp_path):
    from types import SimpleNamespace

    from oh_goon.integrations._fleet_plan_step_local_execution import execute_git

    physical = CommandResult(
        argv=("git", "status"),
        returncode=0,
        stdout=" M changed.py\n",
        stderr="",
        supervisor=CommandSupervisorReceipt(),
    )
    step = ResolvedGitStep(
        step_id="clean",
        argv=("status", "--porcelain", "--untracked-files=normal"),
        timeout_seconds=10,
        result_contract=GitResultContract.CLEAN_WORKTREE,
    )
    outcome = execute_git(
        SimpleNamespace(run=lambda *args, **kwargs: physical), tmp_path, step, None, environment={}
    )
    assert outcome.returncode == 1 and outcome.observed_returncode == 0
    assert outcome.supervisor is physical.supervisor
    assert settle_step_execution(step, outcome).outcome == "FAILED"


@pytest.mark.parametrize(
    "value",
    [
        {"outcome": "NEW_UNHANDLED_VARIANT"},
        {"outcome": "SUCCESS", "cleanup": "UNAVAILABLE", "reason": "ACCEPTED_EXIT"},
        {"outcome": "SUCCESS", "cleanup": "VERIFIED", "reason": "FAILED_EXIT"},
        {"outcome": "CANCELLED", "cleanup": "VERIFIED", "reason": "ACCEPTED_SERVICE_COMPLETION"},
        {"outcome": "FAILED", "cleanup": "UNAVAILABLE", "reason": "FAILED_TO_START"},
        {
            "outcome": "FAILED",
            "cleanup": "VERIFIED",
            "reason": "FAILED_TO_START",
            "observed_returncode": 127,
        },
    ],
)
def test_unknown_or_contradictory_settlement_cannot_establish_success(value):
    assert PlanStepSettlementEvidence().outcome == "INCOMPLETE"
    with pytest.raises(ValidationError):
        PlanStepSettlementEvidence.model_validate(value)
