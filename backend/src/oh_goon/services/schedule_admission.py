from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import (
    CapabilityUnavailable,
    InvalidRequest,
    RuntimeSafetyStale,
    ScheduleDefinitionStale,
    StateConflict,
)
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
    writable_schedule_advance_id,
    writable_schedule_run_id,
)
from oh_goon.core.vocabulary import OperationAction, ScheduleRepositoryRunStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.schedule_task_documents import ApprovedTaskDocumentError, read_approved_tasks
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
        writable = None
        document = None
        index = None
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
            if policy is not None:
                index = next_writable_task_index(latest, definition.generation_id)
                if index is None:
                    return None
                task = self._definitions.writable_task_in(
                    connection, definition.generation_id, policy, index
                )
                if task is None:
                    document = self._definitions.approved_task_document_in(
                        connection, definition.generation_id
                    )
                    if document is None:
                        return None
                else:
                    writable = ScheduledWritableRound(
                        task_index=index,
                        task=task,
                        advance_id=(
                            policy.seed_advance_id
                            if index == 0
                            else writable_schedule_advance_id(
                                writable_schedule_run_id(definition.generation_id, index)
                            )
                        ),
                        predecessor_run_id=latest.repository_runs[0].fleet_run_id
                        if latest
                        else None,
                        native_selection_id=policy.native_selection_id,
                    )
        admitted = self._runtime_safety.admit_cut(cut)
        if document is not None:
            assert index is not None
            tasks = read_approved_tasks(document)
            if tasks:
                try:
                    self._definitions.append_approved_tasks(
                        definition.generation_id,
                        expected_task_index=index,
                        expected_durable_revision=admitted.durable_revision,
                        tasks=tasks,
                    )
                except (RuntimeSafetyStale, ScheduleDefinitionStale):
                    raise
                except StateConflict as exc:
                    raise ApprovedTaskDocumentError(str(exc)) from None
            # The refill changes durable authority. The next ordinary tick admits its task.
            return None
        return _admission_plan(definition, admitted.durable_revision, writable), decision


def next_writable_task_index(previous: ScheduleRunRecord | None, generation_id: str) -> int | None:
    """Select the next ordinal only after the original predecessor succeeded."""
    if previous is None:
        return 0
    last_round = previous.intent.writable_round
    if (
        last_round is None
        or previous.intent.definition_generation_id != generation_id
        or not isinstance(previous, CompletedScheduleRun)
        or len(previous.repository_runs) != 1
        or previous.repository_runs[0].status is not ScheduleRepositoryRunStatus.SUCCESSFUL
    ):
        return None
    assert previous.repository_runs[0].fleet_run_id is not None
    return last_round.task_index + 1


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
