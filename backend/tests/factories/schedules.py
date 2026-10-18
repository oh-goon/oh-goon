from __future__ import annotations

from datetime import datetime

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.run_admission import ExplicitRepositoryScope
from oh_goon.core.models.schedule import ScheduleCreate, ScheduleDefinition, ScheduleFailure
from oh_goon.core.models.schedule_execution import (
    AttentionRequiredScheduleRun,
    CancelledScheduleRun,
    CompletedScheduleRun,
    FailedScheduleRun,
    RunningScheduleRun,
    ScheduleAttention,
    ScheduledFleetRunIntent,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    OperationAction,
    ScheduleExecutionStatus,
    ScheduleRepositoryRunStatus,
    ScheduleState,
)
from tests.factories.fleet_plan_targets import structural_execution_identity


def scheduled_plan(name: str = "scheduled") -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name=name,
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=60,
                step_id="check",
                argv=("python", "-c", "print('ok')"),
            ),
        ),
    )


def schedule_create(
    *,
    name: str = "Schedule",
    repository_ids: tuple[str, ...] = ("repo-a",),
    interval_minutes: int = 60,
    state: ScheduleState = ScheduleState.ACTIVE,
) -> ScheduleCreate:
    plan = scheduled_plan(name)
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(plan),
    )
    return ScheduleCreate(
        name=name,
        repository_ids=tuple(sorted(repository_ids)),
        repository_instance_ids={item: f"instance-{item}" for item in repository_ids},
        target=target,
        interval_minutes=interval_minutes,
        state=state,
    )


def schedule_definition(
    *,
    now: datetime,
    schedule_id: str = "schedule-test",
    generation_id: str = "schedule-gen-test",
    name: str = "Schedule",
    repository_ids: tuple[str, ...] = ("repo-a",),
    interval_minutes: int = 60,
    state: ScheduleState = ScheduleState.ACTIVE,
) -> ScheduleDefinition:
    request = schedule_create(
        name=name,
        repository_ids=tuple(sorted(repository_ids)),
        interval_minutes=interval_minutes,
        state=state,
    )
    return ScheduleDefinition(
        schedule_id=schedule_id,
        generation_id=generation_id,
        record_version=0,
        created_at=now,
        updated_at=now,
        **request.model_dump(),
    )


def scheduled_fleet_run_intent(definition: ScheduleDefinition) -> ScheduledFleetRunIntent:
    target = definition.target
    capability = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target).definition
    return ScheduledFleetRunIntent(
        definition_generation_id=definition.generation_id,
        definition_record_version=definition.record_version,
        capability=capability.durable_snapshot(),
        target=target,
        repository_scope=ExplicitRepositoryScope(repository_ids=definition.repository_ids),
        repository_instance_ids=definition.repository_instance_ids,
    )


def schedule_run_record(
    run_id: str,
    status: ScheduleExecutionStatus,
    *,
    definition: ScheduleDefinition | None = None,
    intent: ScheduledFleetRunIntent | None = None,
    attention: tuple[ScheduleAttention, ...] = (),
    failure: ScheduleFailure | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
):
    definition = definition or schedule_definition(now=started_at or utc_now())
    intent = intent or scheduled_fleet_run_intent(definition)
    started_at = started_at or utc_now()
    from oh_goon.core.models.schedule import ScheduleOccurrenceRepositoryRun
    from oh_goon.core.product_identity import schedule_repository_fleet_run_id

    row_status = {
        ScheduleExecutionStatus.RUNNING: ScheduleRepositoryRunStatus.PENDING,
        ScheduleExecutionStatus.COMPLETED: ScheduleRepositoryRunStatus.SUCCESSFUL,
        ScheduleExecutionStatus.ATTENTION_REQUIRED: ScheduleRepositoryRunStatus.ATTENTION_REQUIRED,
        ScheduleExecutionStatus.FAILED: ScheduleRepositoryRunStatus.NOT_STARTED,
        ScheduleExecutionStatus.CANCELLED: ScheduleRepositoryRunStatus.CANCELLED,
    }[status]
    rows = tuple(
        ScheduleOccurrenceRepositoryRun(
            repository_id=repository_id,
            status=row_status,
            fleet_run_id=None
            if row_status
            in {ScheduleRepositoryRunStatus.PENDING, ScheduleRepositoryRunStatus.NOT_STARTED}
            else schedule_repository_fleet_run_id(run_id, repository_id),
            attention_count=1
            if row_status is ScheduleRepositoryRunStatus.ATTENTION_REQUIRED
            else 0,
            failure=failure if row_status is ScheduleRepositoryRunStatus.NOT_STARTED else None,
        )
        for repository_id in intent.repository_scope.repository_ids
    )
    common = dict(
        schedule_run_id=run_id,
        schedule_id=definition.schedule_id,
        intent=intent,
        repository_runs=rows,
        attention=attention,
        started_at=started_at,
    )
    if status is ScheduleExecutionStatus.RUNNING:
        return RunningScheduleRun(status=status, **common)
    terminal = finished_at or started_at
    if status is ScheduleExecutionStatus.COMPLETED:
        return CompletedScheduleRun(status=status, **common, finished_at=terminal)
    if status is ScheduleExecutionStatus.ATTENTION_REQUIRED:
        return AttentionRequiredScheduleRun(status=status, **common, finished_at=terminal)
    if status is ScheduleExecutionStatus.FAILED:
        if failure is None:
            raise ValueError("FAILED Schedule test execution requires failure")
        return FailedScheduleRun(status=status, **common, failure=failure, finished_at=terminal)
    if status is ScheduleExecutionStatus.CANCELLED:
        return CancelledScheduleRun(status=status, **common, finished_at=terminal)
    raise AssertionError(f"Unsupported Schedule status: {status}")


def persist_active_schedule(database, executions, definition: ScheduleDefinition):
    from uuid import uuid4

    from oh_goon.core.schedule_transitions import begin_schedule_run

    execution = begin_schedule_run(
        schedule_run_id=f"schedule-run-{uuid4().hex}",
        schedule_id=definition.schedule_id,
        intent=scheduled_fleet_run_intent(definition),
        started_at=utc_now(),
    )
    return executions.insert(
        execution,
        expected_durable_revision=database.durable_revision(),
    )
