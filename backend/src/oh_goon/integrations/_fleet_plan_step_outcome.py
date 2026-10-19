from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.fleet_observation import FleetObservationEvidence
from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
    PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
    PLAN_TIMEOUT_RETURN_CODE,
    ResolvedPlanStep,
    StepFailurePolicy,
    StepLifecycle,
)
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationEvidence,
    PlanReleaseEffectEvidence,
)
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt, RuntimeAuthorityReceipt
from oh_goon.engine.errors import OperationCancelledError


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """Physical outcome of one Fleet plan step attempt."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    skipped: bool = False
    release_effect: PlanReleaseEffectEvidence | None = None
    release_verification: KubernetesRolloutVerificationEvidence | None = None
    observations: tuple[FleetObservationEvidence, ...] = ()
    supervisor: CommandSupervisorReceipt | None = None
    runtime_authority: RuntimeAuthorityReceipt | None = None
    execution_authority_economics: tuple[ExecutionAuthorityEconomicsReceipt, ...] = ()
    physical_execution_id: str | None = None


def output_contract_failure(
    step: ResolvedPlanStep,
    error: FleetPlanInvalid,
    argv: tuple[str, ...],
    *,
    base: StepOutcome | None = None,
) -> StepOutcome:
    if base is not None:
        stderr = (base.stderr + f"\n{error}").strip()
        return replace(
            base,
            argv=argv or base.argv or (step.kind, step.step_id),
            returncode=PLAN_CONTRACT_RETURN_CODE,
            stderr=stderr,
        )
    return StepOutcome(
        argv=argv or (step.kind, step.step_id),
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stderr=str(error),
    )


def skipped_after_failure(step: ResolvedPlanStep) -> StepOutcome:
    return StepOutcome(
        argv=(),
        returncode=0,
        skipped=True,
        stdout=f"Step {step.step_id} skipped after an earlier stop-policy failure",
    )


def is_finalizer(step: ResolvedPlanStep) -> bool:
    return step.lifecycle is StepLifecycle.FINALIZER


def wait_for_retry(delay_seconds: float, cancel_requested: Callable[[], bool] | None) -> None:
    deadline = time.monotonic() + delay_seconds
    while True:
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Fleet plan cancelled during retry delay")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def step_succeeded(step: ResolvedPlanStep, outcome: StepOutcome) -> bool:
    if outcome.skipped:
        return True
    return outcome.returncode in getattr(step, "success_codes", (0,))


def failure_policy(step: ResolvedPlanStep) -> StepFailurePolicy:
    return getattr(step, "on_failure", StepFailurePolicy.STOP)


def executable_not_runnable_outcome(step: ResolvedPlanStep, executable: str) -> StepOutcome:
    return StepOutcome(
        argv=tuple(getattr(step, "argv", ())) or (executable,),
        returncode=PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
        stderr=f"Executable is not runnable: {executable}",
    )


def executable_not_found_outcome(step: ResolvedPlanStep, executable: str) -> StepOutcome:
    return StepOutcome(
        argv=tuple(getattr(step, "argv", ())) or (executable,),
        returncode=PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
        stderr=f"Executable not found: {executable}",
    )


def timeout_outcome(step: ResolvedPlanStep, error: subprocess.TimeoutExpired) -> StepOutcome:
    command = error.cmd
    if isinstance(command, str):
        argv = (command,)
    else:
        argv = tuple(str(item) for item in command)
    if not argv:
        argv = (step.kind, step.step_id)
    return StepOutcome(
        argv=argv,
        returncode=PLAN_TIMEOUT_RETURN_CODE,
        stderr=f"Fleet plan step timed out after {error.timeout} seconds",
        supervisor=getattr(error, "supervisor", None),
        runtime_authority=getattr(error, "runtime_authority", None),
    )


def from_command(result: CommandResult) -> StepOutcome:
    return StepOutcome(
        argv=result.argv,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        supervisor=result.supervisor,
        runtime_authority=result.runtime_authority,
        execution_authority_economics=result.execution_authority_economics,
        physical_execution_id=result.physical_execution_id,
    )
