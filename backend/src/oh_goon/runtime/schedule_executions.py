from __future__ import annotations

import json
import sqlite3

from oh_goon.core.errors import ScheduleExecutionActive, ScheduleExecutionStale
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.schedule_execution import (
    FailedScheduleRun,
    RunningScheduleRun,
    ScheduledFleetRunIntent,
    ScheduleRunRecord,
    parse_schedule_run,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import schedule_repository_fleet_run_id
from oh_goon.core.vocabulary import ScheduleExecutionStatus, ScheduleRepositoryRunStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.durable_revision import require_durable_revision


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
            row = c.execute(
                "SELECT * FROM schedule_executions WHERE schedule_run_id=?", (schedule_run_id,)
            ).fetchone()
        return _execution(row) if row is not None else None

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
        current = self.find(schedule_run_id)
        if not isinstance(current, RunningScheduleRun):
            raise ScheduleExecutionStale("Scheduled Fleet child has no active durable occurrence")
        expected_id = schedule_repository_fleet_run_id(schedule_run_id, repository_id)
        if fleet_run_id != expected_id:
            raise ScheduleExecutionStale("Scheduled Fleet child identity is not occurrence-bound")
        if (
            current.intent.capability != capability
            or current.intent.target != target
            or repository_instance_ids
            != {repository_id: current.intent.repository_instance_ids.get(repository_id)}
            or repository_id not in current.intent.repository_scope.repository_ids
        ):
            raise ScheduleExecutionStale(
                "Scheduled Fleet child does not match frozen occurrence authority"
            )
        if not any(
            row.repository_id == repository_id
            and row.status is ScheduleRepositoryRunStatus.PENDING
            and row.fleet_run_id is None
            for row in current.repository_runs
        ):
            raise ScheduleExecutionStale("Scheduled Fleet child is no longer pending admission")

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
        row = connection.execute(
            "SELECT * FROM schedule_executions WHERE schedule_run_id=?", (schedule_run_id,)
        ).fetchone()
        if row is None:
            raise ScheduleExecutionStale("Scheduled occurrence disappeared before Fleet admission")
        current = _execution(row)
        if not isinstance(current, RunningScheduleRun):
            raise ScheduleExecutionStale("Scheduled occurrence cancelled before Fleet admission")
        if (
            fleet_run_id != schedule_repository_fleet_run_id(schedule_run_id, repository_id)
            or current.intent.capability != capability
            or current.intent.target != target
            or repository_id not in current.intent.repository_scope.repository_ids
        ):
            raise ScheduleExecutionStale("Scheduled child differs from frozen occurrence authority")
        if not any(
            item.repository_id == repository_id
            and item.status is ScheduleRepositoryRunStatus.PENDING
            and item.fleet_run_id is None
            for item in current.repository_runs
        ):
            raise ScheduleExecutionStale("Scheduled child already consumed or cancelled")
        from oh_goon.core.schedule_transitions import mark_repository_running

        updated = mark_repository_running(current, repository_id, fleet_run_id)
        self._update(
            connection,
            _with_record_version(updated, current.record_version + 1),
            expected_revision=current.record_version,
        )

    def latest_for_schedule_in(
        self, c: sqlite3.Connection, schedule_id: str
    ) -> ScheduleRunRecord | None:
        row = c.execute(
            "SELECT * FROM schedule_executions WHERE schedule_id=? ORDER BY started_at DESC LIMIT 1",
            (schedule_id,),
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
    def _update(c: sqlite3.Connection, e: ScheduleRunRecord, *, expected_revision: int) -> None:
        failure_json = (
            e.failure.model_dump_json(exclude_computed_fields=True)
            if isinstance(e, FailedScheduleRun) and e.failure is not None
            else None
        )
        finished = e.finished_at.isoformat() if not isinstance(e, RunningScheduleRun) else None
        cur = c.execute(
            """UPDATE schedule_executions SET repository_runs_json=?, status=?, attention_json=?, failure_json=?, finished_at=?, record_version=? WHERE schedule_run_id=? AND record_version=?""",
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
                e.schedule_run_id,
                expected_revision,
            ),
        )
        if cur.rowcount != 1:
            raise ScheduleExecutionStale(
                f"Schedule execution changed concurrently: {e.schedule_run_id}"
            )


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
