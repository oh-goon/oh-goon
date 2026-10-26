from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, TypedDict, assert_never

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
    ResolvedCommandStep,
    ResolvedPlanStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    PlanOutputEvidence,
    PlanStandardIsolationEvidence,
    PlanStepSettlementEvidence,
    PlanUvPreparationEvidence,
)
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationEvidence,
    PlanReleaseEffectEvidence,
)
from oh_goon.core.models.fleet_plan_step_primitives import StepFailurePolicy, StepLifecycle
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.engine.command_runtime_authority import RuntimeAuthorityReceipt
from oh_goon.engine.errors import OperationCancelledError


class _SettlementFields(TypedDict):
    cleanup: Literal["VERIFIED", "UNVERIFIED", "UNAVAILABLE"]
    observed_returncode: int | None


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
    standard_isolation: PlanStandardIsolationEvidence | None = None
    uv_preparation: PlanUvPreparationEvidence | None = None
    # Tail capture retains diagnostic text; evidence covers the whole original raw stream.
    stdout_evidence: PlanOutputEvidence | None = None
    # Adapters distinguish native completion from process-free work and proven launch refusal.
    completion: Literal["process_exit", "service_shutdown", "in_process", "not_started"] = (
        "process_exit"
    )
    observed_returncode: int | None = None
    resource_cleanup: Literal["verified", "unverified"] = "verified"

    def __post_init__(self) -> None:
        if self.supervisor is not None and (
            type(self.supervisor.cleanup_reason) is not str
            or self.supervisor.cleanup_reason
            not in {"none", "timeout", "cancel", "authority", "output_cleanup", "output_limit"}
        ):
            raise FleetPlanInvalid("Unsupported Step supervisor cleanup reason")
        if type(self.resource_cleanup) is not str or self.resource_cleanup not in (
            "verified",
            "unverified",
        ):
            raise FleetPlanInvalid("Unsupported Step resource cleanup variant")
        if type(self.completion) is not str or self.completion not in (
            "process_exit",
            "service_shutdown",
            "in_process",
            "not_started",
        ):
            raise FleetPlanInvalid("Unsupported Step completion variant")
        if self.completion == "in_process" and (
            self.execution_authority_economics
            or any(
                value is not None
                for value in (
                    self.supervisor,
                    self.runtime_authority,
                    self.physical_execution_id,
                    self.standard_isolation,
                    self.observed_returncode,
                    self.release_effect,
                    self.release_verification,
                )
            )
        ):
            raise ValueError("In-process completion cannot carry an owned process boundary")
        if self.completion == "not_started" and (
            type(self.returncode) is not int
            or self.returncode
            not in {
                PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
                PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
            }
            or self.skipped
            or self.resource_cleanup != "verified"
            or self.execution_authority_economics
            or any(
                value is not None
                for value in (
                    self.supervisor,
                    self.runtime_authority,
                    self.physical_execution_id,
                    self.standard_isolation,
                    self.observed_returncode,
                    self.release_effect,
                    self.release_verification,
                )
            )
        ):
            raise FleetPlanInvalid("Launch refusal requires a failed, unstarted process boundary")
        if process_boundary_settled(self) is False:
            diagnostic = "Execution cleanup is unverified"
            if diagnostic not in self.stderr:
                object.__setattr__(
                    self, "stderr", (self.stderr.rstrip() + "\n" + diagnostic).lstrip("\n")
                )
            if self.returncode == 0:
                object.__setattr__(self, "returncode", PLAN_CONTRACT_RETURN_CODE)
            object.__setattr__(self, "skipped", False)
        if (
            self.supervisor is not None
            and self.supervisor.cleanup_reason in {"cancel", "timeout", "authority", "output_limit"}
            and not (
                self.supervisor.cleanup_reason == "cancel" and self.completion == "service_shutdown"
            )
            and self.returncode == 0
        ):
            object.__setattr__(self, "returncode", PLAN_CONTRACT_RETURN_CODE)


def process_boundary_settled(outcome: StepOutcome) -> bool | None:
    """Preserve unknown historical closure and reject an observed cleanup failure."""
    if outcome.resource_cleanup == "unverified":
        return False
    if outcome.completion in {"in_process", "not_started"}:
        return True
    supervisor = outcome.supervisor
    isolation = outcome.standard_isolation
    if (supervisor is not None and not supervisor.final_process_group_quiescent) or (
        isolation is not None and isolation.filesystem_cleanup == "unverified"
    ):
        return False
    if supervisor is None or (isolation is not None and isolation.filesystem_cleanup != "verified"):
        return None
    return True


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
            returncode=base.returncode
            if base.completion == "not_started"
            else PLAN_CONTRACT_RETURN_CODE,
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
    return settle_step_execution(step, outcome).outcome in {"SUCCESS", "SKIPPED"}


def _base_execution_succeeded(step: ResolvedPlanStep, outcome: StepOutcome) -> bool:
    if process_boundary_settled(outcome) is False:
        return False
    if outcome.supervisor is not None and (
        outcome.supervisor.cleanup_reason in {"timeout", "authority", "output_limit"}
        or (
            outcome.supervisor.cleanup_reason == "cancel"
            and outcome.completion != "service_shutdown"
        )
    ):
        return False
    if outcome.skipped:
        return True
    return outcome.returncode in getattr(step, "success_codes", (0,))


