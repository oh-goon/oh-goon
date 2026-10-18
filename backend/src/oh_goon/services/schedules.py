from __future__ import annotations

import sqlite3

from oh_goon.core.errors import (
    InvalidRequest,
    RuntimeSafetyBlocked,
    RuntimeSafetyStale,
    ScheduleExecutionActive,
    ScheduleNotFound,
    StateConflict,
)
from oh_goon.core.models.schedule import (
    ScheduleCatalog,
    ScheduleCreate,
    ScheduleDefinition,
    ScheduleDeleteRequest,
    ScheduleOccurrence,
    ScheduleOccurrenceRepositoryPage,
    ScheduleReplace,
    ScheduleStatus,
)
from oh_goon.core.models.schedule_execution import FailedScheduleRun, ScheduleRunRecord
from oh_goon.core.product_identity import new_schedule_run_id
from oh_goon.core.vocabulary import (
    ScheduleOccurrenceFilter,
    ScheduleRepositoryRunStatus,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.schedule_admission import ScheduleAdmissionPlanner
from oh_goon.services.schedule_execution import ScheduleExecutionEngine
from oh_goon.services.schedule_timing import schedule_timing_decision


class ScheduleService:
    def __init__(
        self,
        database: LocalDatabase,
        definitions: ScheduleDefinitionStore,
        executions: ScheduleExecutionStore,
        engine: ScheduleExecutionEngine,
        admission: ScheduleAdmissionPlanner,
        runtime_safety: RuntimeSafetyService,
    ) -> None:
        self._database = database
        self._definitions = definitions
        self._executions = executions
        self._engine = engine
        self._admission = admission
        self._runtime_safety = runtime_safety

    def catalog(self) -> ScheduleCatalog:
        with self._database.transaction() as c:
            statuses = [self._status_in(c, d) for d in self._definitions.definitions_in(c)]
        return ScheduleCatalog(schedules=tuple(statuses))

    def definition(self, schedule_id: str) -> ScheduleDefinition:
        return self._definitions.get(schedule_id)

    def create_prepared(self, schedule_id: str, request: ScheduleCreate) -> ScheduleDefinition:
        """Persist a Schedule whose execution target was prepared by an execution-entry owner."""
        return self._definitions.create(schedule_id, request)

    def replace_preserving_authority(
        self, schedule_id: str, request: ScheduleReplace
    ) -> ScheduleDefinition:
        """Update Schedule metadata/timing without accepting a new execution authority."""
        current = self.definition(schedule_id)
        if (
            request.repository_ids != current.repository_ids
            or request.repository_instance_ids != current.repository_instance_ids
            or request.target != current.target
        ):
            raise InvalidRequest(
                "Schedule updates cannot replace repository instance or Plan target authority; "
                "use FleetPlanService to resolve and freeze a new execution target"
            )
        return self._definitions.replace(schedule_id, request)

    def replace_prepared(self, schedule_id: str, request: ScheduleReplace) -> ScheduleDefinition:
        """Persist a replacement whose target was prepared by FleetPlanService."""
        return self._definitions.replace(schedule_id, request)

    def delete(self, schedule_id: str, request: ScheduleDeleteRequest) -> None:
        self._definitions.delete(schedule_id, request)

    def start(
        self,
        schedule_id: str,
        schedule_run_id: str,
        *,
        expected_generation_id: str,
        expected_record_version: int,
    ) -> ScheduleOccurrence:
        existing = self._executions.find(schedule_run_id)
        if existing is not None:
            return _public_occurrence(
                _require_matching_schedule_authority(
                    existing,
                    schedule_id,
                    expected_generation_id=expected_generation_id,
                    expected_record_version=expected_record_version,
                )
            )
        plan = self._admission.plan(schedule_id)
        _require_expected_schedule_authority(
            plan.intent.definition_generation_id,
            plan.intent.definition_record_version,
            expected_generation_id=expected_generation_id,
            expected_record_version=expected_record_version,
        )
        try:
            return _public_occurrence(self._engine.start(plan, schedule_run_id=schedule_run_id))
        except ScheduleExecutionActive:
            existing = self._executions.find(schedule_run_id)
            if existing is not None:
                return _public_occurrence(
                    _require_matching_schedule_authority(
                        existing,
                        schedule_id,
                        expected_generation_id=expected_generation_id,
                        expected_record_version=expected_record_version,
                    )
                )
            raise

    def occurrence_repositories(
        self,
        schedule_id: str,
        schedule_run_id: str,
        *,
        status: ScheduleOccurrenceFilter = ScheduleOccurrenceFilter.ALL,
        offset: int = 0,
        limit: int = 50,
    ) -> ScheduleOccurrenceRepositoryPage:
        if offset < 0:
            raise InvalidRequest("Schedule occurrence offset must be zero or greater")
        if limit < 1 or limit > 100:
            raise InvalidRequest("Schedule occurrence limit must be between 1 and 100")
        execution = _require_matching_schedule_identity(
            self._executions.get(schedule_run_id), schedule_id
        )
        rows = execution.repository_runs
        if status is not ScheduleOccurrenceFilter.ALL:
            rows = tuple(item for item in rows if item.status.value == status.value)
        return ScheduleOccurrenceRepositoryPage(
            schedule_run_id=schedule_run_id,
            schedule_id=schedule_id,
            total=len(rows),
            offset=offset,
            limit=limit,
            items=rows[offset : offset + limit],
        )

    def tick(self) -> None:
        allowed = self._runtime_safety.effects_allowed()
        self._engine.advance_all(allow_effect_admission=allowed)
        if not allowed:
            return
        for candidate in self._definitions.definitions():
            try:
                scheduled = self._admission.plan_scheduled(candidate.schedule_id)
            except (ScheduleNotFound, RuntimeSafetyBlocked, RuntimeSafetyStale):
                continue
            if scheduled is None:
                continue
            plan, _ = scheduled
            try:
                self._engine.start(plan, schedule_run_id=new_schedule_run_id())
            except (ScheduleExecutionActive, RuntimeSafetyBlocked, RuntimeSafetyStale):
                continue

    def _status_in(self, c: sqlite3.Connection, definition: ScheduleDefinition) -> ScheduleStatus:
        executions = self._executions.executions_in(c, definition.schedule_id, limit=1)
        latest = executions[0] if executions else None
        decision = schedule_timing_decision(definition, latest)
        active = latest is not None and not latest.status.terminal
        return ScheduleStatus(
            definition=definition,
            latest_occurrence=_public_occurrence(latest) if latest else None,
            next_run_at=decision.due_at,
            missing_expected_occurrence_at=decision.due_at if decision.due and not active else None,
        )


def _public_occurrence(execution: ScheduleRunRecord) -> ScheduleOccurrence:
    counts = {state: 0 for state in ScheduleRepositoryRunStatus}
    for item in execution.repository_runs:
        counts[item.status] += 1
    return ScheduleOccurrence(
        schedule_run_id=execution.schedule_run_id,
        schedule_id=execution.schedule_id,
        expected=len(execution.repository_runs),
        successful=counts[ScheduleRepositoryRunStatus.SUCCESSFUL],
        failed=counts[ScheduleRepositoryRunStatus.FAILED],
        attention_required=counts[ScheduleRepositoryRunStatus.ATTENTION_REQUIRED],
        cancelled=counts[ScheduleRepositoryRunStatus.CANCELLED],
        running=counts[ScheduleRepositoryRunStatus.RUNNING],
        pending=counts[ScheduleRepositoryRunStatus.PENDING],
        not_started=counts[ScheduleRepositoryRunStatus.NOT_STARTED],
        status=execution.status,
        started_at=execution.started_at,
        finished_at=getattr(execution, "finished_at", None),
        attention_count=execution.attention_count,
        failure=execution.failure if isinstance(execution, FailedScheduleRun) else None,
    )


def _require_matching_schedule_identity(
    execution: ScheduleRunRecord, schedule_id: str
) -> ScheduleRunRecord:
    if execution.schedule_id != schedule_id:
        raise StateConflict(
            f"Schedule run identity {execution.schedule_run_id} is already bound to another Schedule"
        )
    return execution


def _require_matching_schedule_authority(
    execution: ScheduleRunRecord,
    schedule_id: str,
    *,
    expected_generation_id: str,
    expected_record_version: int,
) -> ScheduleRunRecord:
    _require_matching_schedule_identity(execution, schedule_id)
    _require_expected_schedule_authority(
        execution.intent.definition_generation_id,
        execution.intent.definition_record_version,
        expected_generation_id=expected_generation_id,
        expected_record_version=expected_record_version,
    )
    return execution


def _require_expected_schedule_authority(
    actual_generation_id: str,
    actual_record_version: int,
    *,
    expected_generation_id: str,
    expected_record_version: int,
) -> None:
    if (actual_generation_id, actual_record_version) != (
        expected_generation_id,
        expected_record_version,
    ):
        raise StateConflict("Schedule definition changed since this occurrence command was created")
