from __future__ import annotations

import subprocess

from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    ResolvedLocalSearchAgentStep,
    ResolvedPlanStep,
)
from oh_goon.engine.command import CommandOutputLimitExceeded
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, timeout_outcome


def bounded_execution_failure_outcome(
    step: ResolvedPlanStep,
    error: CommandOutputLimitExceeded | subprocess.TimeoutExpired,
) -> StepOutcome:
    if isinstance(error, CommandOutputLimitExceeded):
        return _output_limit_outcome(step, error)
    return timeout_outcome(step, error)


def _output_limit_outcome(step: ResolvedPlanStep, error: CommandOutputLimitExceeded) -> StepOutcome:
    message = (
        f"Fleet plan step {step.step_id} {error.stream} exceeded the "
        f"{error.limit_bytes}-byte capture limit; output is bounded to protect executor memory"
    )
    stderr = (error.stderr + "\n" + message).strip()
    native = isinstance(step, ResolvedLocalSearchAgentStep) and step.native_agent is not None
    argv = error.argv
    if not argv and not native:
        argv = tuple(getattr(step, "argv", ())) or (step.kind, step.step_id)
    return StepOutcome(
        argv=argv,
        returncode=PLAN_CONTRACT_RETURN_CODE,
        stdout=error.stdout,
        stderr=stderr,
        supervisor=error.supervisor,
        runtime_authority=error.runtime_authority,
    )