def settle_step_execution(
    step: ResolvedPlanStep, outcome: StepOutcome
) -> PlanStepSettlementEvidence:
    """Accept process facts once; downstream policies consume this typed meaning."""
    if outcome.completion == "in_process" and step.kind != "dependency_inventory":
        raise FleetPlanInvalid("Step workload does not own in-process completion")
    if outcome.completion == "not_started" and not isinstance(step, ResolvedCommandStep):
        raise FleetPlanInvalid("Only a direct Command Step can own a known launch refusal")
    closure = process_boundary_settled(outcome)
    cleanup: Literal["VERIFIED", "UNVERIFIED", "UNAVAILABLE"] = (
        "VERIFIED" if closure is True else "UNVERIFIED" if closure is False else "UNAVAILABLE"
    )
    common: _SettlementFields = {
        "cleanup": cleanup,
        "observed_returncode": outcome.observed_returncode,
    }
    if outcome.completion == "not_started":
        return PlanStepSettlementEvidence(outcome="FAILED", reason="FAILED_TO_START", **common)
    if outcome.skipped:
        return PlanStepSettlementEvidence(outcome="SKIPPED", reason="NOT_STARTED", **common)
    supervisor = outcome.supervisor
    reason = supervisor.cleanup_reason if supervisor is not None else "none"
    match reason:
        case "none" | "output_cleanup":
            pass
        case "cancel":
            if outcome.completion != "service_shutdown":
                return PlanStepSettlementEvidence(
                    outcome="CANCELLED", reason="CALLER_CANCELLED", **common
                )
        case "timeout":
            return PlanStepSettlementEvidence(
                outcome="TIMED_OUT", reason="DEADLINE_EXPIRED", **common
            )
        case "authority":
            return PlanStepSettlementEvidence(
                outcome="AUTHORITY_LOST", reason="AUTHORITY_REVOKED", **common
            )
        case "output_limit":
            return PlanStepSettlementEvidence(
                outcome="OUTPUT_LIMITED", reason="OUTPUT_LIMIT_EXCEEDED", **common
            )
        case unknown_reason:
            assert_never(unknown_reason)
    if outcome.returncode == PLAN_TIMEOUT_RETURN_CODE:
        return PlanStepSettlementEvidence(outcome="TIMED_OUT", reason="DEADLINE_EXPIRED", **common)
    if closure is False:
        return PlanStepSettlementEvidence(
            outcome="INCOMPLETE", reason="CLEANUP_UNVERIFIED", **common
        )
    if closure is None:
        return PlanStepSettlementEvidence(
            outcome="INCOMPLETE", reason="SUPERVISION_UNAVAILABLE", **common
        )
    if not _base_execution_succeeded(step, outcome):
        return PlanStepSettlementEvidence(outcome="FAILED", reason="FAILED_EXIT", **common)
    match outcome.completion:
        case "process_exit":
            accepted_reason: Literal[
                "ACCEPTED_EXIT", "ACCEPTED_SERVICE_COMPLETION", "ACCEPTED_IN_PROCESS"
            ] = "ACCEPTED_EXIT"
        case "service_shutdown":
            accepted_reason = "ACCEPTED_SERVICE_COMPLETION"
        case "in_process":
            accepted_reason = "ACCEPTED_IN_PROCESS"
        case unknown:
            assert_never(unknown)
    return PlanStepSettlementEvidence(outcome="SUCCESS", reason=accepted_reason, **common)


def failure_policy(step: ResolvedPlanStep) -> StepFailurePolicy:
    return getattr(step, "on_failure", StepFailurePolicy.STOP)


def executable_not_runnable_outcome(step: ResolvedCommandStep, executable: str) -> StepOutcome:
    return StepOutcome(
        completion="not_started",
        argv=tuple(getattr(step, "argv", ())) or (executable,),
        returncode=PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
        stderr=f"Executable is not runnable: {executable}",
    )


def executable_not_found_outcome(step: ResolvedCommandStep, executable: str) -> StepOutcome:
    return StepOutcome(
        completion="not_started",
        argv=tuple(getattr(step, "argv", ())) or (executable,),
        returncode=PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
        stderr=f"Executable not found: {executable}",
    )


def timeout_outcome(step: ResolvedPlanStep, error: subprocess.TimeoutExpired) -> StepOutcome:
    command = error.cmd
    argv: tuple[str, ...]
    if isinstance(command, str):
        argv = (command,)
    else:
        argv = tuple(str(item) for item in command)
    if not argv:
        argv = (step.kind, step.step_id)
    stdout = error.output or ""
    stderr = error.stderr or ""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    diagnostic = f"Fleet plan step timed out after {error.timeout} seconds"
    return StepOutcome(
        argv=argv,
        returncode=PLAN_TIMEOUT_RETURN_CODE,
        stdout=stdout,
        stderr=(stderr.rstrip() + "\n" + diagnostic).lstrip("\n"),
        duration_ms=getattr(error, "duration_ms", 0),
        supervisor=getattr(error, "supervisor", None),
        runtime_authority=getattr(error, "runtime_authority", None),
        physical_execution_id=getattr(error, "physical_execution_id", None),
        standard_isolation=getattr(error, "standard_isolation", None),
        stdout_evidence=getattr(error, "stdout_evidence", None),
    )


def from_command(result: CommandResult) -> StepOutcome:
    return StepOutcome(
        argv=result.argv,
        returncode=result.returncode,
        observed_returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=result.duration_ms,
        supervisor=result.supervisor,
        runtime_authority=result.runtime_authority,
        execution_authority_economics=result.execution_authority_economics,
        physical_execution_id=result.physical_execution_id,
        standard_isolation=result.standard_isolation,
        stdout_evidence=result.stdout_evidence,
    )
