from __future__ import annotations

from datetime import datetime
from typing import TypedDict, cast

from oh_goon.core.models.run import FailedFleetRun, FleetRunDetail, fleet_run_outcome
from oh_goon.core.models.schedule import ScheduleFailure, ScheduleOccurrenceRepositoryRun
from oh_goon.core.models.schedule_execution import (
    AttentionRequiredScheduleRun,
    CancelledScheduleRun,
    CompletedScheduleRun,
    FailedScheduleRun,
    RunningScheduleRun,
    ScheduleAttention,
    ScheduledFleetRunIntent,
    ScheduleRunRecord,
    pending_repository_runs,
)
from oh_goon.core.vocabulary import (
    FleetRunOutcome,
    ScheduleExecutionStatus,
    ScheduleFailureStage,
    ScheduleRepositoryRunStatus,
)


class _ScheduleRunFields(TypedDict):
    schedule_run_id: str
    schedule_id: str
    intent: ScheduledFleetRunIntent
    repository_runs: tuple[ScheduleOccurrenceRepositoryRun, ...]
    record_version: int
    attention: tuple[ScheduleAttention, ...]
    started_at: datetime


def begin_schedule_run(
    *, schedule_run_id: str, schedule_id: str, intent: ScheduledFleetRunIntent, started_at: datetime
) -> RunningScheduleRun:
    return RunningScheduleRun(
        status=ScheduleExecutionStatus.RUNNING,
        schedule_run_id=schedule_run_id,
        schedule_id=schedule_id,
        intent=intent,
        repository_runs=pending_repository_runs(intent),
        record_version=0,
        started_at=started_at,
    )


def mark_repository_running(
    execution: RunningScheduleRun, repository_id: str, fleet_run_id: str
) -> RunningScheduleRun:
    return _running_with(
        execution,
        _replace(
            execution.repository_runs,
            repository_id,
            ScheduleOccurrenceRepositoryRun(
                repository_id=repository_id,
                status=ScheduleRepositoryRunStatus.RUNNING,
                fleet_run_id=fleet_run_id,
            ),
        ),
    )


def mark_repository_not_started(
    execution: RunningScheduleRun, repository_id: str, failure: ScheduleFailure
) -> ScheduleRunRecord:
    updated = _replace(
        execution.repository_runs,
        repository_id,
        ScheduleOccurrenceRepositoryRun(
            repository_id=repository_id,
            status=ScheduleRepositoryRunStatus.NOT_STARTED,
            failure=failure,
        ),
    )
    return settle_if_complete(_running_with(execution, updated), finished_at=None)


def observe_repository_run(
    execution: RunningScheduleRun,
    repository_id: str,
    detail: FleetRunDetail,
    *,
    observed_at: datetime,
) -> ScheduleRunRecord:
    outcome = fleet_run_outcome(detail.run)
    attention = _attention_from_run(detail)
    failure = None
    if outcome is FleetRunOutcome.CANCELLED:
        status = ScheduleRepositoryRunStatus.CANCELLED
    elif outcome is FleetRunOutcome.FAILED:
        status = ScheduleRepositoryRunStatus.FAILED
        if not isinstance(detail.run, FailedFleetRun):
            raise ValueError("FAILED FleetRun outcome requires structural failure evidence")
        item = detail.run.failure
        failure = ScheduleFailure(
            code=item.code,
            kind=item.kind,
            retryable=item.retryable,
            stage=ScheduleFailureStage.FLEET_RUN,
            message=item.message,
        )
    elif outcome is FleetRunOutcome.ATTENTION_REQUIRED:
        status = ScheduleRepositoryRunStatus.ATTENTION_REQUIRED
    elif outcome is FleetRunOutcome.SUCCESS:
        status = ScheduleRepositoryRunStatus.SUCCESSFUL
    else:
        raise ValueError("Only terminal Fleet Runs may settle Schedule repository execution")
    current = next(
        item for item in execution.repository_runs if item.repository_id == repository_id
    )
    row = ScheduleOccurrenceRepositoryRun(
        repository_id=repository_id,
        status=status,
        fleet_run_id=current.fleet_run_id,
        attention_count=len(attention),
        failure=failure,
    )
    combined = {item.identity: item for item in (*execution.attention, *attention)}
    updated = _running_with(
        execution,
        _replace(execution.repository_runs, repository_id, row),
        attention=tuple(
            combined[key] for key in sorted(combined, key=lambda x: (x[0], x[1].value))
        ),
    )
    return settle_if_complete(updated, finished_at=observed_at)


