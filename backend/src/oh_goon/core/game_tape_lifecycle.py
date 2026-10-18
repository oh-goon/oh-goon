from __future__ import annotations

from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FailedPlanStepTeardownEvidence,
    PlanStepExecution,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.run import FleetRunState, fleet_run_outcome
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunOutcome,
    FleetRunStatus,
    GameTapeStatus,
    Verdict,
)


def run_status(run: FleetRunState) -> GameTapeStatus:
    """Interpret one Fleet lifecycle fact into the shared Game Tape lifecycle."""

    if run.status is FleetRunStatus.QUEUED:
        return GameTapeStatus.ADMITTED
    if run.status.active:
        return GameTapeStatus.RUNNING
    failure = getattr(run, "failure", None)
    if failure is not None and failure.stage is FleetRunFailureStage.RECOVERY:
        return GameTapeStatus.INTERRUPTED
    outcome = fleet_run_outcome(run)
    if outcome is FleetRunOutcome.SUCCESS:
        return GameTapeStatus.PASSED
    if outcome is FleetRunOutcome.ATTENTION_REQUIRED:
        return GameTapeStatus.ATTENTION_REQUIRED
    if outcome is FleetRunOutcome.CANCELLED:
        return GameTapeStatus.CANCELLED
    if outcome is FleetRunOutcome.FAILED:
        return GameTapeStatus.FAILED
    raise AssertionError(
        f"Non-terminal Fleet Run outcome reached terminal Game Tape projection: {outcome.value}"
    )


def repository_status(report: OperationReport | None, *, terminal: bool) -> GameTapeStatus:
    """Interpret durable repository operation evidence once for every Game Tape view."""

    if report is None:
        return GameTapeStatus.NOT_RUN if terminal else GameTapeStatus.PLANNED
    verdict = report.outcome.verdict
    if verdict is Verdict.FAILED:
        return GameTapeStatus.FAILED
    if verdict is Verdict.MANUAL_ACTION_REQUIRED:
        return GameTapeStatus.ATTENTION_REQUIRED
    if verdict in {Verdict.READY, Verdict.UPDATED, Verdict.UNCHANGED}:
        return GameTapeStatus.PASSED
    raise AssertionError(
        f"Unsupported repository verdict reached Game Tape projection: {verdict.value}"
    )


def step_status(
    execution: PlanStepExecution | None,
    *,
    terminal: bool,
) -> GameTapeStatus:
    """Interpret canonical step evidence once; never infer state from logs or timing."""

    if execution is None:
        return GameTapeStatus.NOT_RUN if terminal else GameTapeStatus.PLANNED
    if isinstance(getattr(execution, "teardown", None), FailedPlanStepTeardownEvidence):
        return GameTapeStatus.FAILED
    if isinstance(execution, CompletedPlanStepExecution):
        return GameTapeStatus.PASSED
    if isinstance(execution, FailedPlanStepExecution):
        return GameTapeStatus.FAILED
    if isinstance(execution, SkippedPlanStepExecution):
        return GameTapeStatus.SKIPPED
    raise AssertionError(
        f"Unsupported step execution reached Game Tape projection: {type(execution).__name__}"
    )
