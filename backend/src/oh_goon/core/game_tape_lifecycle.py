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
from oh_goon.core.models.run_events import (
    ProcessStateUnknownEvent,
    StepCancelledEvent,
    StepFailedEvent,
    StepSkippedEvent,
    StepStartedEvent,
    StepSucceededEvent,
)
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


def repository_status(
    report: OperationReport | None,
    *,
    terminal: bool,
    started: bool = False,
    lifecycle_events: tuple[object, ...] = (),
) -> GameTapeStatus:
    """Interpret durable repository operation evidence once for every Game Tape view."""

    if report is None:
        if not started:
            return GameTapeStatus.NOT_RUN if terminal else GameTapeStatus.PLANNED
        if not terminal:
            return GameTapeStatus.RUNNING
        observed = _unreported_step_status(terminal=True, started=True, events=lifecycle_events)
        # A closed Step cannot stand in for the absent whole-repository result.
        return (
            GameTapeStatus.CANCELLED
            if observed is GameTapeStatus.CANCELLED
            else GameTapeStatus.INTERRUPTED
        )
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
    started: bool = False,
    lifecycle_events: tuple[object, ...] = (),
) -> GameTapeStatus:
    """Interpret canonical result or journal facts, never parent success or timing."""

    if execution is None:
        return _unreported_step_status(terminal=terminal, started=started, events=lifecycle_events)
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


def _unreported_step_status(
    *, terminal: bool, started: bool, events: tuple[object, ...]
) -> GameTapeStatus:
    closed = [
        event
        for event in events
        if isinstance(
            event, (StepSucceededEvent, StepFailedEvent, StepSkippedEvent, StepCancelledEvent)
        )
    ]
    if closed:
        latest = max(closed, key=lambda event: event.sequence)
        if isinstance(latest, StepSucceededEvent):
            return GameTapeStatus.PASSED
        if isinstance(latest, StepFailedEvent):
            return GameTapeStatus.FAILED
        if isinstance(latest, StepSkippedEvent):
            return GameTapeStatus.SKIPPED
        return GameTapeStatus.CANCELLED
    progress = [
        event for event in events if isinstance(event, (StepStartedEvent, ProcessStateUnknownEvent))
    ]
    if progress:
        latest_progress = max(progress, key=lambda event: event.sequence)
        if isinstance(latest_progress, ProcessStateUnknownEvent):
            return GameTapeStatus.INTERRUPTED
        started = True
    if started:
        return GameTapeStatus.INTERRUPTED if terminal else GameTapeStatus.RUNNING
    return GameTapeStatus.NOT_RUN if terminal else GameTapeStatus.PLANNED
