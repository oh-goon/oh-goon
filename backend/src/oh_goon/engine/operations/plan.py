from __future__ import annotations

from typing import Literal, cast

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    DependencyPlanCompleted,
    DependencyPlanFailed,
    FailedOperationOutcome,
    OperationExecution,
    OperationReport,
    PlanOperationEvidence,
    SuccessfulOperationOutcome,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    Verdict,
    WorktreeState,
)
from oh_goon.engine.operation_execution_context import OperationContext
from oh_goon.engine.operations.command_execution import run_operation_command
from oh_goon.engine.project import policy_snapshot
from oh_goon.engine.reporting import execution, provenance


def run(context: OperationContext) -> OperationExecution:
    started = utc_now()
    before_observation = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    )
    before = before_observation.fingerprint
    policy = policy_snapshot(context.attempt.repository.path)
    argv = ["uv", "lock", "--upgrade", "--dry-run"]
    result = run_operation_command(
        context.attempt.repository.path,
        *argv,
        timeout=context.timeout_seconds,
        cancel_requested=context.cancel_requested,
    )
    after_observation = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    )
    clean = after_observation.worktree_state is WorktreeState.CLEAN
    success = result.returncode == 0 and clean
    reason = (
        ReasonCode.READY
        if success
        else ReasonCode.UNEXPECTED_FILE_CHANGES
        if not clean
        else ReasonCode.OPERATION_FAILED
    )
    verdict = Verdict.READY if success else Verdict.FAILED
    returncode = result.returncode if result.returncode != 0 else (0 if clean else 1)
    report = OperationReport(
        capability=context.attempt.capability,
        target=context.attempt.target,
        provenance=provenance(
            context,
            started,
            before,
            after_observation.fingerprint,
            policy,
        ),
        repository=context.attempt.repository,
        outcome=(
            SuccessfulOperationOutcome(
                state="successful",
                verdict=cast(Literal[Verdict.READY, Verdict.UPDATED, Verdict.UNCHANGED], verdict),
                reason_code=reason,
            )
            if success
            else FailedOperationOutcome(state="failed", returncode=returncode, reason_code=reason)
        ),
        evidence=PlanOperationEvidence(
            kind="PLAN",
            policy=policy,
            dependencies=(
                DependencyPlanCompleted(state="plan_completed")
                if success
                else DependencyPlanFailed(state="plan_failed")
            ),
        ),
    )
    return execution(result.stdout, result.stderr, report)