def fail_schedule_run(
    execution: RunningScheduleRun, failure: ScheduleFailure, *, finished_at: datetime
) -> FailedScheduleRun:
    # Infrastructure failure: remaining targets get explicit not-started evidence.
    rows = tuple(
        ScheduleOccurrenceRepositoryRun(
            repository_id=item.repository_id,
            status=ScheduleRepositoryRunStatus.NOT_STARTED,
            failure=failure,
        )
        if not item.status.terminal
        else item
        for item in execution.repository_runs
    )
    return FailedScheduleRun(
        status=ScheduleExecutionStatus.FAILED,
        **_shared(execution, repository_runs=rows),
        failure=failure,
        finished_at=finished_at,
    )


def settle_if_complete(
    execution: RunningScheduleRun, *, finished_at: datetime | None
) -> ScheduleRunRecord:
    if any(not item.status.terminal for item in execution.repository_runs):
        return execution
    if finished_at is None:
        from oh_goon.core.time import utc_now

        finished_at = utc_now()
    statuses = {item.status for item in execution.repository_runs}
    common = _shared(execution)
    if (
        ScheduleRepositoryRunStatus.FAILED in statuses
        or ScheduleRepositoryRunStatus.NOT_STARTED in statuses
    ):
        first = next(
            (item.failure for item in execution.repository_runs if item.failure is not None), None
        )
        return FailedScheduleRun(
            status=ScheduleExecutionStatus.FAILED,
            **common,
            # Valid terminal failed/not-started rows require failure evidence;
            # retain model validation as the owner of malformed-row rejection.
            failure=cast(ScheduleFailure, first),
            finished_at=finished_at,
        )
    if ScheduleRepositoryRunStatus.ATTENTION_REQUIRED in statuses:
        return AttentionRequiredScheduleRun(
            status=ScheduleExecutionStatus.ATTENTION_REQUIRED, **common, finished_at=finished_at
        )
    if ScheduleRepositoryRunStatus.CANCELLED in statuses:
        return CancelledScheduleRun(
            status=ScheduleExecutionStatus.CANCELLED, **common, finished_at=finished_at
        )
    return CompletedScheduleRun(
        status=ScheduleExecutionStatus.COMPLETED, **common, finished_at=finished_at
    )


def _running_with(
    execution: RunningScheduleRun,
    rows: tuple[ScheduleOccurrenceRepositoryRun, ...],
    *,
    attention: tuple[ScheduleAttention, ...] | None = None,
) -> RunningScheduleRun:
    return RunningScheduleRun(
        status=ScheduleExecutionStatus.RUNNING,
        **_shared(execution, repository_runs=rows, attention=attention),
    )


def _shared(
    execution: ScheduleRunRecord,
    *,
    repository_runs: tuple[ScheduleOccurrenceRepositoryRun, ...] | None = None,
    attention: tuple[ScheduleAttention, ...] | None = None,
) -> _ScheduleRunFields:
    return {
        "schedule_run_id": execution.schedule_run_id,
        "schedule_id": execution.schedule_id,
        "intent": execution.intent,
        "repository_runs": execution.repository_runs
        if repository_runs is None
        else repository_runs,
        "record_version": execution.record_version,
        "attention": execution.attention if attention is None else attention,
        "started_at": execution.started_at,
    }


def _replace(
    rows: tuple[ScheduleOccurrenceRepositoryRun, ...],
    repository_id: str,
    row: ScheduleOccurrenceRepositoryRun,
) -> tuple[ScheduleOccurrenceRepositoryRun, ...]:
    return tuple(row if item.repository_id == repository_id else item for item in rows)


def _attention_from_run(detail: FleetRunDetail) -> tuple[ScheduleAttention, ...]:
    items = {
        (report.repository.repository_id, report.outcome.reason_code): ScheduleAttention(
            repository_id=report.repository.repository_id, reason_code=report.outcome.reason_code
        )
        for report in detail.operations
        if report.outcome.verdict.needs_attention
    }
    return tuple(items[key] for key in sorted(items, key=lambda x: (x[0], x[1].value)))
