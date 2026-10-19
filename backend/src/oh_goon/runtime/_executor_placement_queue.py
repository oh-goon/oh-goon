from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oh_goon.core.errors import (
    ExecutorAdmissionCancelled,
    ExecutorCapacityBusy,
    ExecutorQueueEntryNotFound,
    FleetPlanInvalid,
    StateConflict,
)
from oh_goon.core.models.fleet_executor_operations import (
    CancellationRequestedPlacementQueueEntry,
    PlacementQueueEntry,
    PlacementQueueSnapshot,
    WaitingPlacementQueueEntry,
)
from oh_goon.core.models.scheduling import PriorityScope, SchedulingEnvelope, SchedulingPriority
from oh_goon.runtime.database import LocalDatabase


@dataclass(frozen=True, slots=True)
class PlacementQueueTicket:
    fleet_run_id: str
    sequence: int


_QUEUE_ORDER_SQL = """
    CASE admitted_priority
        WHEN 'CRITICAL' THEN 0
        WHEN 'HIGH' THEN 1
        WHEN 'NORMAL' THEN 2
        WHEN 'LOW' THEN 3
        ELSE 4
    END, sequence, fleet_run_id
"""


class ExecutorPlacementQueue:
    """SQLite-backed FIFO pre-admission authority shared by Fleet processes.

    Queue state is durable coordination, not a product revision. The final ticket
    consumption occurs inside the same write transaction that creates executor
    capacity, the Fleet execution lease, and RUN_ADMITTED.
    """

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def enter(
        self,
        fleet_run_id: str,
        *,
        max_entries: int,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
        initially_eligible: bool = True,
    ) -> PlacementQueueTicket:
        identity = _process_identity(os.getpid())
        if identity is None:
            raise FleetPlanInvalid("Fleet cannot identify the process owning placement admission")
        try:
            with self._database.transaction(write=True, track_revision=False) as connection:
                row = self._row_for_run(connection, fleet_run_id)
                if row is not None:
                    return self._claim_existing_ticket(
                        connection, row, fleet_run_id, identity, requested_priority
                    )
                self._reclaim_dead_in(connection)
                active_entries = int(
                    connection.execute("SELECT COUNT(*) FROM executor_placement_queue").fetchone()[
                        0
                    ]
                )
                if active_entries >= max_entries:
                    raise ExecutorCapacityBusy(
                        "Fleet placement queue is full; explicit backpressure rejected "
                        f"new admission ({active_entries}/{max_entries} queued)"
                    )
                canonical_admitted, priority_decision_reason = _admit_local_priority(
                    requested_priority
                )
                cursor = connection.execute(
                    """
                    INSERT INTO executor_placement_queue(
                        fleet_run_id, scheduling_scope_id, admission_domain_id,
                        requested_priority, admitted_priority, priority_scope,
                        priority_decision_reason, scheduling_revision,
                        scheduling_policy_revision, priority_policy_revision, created_at,
                        owner_session_id, owner_pid, owner_identity, eligibility_ready
                    ) VALUES (?, 'local-default', 'default', ?, ?, ?, ?, 0, 'local-v1', 'local-v1', ?, ?, ?, ?, ?)
                    """,
                    (
                        fleet_run_id,
                        requested_priority.value,
                        canonical_admitted.value,
                        PriorityScope.DOMAIN.value,
                        priority_decision_reason,
                        datetime.now(UTC).isoformat(),
                        self._database.session_id,
                        os.getpid(),
                        identity,
                        1 if initially_eligible else 0,
                    ),
                )
                return PlacementQueueTicket(
                    fleet_run_id=fleet_run_id, sequence=int(cursor.lastrowid)
                )
        except BaseException:
            replay = self._replay_enter(fleet_run_id, identity, requested_priority)
            if replay is not None:
                return replay
            raise

    def _claim_existing_ticket(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        fleet_run_id: str,
        identity: str,
        requested_priority: SchedulingPriority,
    ) -> PlacementQueueTicket:
        """Reclaim one durable scheduling identity without manufacturing new FIFO age."""
        self._require_not_cancelled(row)
        if str(row["requested_priority"]) != requested_priority.value:
            raise FleetPlanInvalid("Fleet placement queue replay changed requested priority")
        if not _owned_by_session(row, self._database):
            owner_identity = str(row["owner_identity"])
            if _process_identity(int(row["owner_pid"])) == owner_identity:
                raise FleetPlanInvalid(
                    "Fleet placement queue identity is already owned by another live runtime session"
                )
            connection.execute(
                """
                UPDATE executor_placement_queue
                SET owner_session_id = ?, owner_pid = ?, owner_identity = ?
                WHERE fleet_run_id = ? AND sequence = ?
                """,
                (
                    self._database.session_id,
                    os.getpid(),
                    identity,
                    fleet_run_id,
                    int(row["sequence"]),
                ),
            )
            row = self._row_for_run(connection, fleet_run_id)
            assert row is not None
        self._reclaim_dead_in(connection, except_run_id=fleet_run_id)
        return self._ticket(row)

    def set_eligibility(self, ticket: PlacementQueueTicket, *, ready: bool) -> None:
        """Publish this admission's current resource/capability eligibility.

        Eligibility is advisory until the final write transaction re-proves both
        queue turn and capacity. Keeping it in the shared queue lets an ineligible
        higher-priority request stop blocking runnable compatible work.
        """
        with self._database.transaction(write=True, track_revision=False) as connection:
            self._reclaim_dead_in(connection)
            current = self._require_ticket(connection, ticket)
            self._require_not_cancelled(current)
            value = 1 if ready else 0
            if int(current["eligibility_ready"]) != value:
                connection.execute(
                    """
                    UPDATE executor_placement_queue
                    SET eligibility_ready = ?, eligibility_revision = eligibility_revision + 1
                    WHERE fleet_run_id = ? AND sequence = ?
                    """,
                    (value, ticket.fleet_run_id, ticket.sequence),
                )

    def is_turn(self, ticket: PlacementQueueTicket) -> bool:
        with self._database.transaction(write=True, track_revision=False) as connection:
            self._reclaim_dead_in(connection)
            current = self._require_ticket(connection, ticket)
            self._require_not_cancelled(current)
            first = connection.execute(
                "SELECT fleet_run_id FROM executor_placement_queue WHERE eligibility_ready = 1 AND cancellation_requested = 0 ORDER BY "
                + _QUEUE_ORDER_SQL
                + " LIMIT 1"
            ).fetchone()
            return first is not None and str(first["fleet_run_id"]) == ticket.fleet_run_id

    def snapshot(self) -> PlacementQueueSnapshot:
        """Project persisted queue coordination without mutating admission state."""
        with self._database.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM executor_placement_queue ORDER BY " + _QUEUE_ORDER_SQL
            ).fetchall()
            return PlacementQueueSnapshot(
                entries=tuple(self._entry(row, position) for position, row in enumerate(rows, 1))
            )

    def cancel(self, fleet_run_id: str, *, expected_sequence: int) -> PlacementQueueEntry:
        with self._database.transaction(write=True, track_revision=False) as connection:
            self._reclaim_dead_in(connection)
            rows = connection.execute(
                "SELECT * FROM executor_placement_queue ORDER BY " + _QUEUE_ORDER_SQL
            ).fetchall()
            current = next((row for row in rows if str(row["fleet_run_id"]) == fleet_run_id), None)
            if current is None:
                raise ExecutorQueueEntryNotFound(
                    f"No queued Fleet admission exists for {fleet_run_id}; it may already be admitted"
                )
            if int(current["sequence"]) != expected_sequence:
                raise StateConflict(f"Fleet placement queue entry changed: {fleet_run_id}")
            position = rows.index(current) + 1
            if not bool(current["cancellation_requested"]):
                connection.execute(
                    "UPDATE executor_placement_queue SET cancellation_requested = 1 WHERE fleet_run_id = ?",
                    (fleet_run_id,),
                )
                current = connection.execute(
                    "SELECT * FROM executor_placement_queue WHERE fleet_run_id = ?",
                    (fleet_run_id,),
                ).fetchone()
                assert current is not None
            return self._entry(current, position)

    def reprioritize(
        self,
        fleet_run_id: str,
        *,
        admitted_priority: SchedulingPriority,
        expected_revision: int,
        reason: str,
    ) -> PlacementQueueEntry:
        """CAS-update queued priority while preserving durable FIFO identity."""
        if not reason.strip():
            raise FleetPlanInvalid("Fleet reprioritization requires a non-empty reason")
        if admitted_priority is SchedulingPriority.CRITICAL:
            raise FleetPlanInvalid("CRITICAL priority requires a trusted admission policy")
        with self._database.transaction(write=True, track_revision=False) as connection:
            self._reclaim_dead_in(connection)
            current = self._row_for_run(connection, fleet_run_id)
            if current is None:
                raise ExecutorQueueEntryNotFound(
                    f"No queued Fleet admission exists for {fleet_run_id}; it may already be admitted"
                )
            self._require_not_cancelled(current)
            if int(current["scheduling_revision"]) != expected_revision:
                raise StateConflict(
                    f"Fleet scheduling revision changed: {fleet_run_id} "
                    f"(expected {expected_revision}, observed {current['scheduling_revision']})"
                )
            cursor = connection.execute(
                """
                UPDATE executor_placement_queue
                SET admitted_priority = ?, priority_decision_reason = ?,
                    scheduling_revision = scheduling_revision + 1
                WHERE fleet_run_id = ? AND scheduling_revision = ?
                """,
                (admitted_priority.value, reason, fleet_run_id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise StateConflict(f"Fleet scheduling revision changed: {fleet_run_id}")
            rows = connection.execute(
                "SELECT * FROM executor_placement_queue ORDER BY " + _QUEUE_ORDER_SQL
            ).fetchall()
            updated = next(row for row in rows if str(row["fleet_run_id"]) == fleet_run_id)
            return self._entry(updated, rows.index(updated) + 1)

    def require_turn_in(
        self,
        connection: sqlite3.Connection,
        ticket: PlacementQueueTicket,
    ) -> None:
        """Prove FIFO/cancellation inside the caller's final admission transaction."""
        self._reclaim_dead_in(connection)
        current = self._require_ticket(connection, ticket)
        self._require_not_cancelled(current)
        first = connection.execute(
            "SELECT fleet_run_id, sequence FROM executor_placement_queue WHERE eligibility_ready = 1 AND cancellation_requested = 0 ORDER BY "
            + _QUEUE_ORDER_SQL
            + " LIMIT 1"
        ).fetchone()
        if (
            first is None
            or str(first["fleet_run_id"]) != ticket.fleet_run_id
            or int(first["sequence"]) != ticket.sequence
        ):
            raise FleetPlanInvalid("Fleet placement queue turn changed before RUN_ADMITTED")

    def consume_in(
        self,
        connection: sqlite3.Connection,
        ticket: PlacementQueueTicket,
    ) -> None:
        self.require_turn_in(connection, ticket)
        connection.execute(
            """
            INSERT INTO run_scheduling_receipts(
                fleet_run_id, scheduling_scope_id, admission_domain_id,
                requested_priority, admitted_priority, priority_scope,
                priority_decision_reason, queue_seq, scheduling_revision,
                scheduling_policy_revision, priority_policy_revision, created_at, admitted_at
            )
            SELECT fleet_run_id, scheduling_scope_id, admission_domain_id,
                   requested_priority, admitted_priority, priority_scope,
                   priority_decision_reason, sequence, scheduling_revision,
                   scheduling_policy_revision, priority_policy_revision, created_at, ?
            FROM executor_placement_queue
            WHERE fleet_run_id = ? AND sequence = ?
            """,
            (datetime.now(UTC).isoformat(), ticket.fleet_run_id, ticket.sequence),
        )
        cursor = connection.execute(
            "DELETE FROM executor_placement_queue WHERE fleet_run_id = ? AND sequence = ?",
            (ticket.fleet_run_id, ticket.sequence),
        )
        if cursor.rowcount != 1:
            raise FleetPlanInvalid("Fleet placement queue ticket disappeared before RUN_ADMITTED")

    def scheduling_receipt_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> SchedulingEnvelope | None:
        """Read the immutable admitted scheduling receipt owned by Scheduling Authority."""
        row = connection.execute(
            "SELECT * FROM run_scheduling_receipts WHERE fleet_run_id = ?",
            (fleet_run_id,),
        ).fetchone()
        if row is None:
            return None
        return SchedulingEnvelope(
            scheduling_scope_id=str(row["scheduling_scope_id"]),
            admission_domain_id=str(row["admission_domain_id"]),
            run_id=str(row["fleet_run_id"]),
            requested_priority=SchedulingPriority(str(row["requested_priority"])),
            admitted_priority=SchedulingPriority(str(row["admitted_priority"])),
            priority_scope=PriorityScope(str(row["priority_scope"])),
            priority_decision_reason=str(row["priority_decision_reason"]),
            queue_seq=int(row["queue_seq"]),
            scheduling_revision=int(row["scheduling_revision"]),
            scheduling_policy_revision=str(row["scheduling_policy_revision"]),
            priority_policy_revision=str(row["priority_policy_revision"]),
            created_at=str(row["created_at"]),
            admitted_at=str(row["admitted_at"]),
        )

    def leave(self, ticket: PlacementQueueTicket) -> None:
        try:
            with self._database.transaction(write=True, track_revision=False) as connection:
                row = self._row_for_run(connection, ticket.fleet_run_id)
                if row is None:
                    return
                if int(row["sequence"]) != ticket.sequence:
                    raise FleetPlanInvalid(
                        "Fleet placement queue ticket identity changed before release"
                    )
                if not _owned_by_session(row, self._database):
                    owner_identity = str(row["owner_identity"])
                    if _process_identity(int(row["owner_pid"])) == owner_identity:
                        raise FleetPlanInvalid(
                            "Fleet placement queue ticket is owned by another live runtime session"
                        )
                connection.execute(
                    "DELETE FROM executor_placement_queue WHERE fleet_run_id = ? AND sequence = ?",
                    (ticket.fleet_run_id, ticket.sequence),
                )
        except BaseException:
            with self._database.transaction() as connection:
                if self._row_for_run(connection, ticket.fleet_run_id) is None:
                    return
            raise

    def _reclaim_dead_in(
        self, connection: sqlite3.Connection, *, except_run_id: str | None = None
    ) -> None:
        rows = connection.execute(
            "SELECT fleet_run_id, owner_pid, owner_identity FROM executor_placement_queue"
        ).fetchall()
        for row in rows:
            fleet_run_id = str(row["fleet_run_id"])
            if fleet_run_id == except_run_id:
                continue
            if _process_identity(int(row["owner_pid"])) != str(row["owner_identity"]):
                connection.execute(
                    "DELETE FROM executor_placement_queue WHERE fleet_run_id = ?",
                    (fleet_run_id,),
                )

    @staticmethod
    def _row_for_run(connection: sqlite3.Connection, fleet_run_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM executor_placement_queue WHERE fleet_run_id = ?",
            (fleet_run_id,),
        ).fetchone()

    def _require_ticket(
        self,
        connection: sqlite3.Connection,
        ticket: PlacementQueueTicket,
    ) -> sqlite3.Row:
        current = self._row_for_run(connection, ticket.fleet_run_id)
        if current is None or int(current["sequence"]) != ticket.sequence:
            raise FleetPlanInvalid("Fleet placement queue ticket disappeared or changed")
        return current

    @staticmethod
    def _require_not_cancelled(row: sqlite3.Row) -> None:
        if bool(row["cancellation_requested"]):
            raise ExecutorAdmissionCancelled(
                f"Fleet admission was cancelled before RUN_ADMITTED: {row['fleet_run_id']}"
            )

    @staticmethod
    def _entry(row: sqlite3.Row, position: int) -> PlacementQueueEntry:
        common = {
            "fleet_run_id": str(row["fleet_run_id"]),
            "sequence": int(row["sequence"]),
            "position": position,
            "scheduling_scope_id": str(row["scheduling_scope_id"]),
            "admission_domain_id": str(row["admission_domain_id"]),
            "requested_priority": SchedulingPriority(str(row["requested_priority"])),
            "admitted_priority": SchedulingPriority(str(row["admitted_priority"])),
            "priority_scope": PriorityScope(str(row["priority_scope"])),
            "priority_decision_reason": str(row["priority_decision_reason"]),
            "scheduling_revision": int(row["scheduling_revision"]),
            "scheduling_policy_revision": str(row["scheduling_policy_revision"]),
            "priority_policy_revision": str(row["priority_policy_revision"]),
        }
        if bool(row["cancellation_requested"]):
            return CancellationRequestedPlacementQueueEntry(
                state="cancellation_requested", **common
            )
        return WaitingPlacementQueueEntry(state="waiting", **common)

    @staticmethod
    def _ticket(row: sqlite3.Row) -> PlacementQueueTicket:
        return PlacementQueueTicket(
            fleet_run_id=str(row["fleet_run_id"]),
            sequence=int(row["sequence"]),
        )

    def _replay_enter(
        self,
        fleet_run_id: str,
        owner_identity: str,
        requested_priority: SchedulingPriority,
    ) -> PlacementQueueTicket | None:
        with self._database.transaction() as connection:
            row = self._row_for_run(connection, fleet_run_id)
            if row is None or bool(row["cancellation_requested"]):
                return None
            if str(row["requested_priority"]) != requested_priority.value:
                return None
            if (
                str(row["owner_session_id"]) != self._database.session_id
                or int(row["owner_pid"]) != os.getpid()
                or str(row["owner_identity"]) != owner_identity
            ):
                return None
            return self._ticket(row)


def _admit_local_priority(
    requested: SchedulingPriority,
) -> tuple[SchedulingPriority, str]:
    if requested is SchedulingPriority.CRITICAL:
        raise FleetPlanInvalid("CRITICAL priority requires a trusted admission policy")
    return requested, "LOCAL_V1_AUTHORIZED"


def _owned_by_session(row: sqlite3.Row, database: LocalDatabase) -> bool:
    return (
        str(row["owner_session_id"]) == database.session_id
        and int(row["owner_pid"]) == os.getpid()
        and str(row["owner_identity"]) == _process_identity(os.getpid())
    )


def _process_identity(pid: int) -> str | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        return None
    return f"{boot_id}:{start_ticks}"
