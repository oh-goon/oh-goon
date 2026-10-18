from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.errors import (
    ExecutorAdmissionCancelled,
    ExecutorCapacityBusy,
    FleetPlanInvalid,
    StateConflict,
)
from oh_goon.core.models.scheduling import PriorityScope, SchedulingPriority
from oh_goon.runtime._executor_placement_queue import ExecutorPlacementQueue
from oh_goon.runtime.database import LocalDatabase


def _queue(tmp_path: Path) -> tuple[LocalDatabase, ExecutorPlacementQueue]:
    database = LocalDatabase(tmp_path / "state.db")
    return database, ExecutorPlacementQueue(database)


def test_placement_queue_is_fifo(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    first = queue.enter("run-a", max_entries=64)
    second = queue.enter("run-b", max_entries=64)

    assert queue.is_turn(first) is True
    assert queue.is_turn(second) is False

    queue.leave(first)
    assert queue.is_turn(second) is True
    queue.leave(second)


def test_placement_queue_rejects_new_run_at_configured_limit(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    first = queue.enter("run-full-a", max_entries=1)

    with pytest.raises(ExecutorCapacityBusy, match=r"queue is full.*1/1"):
        queue.enter("run-full-b", max_entries=1)

    assert [item.fleet_run_id for item in queue.snapshot().entries] == ["run-full-a"]
    queue.leave(first)


def test_placement_queue_replays_same_run_even_when_limit_is_full(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    first = queue.enter("run-replay-full", max_entries=1)

    replay = queue.enter("run-replay-full", max_entries=1)

    assert replay == first
    queue.leave(first)


def test_placement_queue_reclaims_dead_owner_before_applying_limit(tmp_path: Path) -> None:
    database, queue = _queue(tmp_path)
    with database.transaction(write=True, track_revision=False) as connection:
        connection.execute(
            """
            INSERT INTO executor_placement_queue(
                fleet_run_id, owner_session_id, owner_pid, owner_identity
            ) VALUES ('dead-full', 'dead-session', 999999999, 'dead-owner')
            """
        )

    live = queue.enter("run-after-dead-full", max_entries=1)

    assert queue.is_turn(live) is True
    queue.leave(live)


def test_placement_queue_reclaims_dead_owner_ticket(tmp_path: Path) -> None:
    database, queue = _queue(tmp_path)
    with database.transaction(write=True, track_revision=False) as connection:
        connection.execute(
            """
            INSERT INTO executor_placement_queue(
                fleet_run_id, owner_session_id, owner_pid, owner_identity
            ) VALUES ('dead', 'dead-session', 999999999, 'dead-owner')
            """
        )

    ticket = queue.enter("live", max_entries=64)

    assert queue.is_turn(ticket) is True
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM executor_placement_queue WHERE fleet_run_id = 'dead'"
            ).fetchone()
            is None
        )
    queue.leave(ticket)


def test_queue_coordination_does_not_change_product_durable_revision(tmp_path: Path) -> None:
    database, queue = _queue(tmp_path)
    revision = database.durable_revision()

    ticket = queue.enter("run-a", max_entries=64)
    queue.cancel("run-a", expected_sequence=ticket.sequence)
    queue.leave(ticket)

    assert database.durable_revision() == revision


def _lose_next_coordination_commit_response(database: LocalDatabase) -> None:
    original = database.transaction
    armed = True

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal armed
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and not track_revision and armed:
            armed = False
            raise RuntimeError("placement queue commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def test_queue_ticket_is_bound_to_exact_runtime_session(tmp_path: Path) -> None:
    path = tmp_path / "shared-state.db"
    first_database = LocalDatabase(path)
    first = ExecutorPlacementQueue(first_database)
    ticket = first.enter("session-owned", max_entries=64)

    second_database = LocalDatabase(path)
    second = ExecutorPlacementQueue(second_database)
    with pytest.raises(FleetPlanInvalid, match="another live runtime session"):
        second.enter("session-owned", max_entries=64)

    first.leave(ticket)


def test_queue_enter_replays_exact_session_ticket_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database, queue = _queue(tmp_path)
    _lose_next_coordination_commit_response(database)

    ticket = queue.enter("ambiguous-enter", max_entries=64)

    with database.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM executor_placement_queue WHERE fleet_run_id = ?",
            ("ambiguous-enter",),
        ).fetchone()
    assert row is not None
    assert int(row["sequence"]) == ticket.sequence
    assert str(row["owner_session_id"]) == database.session_id
    queue.leave(ticket)


def test_queue_leave_accepts_canonical_absence_after_ambiguous_commit_response(
    tmp_path: Path,
) -> None:
    database, queue = _queue(tmp_path)
    ticket = queue.enter("ambiguous-leave", max_entries=64)
    _lose_next_coordination_commit_response(database)

    queue.leave(ticket)

    assert queue.snapshot().entries == ()


def test_queue_snapshot_is_read_only_and_does_not_reclaim_dead_ticket(tmp_path: Path) -> None:
    database, queue = _queue(tmp_path)
    with database.transaction(write=True, track_revision=False) as connection:
        connection.execute(
            """
            INSERT INTO executor_placement_queue(
                fleet_run_id, owner_session_id, owner_pid, owner_identity
            ) VALUES ('dead-observed', 'dead-session', 999999999, 'dead-owner')
            """
        )

    snapshot = queue.snapshot()

    assert [item.fleet_run_id for item in snapshot.entries] == ["dead-observed"]
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM executor_placement_queue WHERE fleet_run_id='dead-observed'"
            ).fetchone()
            is not None
        )

    ticket = queue.enter("live-after-observation", max_entries=64)
    assert queue.is_turn(ticket) is True
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM executor_placement_queue WHERE fleet_run_id='dead-observed'"
            ).fetchone()
            is None
        )
    queue.leave(ticket)


