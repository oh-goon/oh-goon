from __future__ import annotations

import json
import sqlite3

from oh_goon.core.errors import ScheduleExecutionActive, ScheduleExecutionStale
from oh_goon.core.models.branch_advance import branch_round_run_id, writable_round_request_sha256
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.fleet_plan import ResolvedAgentStep, ResolvedNativeAgentWorkload
from oh_goon.core.models.schedule_execution import (
    CompletedScheduleRun,
    FailedScheduleRun,
    RunningScheduleRun,
    ScheduledFleetRunIntent,
    ScheduleRunRecord,
    parse_schedule_run,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.writable_schedule import (
    ScheduledWritableRound,
    writable_native_selection_id,
    writable_schedule_run_id,
)
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.vocabulary import (
    ScheduleExecutionStatus,
    ScheduleRepositoryRunStatus,
    ScheduleState,
)
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import require_durable_revision
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore


class ScheduleExecutionStore:
    """SQLite owner for occurrence target observations and record-version CAS."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def executions(self, schedule_id: str, *, limit: int = 20) -> list[ScheduleRunRecord]:
        with self._database.transaction() as c:
            return self.executions_in(c, schedule_id, limit=limit)

    def executions_in(
        self, c: sqlite3.Connection, schedule_id: str, *, limit: int = 20
    ) -> list[ScheduleRunRecord]:
        return [
            _execution(r)
            for r in c.execute(
                "SELECT * FROM schedule_executions WHERE schedule_id=? ORDER BY started_at DESC LIMIT ?",
                (schedule_id, limit),
            ).fetchall()
        ]

    def get(self, schedule_run_id: str) -> ScheduleRunRecord:
        value = self.find(schedule_run_id)
        if value is None:
            raise ScheduleExecutionStale(f"Schedule execution no longer exists: {schedule_run_id}")
        return value

    def find(self, schedule_run_id: str) -> ScheduleRunRecord | None:
        with self._database.transaction() as c:
            return _find_in(c, schedule_run_id)

    def require_pending_writable_round(
        self,
        schedule_run_id: str,
        *,
        repository_id: str,
        advance_id: str,
        task_sha256: str,
        native_selection_id: str,
    ) -> ScheduledWritableRound:
        """Prove the selected task and branch advance before any external preparation."""
        with self._database.transaction() as connection:
            current = _require_pending_in(connection, schedule_run_id, repository_id)
            selected = _require_writable_selection_in(connection, current)
        if (
            selected.advance_id != advance_id
            or selected.task.prompt_sha256 != task_sha256
            or selected.native_selection_id != native_selection_id
        ):
            raise ScheduleExecutionStale("Writable work differs from its frozen Schedule selection")
        return selected

    def require_pending_child_binding(
        self,
        *,
        schedule_run_id: str,
        repository_id: str,
        fleet_run_id: str,
        capability: CapabilitySnapshot,
        target: FleetPlanTarget,
        repository_instance_ids: dict[str, str] | None,
    ) -> None:
        """Prove the Schedule owner actually queued this exact child before Fleet planning.

        The caller must perform this read before work and once more after capturing
        FleetAdmissionPlanner's durable revision. That revision is then CAS-checked
        by the original lease/strict-placement transaction: no separate scheduler
        reservation or duplicate execution-policy authority is introduced here.
        """
        with self._database.transaction() as connection:
            current = _require_pending_in(connection, schedule_run_id, repository_id)
            actual = _bound_child_instances_in(
                connection,
                current=current,
                repository_id=repository_id,
                fleet_run_id=fleet_run_id,
                capability=capability,
                target=target,
            )
        if repository_instance_ids != actual:
            raise ScheduleExecutionStale(
                "Scheduled Fleet child source instance differs from its authority"
            )

    def bind_admitted_child_in(
        self,
        connection: sqlite3.Connection,
        *,
        schedule_run_id: str,
        repository_id: str,
        fleet_run_id: str,
        capability: CapabilitySnapshot,
        target: FleetPlanTarget,
    ) -> None:
        """Consume a queued child IN the original Fleet RUN_ADMITTED SQLite cut.

        There is no independent schedule claim or launch. A cancelled/advanced
        occurrence cannot create a Fleet Run; a crash cannot split their durable
        identities because the original transaction owns both mutations.
        """
        if not connection.in_transaction:
            raise ScheduleExecutionStale("Scheduled child needs original active Fleet transaction")
        if not isinstance(target, FleetPlanTarget):
            raise ScheduleExecutionStale("Scheduled child requires frozen Fleet Plan")
        current = _require_pending_in(connection, schedule_run_id, repository_id)
        instances = _bound_child_instances_in(
            connection,
            current=current,
            repository_id=repository_id,
            fleet_run_id=fleet_run_id,
            capability=capability,
            target=target,
        )
        from oh_goon.core.schedule_transitions import mark_repository_running

        promote_intent = current.intent.writable_round is not None
        if promote_intent:
            intent = current.intent.model_copy(
                update={"target": target, "repository_instance_ids": instances}
            )
            current = current.model_copy(update={"intent": intent})
        updated = mark_repository_running(current, repository_id, fleet_run_id)
        self._update(
            connection,
            _with_record_version(updated, current.record_version + 1),
            expected_revision=current.record_version,
            write_intent=promote_intent,
        )

    def latest_for_schedule_in(
        self, c: sqlite3.Connection, schedule_id: str
    ) -> ScheduleRunRecord | None:
        row = c.execute(
            "SELECT * FROM schedule_executions WHERE schedule_id=? ORDER BY started_at DESC LIMIT 1",
            (schedule_id,),
        ).fetchone()
        return _execution(row) if row is not None else None

    @staticmethod
    def latest_writable_for_generation_in(
        c: sqlite3.Connection, schedule_id: str, generation_id: str
    ) -> ScheduleRunRecord | None:
        row = c.execute(
            "SELECT * FROM schedule_executions WHERE schedule_id=? "
            "AND json_extract(intent_json, '$.definition_generation_id')=? "
            "AND json_type(intent_json, '$.writable_round.task_index')='integer' "
            "ORDER BY json_extract(intent_json, '$.writable_round.task_index') DESC LIMIT 1",
            (schedule_id, generation_id),
        ).fetchone()
        return _execution(row) if row is not None else None

    def active(self) -> list[ScheduleRunRecord]:
        with self._database.transaction() as c:
            return self.active_in(c)

    @staticmethod
    def active_in(c: sqlite3.Connection) -> list[ScheduleRunRecord]:
        return [
            _execution(r)
            for r in c.execute(
                "SELECT * FROM schedule_executions WHERE status='RUNNING' ORDER BY started_at"
            ).fetchall()
        ]

    def insert(
        self, execution: RunningScheduleRun, *, expected_durable_revision: int
    ) -> RunningScheduleRun:
        try:
            with self._database.transaction(write=True) as c:
                require_durable_revision(c, expected_durable_revision)
                self._insert(c, execution)
        except sqlite3.IntegrityError as exc:
            if self.find(execution.schedule_run_id) == execution:
                return execution
            raise ScheduleExecutionActive(
                f"Schedule {execution.schedule_id} already has an active execution"
            ) from exc
        except BaseException:
            if self.find(execution.schedule_run_id) == execution:
                return execution
            raise
        return execution

    def compare_and_set(
        self, previous: ScheduleRunRecord, next_execution: ScheduleRunRecord
    ) -> ScheduleRunRecord:
        if (
            next_execution.schedule_run_id != previous.schedule_run_id
            or next_execution.schedule_id != previous.schedule_id
        ):
            raise ValueError("Schedule transition cannot change identity")
        if next_execution.intent != previous.intent:
            raise ValueError("Schedule transition cannot change admitted target-set intent")
        if next_execution.record_version != previous.record_version:
            raise ValueError("Schedule transition input must preserve record_version until CAS")
        updated = _with_record_version(next_execution, previous.record_version + 1)
        try:
            with self._database.transaction(write=True) as c:
                self._update(c, updated, expected_revision=previous.record_version)
        except BaseException:
            if self.find(previous.schedule_run_id) == updated:
                return updated
            raise
        return updated

    @staticmethod
    def _insert(c: sqlite3.Connection, e: ScheduleRunRecord) -> None:
        failure_json = (
            e.failure.model_dump_json(exclude_computed_fields=True)
            if isinstance(e, FailedScheduleRun) and e.failure is not None
            else None
        )
        c.execute(
            "INSERT INTO schedule_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e.schedule_run_id,
                e.schedule_id,
                e.intent.model_dump_json(exclude_computed_fields=True),
                json.dumps(
                    [
                        x.model_dump(mode="json", exclude_computed_fields=True)
                        for x in e.repository_runs
                    ],
                    sort_keys=True,
                ),
                e.status.value,
                json.dumps(
                    [x.model_dump(mode="json", exclude_computed_fields=True) for x in e.attention],
                    sort_keys=True,
                ),
                failure_json,
                e.started_at.isoformat(),
                None,
                e.record_version,
            ),
        )

    @staticmethod
    def _update(
        c: sqlite3.Connection,
        e: ScheduleRunRecord,
        *,
        expected_revision: int,
        write_intent: bool = False,
    ) -> None:
        failure_json = (
            e.failure.model_dump_json(exclude_computed_fields=True)
            if isinstance(e, FailedScheduleRun) and e.failure is not None
            else None
        )
        finished = e.finished_at.isoformat() if not isinstance(e, RunningScheduleRun) else None
        cur = c.execute(
            """UPDATE schedule_executions SET repository_runs_json=?, status=?, attention_json=?, failure_json=?, finished_at=?, record_version=?, intent_json=COALESCE(?, intent_json) WHERE schedule_run_id=? AND record_version=?""",
            (
                json.dumps(
                    [
                        x.model_dump(mode="json", exclude_computed_fields=True)
                        for x in e.repository_runs
                    ],
                    sort_keys=True,
                ),
                e.status.value,
                json.dumps(
                    [x.model_dump(mode="json", exclude_computed_fields=True) for x in e.attention],
                    sort_keys=True,
                ),
                failure_json,
                finished,
                e.record_version,
                e.intent.model_dump_json(exclude_computed_fields=True) if write_intent else None,
                e.schedule_run_id,
                expected_revision,
            ),
        )
        if cur.rowcount != 1:
            raise ScheduleExecutionStale(
                f"Schedule execution changed concurrently: {e.schedule_run_id}"
            )


def _find_in(connection: sqlite3.Connection, schedule_run_id: str) -> ScheduleRunRecord | None:
    row = connection.execute(
        "SELECT * FROM schedule_executions WHERE schedule_run_id=?", (schedule_run_id,)
    ).fetchone()
    return _execution(row) if row is not None else None


def _require_pending_in(
    connection: sqlite3.Connection, schedule_run_id: str, repository_id: str
) -> RunningScheduleRun:
    current = _find_in(connection, schedule_run_id)
    if current is None:
        raise ScheduleExecutionStale("Scheduled occurrence disappeared before Fleet admission")
    if not isinstance(current, RunningScheduleRun):
        raise ScheduleExecutionStale("Scheduled occurrence cancelled before Fleet admission")
    if not any(
        row.repository_id == repository_id
        and row.status is ScheduleRepositoryRunStatus.PENDING
        and row.fleet_run_id is None
        for row in current.repository_runs
    ):
        raise ScheduleExecutionStale("Scheduled Fleet child is no longer pending admission")
    return current


def _require_writable_selection_in(
    connection: sqlite3.Connection, current: RunningScheduleRun
) -> ScheduledWritableRound:
    selected = current.intent.writable_round
    row = connection.execute(
        "SELECT generation_id, state FROM schedule_definitions WHERE schedule_id=?",
        (current.schedule_id,),
    ).fetchone()
    if (
        selected is None
        or row is None
        or row["generation_id"] != current.intent.definition_generation_id
        or row["state"] != ScheduleState.ACTIVE.value
    ):
        raise ScheduleExecutionStale("Writable Schedule has no active selected occurrence")
    policy = ScheduleDefinitionStore.writable_policy_in(
        connection, current.intent.definition_generation_id
    )
    if (
        policy is None
        or selected.task_index >= len(policy.tasks)
        or selected.task != policy.tasks[selected.task_index]
        or selected.native_selection_id != policy.native_selection_id
    ):
        raise ScheduleExecutionStale("Writable occurrence task differs from its authored policy")
    if selected.task_index == 0:
        if selected.advance_id != policy.seed_advance_id:
            raise ScheduleExecutionStale("Writable occurrence does not bind its reserved seed")
    else:
        previous = _find_in(
            connection,
            writable_schedule_run_id(
                current.intent.definition_generation_id, selected.task_index - 1
            ),
        )
        if (
            not isinstance(previous, CompletedScheduleRun)
            or previous.schedule_id != current.schedule_id
            or previous.intent.repository_scope != current.intent.repository_scope
            or previous.intent.writable_round is None
            or previous.repository_runs[0].status is not ScheduleRepositoryRunStatus.SUCCESSFUL
            or previous.repository_runs[0].fleet_run_id != selected.predecessor_run_id
        ):
            raise ScheduleExecutionStale("Writable occurrence lacks its successful predecessor Run")
    return selected


def _bound_child_instances_in(
    connection: sqlite3.Connection,
    *,
    current: RunningScheduleRun,
    repository_id: str,
    fleet_run_id: str,
    capability: CapabilitySnapshot,
    target: FleetPlanTarget,
) -> dict[str, str]:
    if current.intent.capability != capability:
        raise ScheduleExecutionStale(
            "Scheduled Fleet child differs from frozen capability authority"
        )
    if current.intent.writable_round is None:
        if (
            fleet_run_id != schedule_repository_fleet_run_id(current.schedule_run_id, repository_id)
            or current.intent.target != target
        ):
            raise ScheduleExecutionStale(
                "Scheduled Fleet child differs from frozen occurrence authority"
            )
        return {repository_id: current.intent.repository_instance_ids[repository_id]}

    selected = _require_writable_selection_in(connection, current)
    receipt = BranchAdvanceStore.find_in(connection, selected.advance_id)
    candidate = target.candidate_workspace
    if (
        fleet_run_id != branch_round_run_id(selected.advance_id)
        or receipt is None
        or receipt.published_at is None
        or receipt.next_run is None
        or receipt.next_run.fleet_run_id != fleet_run_id
        or receipt.next_run.repository_subject_id is None
        or candidate is None
        or candidate.fleet_run_id != fleet_run_id
        or candidate.workspace.repository_id != repository_id
        or receipt.preparation.repository_id != repository_id
        or candidate.workspace.base_revision != receipt.preparation.commit
        or candidate.source.subject_instance_id != receipt.next_run.repository_subject_id
    ):
        raise ScheduleExecutionStale("Writable child differs from its published branch receipt")
    verified = receipt.preparation.verification
    source = candidate.source.source_snapshot
    agents = tuple(step for step in target.plan.steps if isinstance(step, ResolvedAgentStep))
    if (
        (
            selected.predecessor_run_id is not None
            and verified.fleet_run_id != selected.predecessor_run_id
        )
        or (source.sha256, source.byte_count, source.entry_count)
        != (
            verified.source_sha256,
            receipt.preparation.source_byte_count,
            receipt.preparation.source_entry_count,
        )
        or len(agents) != 1
        or agents[0].task_sha256 != selected.task.prompt_sha256
        or not isinstance(agents[0].workload, ResolvedNativeAgentWorkload)
        or writable_native_selection_id(agents[0].workload.native_agent)
        != selected.native_selection_id
        or target.plan.native_agent is None
        or writable_native_selection_id(target.plan.native_agent) != selected.native_selection_id
        or receipt.next_run.request_sha256
        != writable_round_request_sha256(
            selected=target.plan.native_agent, skill=None, task=selected.task.prompt
        )
    ):
        raise ScheduleExecutionStale(
            "Writable child differs from its frozen task, profile or source"
        )
    return {repository_id: receipt.next_run.repository_subject_id}


def _execution(row: sqlite3.Row) -> ScheduleRunRecord:
    payload = {
        "schedule_run_id": row["schedule_run_id"],
        "schedule_id": row["schedule_id"],
        "intent": ScheduledFleetRunIntent.model_validate_json(row["intent_json"]),
        "repository_runs": json.loads(row["repository_runs_json"]),
        "status": ScheduleExecutionStatus(row["status"]),
        "attention": json.loads(row["attention_json"]),
        "started_at": row["started_at"],
        "record_version": int(row["record_version"]),
    }
    if row["finished_at"]:
        payload["finished_at"] = row["finished_at"]
    if row["failure_json"]:
        payload["failure"] = json.loads(row["failure_json"])
    return parse_schedule_run(payload)


def _with_record_version(e: ScheduleRunRecord, record_version: int) -> ScheduleRunRecord:
    payload = e.model_dump(mode="python", exclude_computed_fields=True)
    payload["record_version"] = record_version
    return parse_schedule_run(payload)
