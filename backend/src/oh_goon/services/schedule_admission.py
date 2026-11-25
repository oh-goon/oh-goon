from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import CapabilityUnavailable, InvalidRequest
from oh_goon.core.models.run_admission import (
    ExplicitRepositoryScope,
    requested_fleet_repository_scope,
)
from oh_goon.core.models.schedule import ScheduleDefinition
from oh_goon.core.models.schedule_execution import (
    CompletedScheduleRun,
    ScheduledFleetRunIntent,
    ScheduleRunRecord,
)
from oh_goon.core.models.writable_schedule import (
    ScheduledWritableRound,
    WritableSchedulePolicy,
    writable_schedule_advance_id,
    writable_schedule_run_id,
)
from oh_goon.core.vocabulary import OperationAction, ScheduleRepositoryRunStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.schedule_timing import ScheduleTimingDecision, schedule_timing_decision


@dataclass(frozen=True, slots=True)
class ScheduleAdmissionPlan:
    durable_revision: int
    schedule_id: str
    intent: ScheduledFleetRunIntent


class ScheduleAdmissionPlanner:
    def __init__(
        self,
        database: LocalDatabase,
        definitions: ScheduleDefinitionStore,
        executions: ScheduleExecutionStore,
        auditor: SystemInvariantAuditor,
        runtime_safety: RuntimeSafetyService,
    ) -> None:
        self._database = database
        self._definitions = definitions
        self._executions = executions
        self._auditor = auditor
        self._runtime_safety = runtime_safety

    def plan(self, schedule_id: str) -> ScheduleAdmissionPlan:
        with self._database.transaction() as connection:
            cut = self._auditor.read_cut_in(connection)
            definition = self._definitions.get_in(connection, schedule_id)
            if (
                self._definitions.writable_policy_in(connection, definition.generation_id)
                is not None
            ):
                raise InvalidRequest("Writable rounds use the selected Schedule interval")
        admitted = self._runtime_safety.admit_cut(cut)
        return _admission_plan(definition, admitted.durable_revision)

    def plan_scheduled(
        self, schedule_id: str
    ) -> tuple[ScheduleAdmissionPlan, ScheduleTimingDecision] | None:
        with self._database.transaction() as connection:
            cut = self._auditor.read_cut_in(connection)
            definition = self._definitions.get_in(connection, schedule_id)
            policy = self._definitions.writable_policy_in(connection, definition.generation_id)
            latest = (
                self._executions.latest_for_schedule_in(connection, schedule_id)
                if policy is None
                else self._executions.latest_writable_for_generation_in(
                    connection, schedule_id, definition.generation_id
                )
            )
            decision = schedule_timing_decision(definition, latest)
        if not decision.due:
            return None
        writable = None
        if policy is not None:
            writable = next_writable_round(policy, latest, definition.generation_id)
            if writable is None:
                return None
        admitted = self._runtime_safety.admit_cut(cut)
        return _admission_plan(definition, admitted.durable_revision, writable), decision


def next_writable_round(
    policy: WritableSchedulePolicy, previous: ScheduleRunRecord | None, generation_id: str
) -> ScheduledWritableRound | None:
    """Select exactly one approved task from the original Schedule history."""
    index = 0
    predecessor = None
    advance_id = policy.seed_advance_id
    if previous is not None:
        last_round = previous.intent.writable_round
        if (
            last_round is None
            or previous.intent.definition_generation_id != generation_id
            or not isinstance(previous, CompletedScheduleRun)
            or len(previous.repository_runs) != 1
            or previous.repository_runs[0].status is not ScheduleRepositoryRunStatus.SUCCESSFUL
        ):
            return None
        predecessor = previous.repository_runs[0].fleet_run_id
        assert predecessor is not None
        index = last_round.task_index + 1
        advance_id = writable_schedule_advance_id(writable_schedule_run_id(generation_id, index))
    if index >= len(policy.tasks):
        return None
    return ScheduledWritableRound(
        task_index=index,
        task=policy.tasks[index],
        advance_id=advance_id,
        predecessor_run_id=predecessor,
        native_selection_id=policy.native_selection_id,
    )


def _admission_plan(
    definition: ScheduleDefinition,
    durable_revision: int,
    writable: ScheduledWritableRound | None = None,
) -> ScheduleAdmissionPlan:
    target = definition.target
    capability = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target).definition
    if not capability.fleet_supported:
        raise CapabilityUnavailable(
            f"Schedule capability is not Fleet-supported: {capability.capability_id}"
        )
    if not capability.schedule_supported:
        raise CapabilityUnavailable(
            f"Capability does not support scheduled execution: {capability.capability_id}"
        )
    scope = requested_fleet_repository_scope(
        target, repository_ids=set(definition.repository_ids), all_repositories=False
    )
    if not isinstance(scope, ExplicitRepositoryScope) or not scope.repository_ids:
        raise AssertionError("Schedule admission must resolve at least one repository")
    return ScheduleAdmissionPlan(
        durable_revision=durable_revision,
        schedule_id=definition.schedule_id,
        intent=ScheduledFleetRunIntent(
            definition_generation_id=definition.generation_id,
            definition_record_version=definition.record_version,
            capability=capability.durable_snapshot(),
            target=target,
            repository_scope=scope,
            repository_instance_ids=definition.repository_instance_ids,
            writable_round=writable,
        ),
    )