def test_placement_queue_prefers_priority_then_preserves_fifo(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    normal_a = queue.enter("normal-a", max_entries=64)
    normal_b = queue.enter("normal-b", max_entries=64)
    high = queue.enter("high", max_entries=64, requested_priority=SchedulingPriority.HIGH)

    assert queue.is_turn(high) is True
    assert queue.is_turn(normal_a) is False
    queue.leave(high)
    assert queue.is_turn(normal_a) is True
    queue.leave(normal_a)
    assert queue.is_turn(normal_b) is True
    queue.leave(normal_b)


def test_reprioritization_preserves_queue_sequence_and_uses_cas(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    first = queue.enter("first", max_entries=64)
    second = queue.enter("second", max_entries=64)

    updated = queue.reprioritize(
        "second",
        admitted_priority=SchedulingPriority.HIGH,
        expected_revision=0,
        reason="operator escalation",
    )

    assert updated.sequence == second.sequence
    assert updated.scheduling_revision == 1
    assert updated.admitted_priority is SchedulingPriority.HIGH
    assert queue.is_turn(second) is True
    with pytest.raises(StateConflict, match="scheduling revision changed"):
        queue.reprioritize(
            "second",
            admitted_priority=SchedulingPriority.LOW,
            expected_revision=0,
            reason="stale writer",
        )
    queue.leave(second)
    queue.leave(first)


def test_local_queue_binds_canonical_scope_domain_and_priority_scope(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    ticket = queue.enter("identity", max_entries=64)

    entry = queue.snapshot().entries[0]
    assert entry.scheduling_scope_id == "local-default"
    assert entry.admission_domain_id == "default"
    assert entry.requested_priority is SchedulingPriority.NORMAL
    assert entry.admitted_priority is SchedulingPriority.NORMAL
    assert entry.priority_scope is PriorityScope.DOMAIN
    queue.leave(ticket)


def test_untrusted_scheduling_scope_priority_is_rejected(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)

    with pytest.raises(FleetPlanInvalid, match="trusted admission policy"):
        queue.enter(
            "scope-priority",
            max_entries=64,
            requested_priority=SchedulingPriority.CRITICAL,
        )


def test_queue_replay_cannot_change_requested_priority(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    ticket = queue.enter("stable-request", max_entries=64)

    with pytest.raises(FleetPlanInvalid, match="changed requested priority"):
        queue.enter(
            "stable-request",
            max_entries=64,
            requested_priority=SchedulingPriority.HIGH,
        )
    queue.leave(ticket)


def test_cancelled_high_priority_does_not_block_runnable_normal(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    high = queue.enter(
        "high-cancelled",
        max_entries=64,
        requested_priority=SchedulingPriority.HIGH,
    )
    normal = queue.enter("normal-after-cancel", max_entries=64)

    queue.cancel("high-cancelled", expected_sequence=high.sequence)

    assert queue.is_turn(normal) is True
    with pytest.raises(ExecutorAdmissionCancelled):
        queue.is_turn(high)
    queue.leave(high)
    queue.leave(normal)


def test_ineligible_high_priority_does_not_block_runnable_normal(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    high = queue.enter(
        "high-gpu",
        max_entries=64,
        requested_priority=SchedulingPriority.HIGH,
        initially_eligible=False,
    )
    normal = queue.enter("normal-cpu", max_entries=64, initially_eligible=True)

    assert queue.is_turn(high) is False
    assert queue.is_turn(normal) is True

    queue.set_eligibility(high, ready=True)
    assert queue.is_turn(high) is True
    assert queue.is_turn(normal) is False
    queue.leave(high)
    queue.leave(normal)
