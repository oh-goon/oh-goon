from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutorAdmissionCancelled, ExecutorQueueEntryNotFound
from oh_goon.runtime._executor_placement_queue import ExecutorPlacementQueue
from oh_goon.runtime.database import LocalDatabase


def _queue(tmp_path: Path) -> tuple[LocalDatabase, ExecutorPlacementQueue]:
    database = LocalDatabase(tmp_path / "state.db")
    return database, ExecutorPlacementQueue(database)


def test_queue_snapshot_exposes_fifo_positions_and_cancellation_state(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    first = queue.enter("run-first", max_entries=64)
    second = queue.enter("run-second", max_entries=64)

    snapshot = queue.snapshot()
    assert [(entry.fleet_run_id, entry.position) for entry in snapshot.entries] == [
        ("run-first", 1),
        ("run-second", 2),
    ]

    cancelled = queue.cancel("run-second", expected_sequence=second.sequence)
    assert cancelled.position == 2
    assert cancelled.state == "cancellation_requested"
    with pytest.raises(ExecutorAdmissionCancelled, match="run-second"):
        queue.is_turn(second)

    queue.leave(second)
    queue.leave(first)


def test_cancellation_wins_before_final_admission_transaction(tmp_path: Path) -> None:
    database, queue = _queue(tmp_path)
    ticket = queue.enter("run-cancelled", max_entries=64)
    queue.cancel("run-cancelled", expected_sequence=ticket.sequence)

    with pytest.raises(ExecutorAdmissionCancelled, match="run-cancelled"):
        with database.transaction(write=True) as connection:
            queue.consume_in(connection, ticket)

    queue.leave(ticket)
    assert queue.snapshot().entries == ()


def test_final_admission_transaction_wins_before_late_queue_cancellation(tmp_path: Path) -> None:
    database, queue = _queue(tmp_path)
    ticket = queue.enter("run-admitting", max_entries=64)
    transaction_entered = threading.Event()
    release_transaction = threading.Event()
    cancel_result: list[BaseException | object] = []

    def admit() -> None:
        with database.transaction(write=True) as connection:
            queue.consume_in(connection, ticket)
            transaction_entered.set()
            assert release_transaction.wait(timeout=2)

    def cancel() -> None:
        try:
            cancel_result.append(queue.cancel("run-admitting", expected_sequence=ticket.sequence))
        except BaseException as exc:
            cancel_result.append(exc)

    admit_thread = threading.Thread(target=admit)
    admit_thread.start()
    assert transaction_entered.wait(timeout=1)
    cancel_thread = threading.Thread(target=cancel)
    cancel_thread.start()
    time.sleep(0.05)
    assert cancel_thread.is_alive()

    release_transaction.set()
    admit_thread.join(timeout=1)
    cancel_thread.join(timeout=1)

    assert len(cancel_result) == 1
    assert isinstance(cancel_result[0], ExecutorQueueEntryNotFound)
    assert queue.snapshot().entries == ()
    with database.transaction() as connection:
        receipt = connection.execute(
            "SELECT * FROM run_scheduling_receipts WHERE fleet_run_id = ?",
            ("run-admitting",),
        ).fetchone()
    assert receipt is not None
    assert receipt["queue_seq"] == ticket.sequence
    assert receipt["admitted_priority"] == "NORMAL"
    assert receipt["scheduling_scope_id"] == "local-default"


def test_cancel_unknown_queue_identity_is_not_a_run_cancellation(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    with pytest.raises(ExecutorQueueEntryNotFound, match="No queued Fleet admission"):
        queue.cancel("already-admitted-or-unknown", expected_sequence=1)


def test_stale_cancel_cannot_target_replacement_queue_entry(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    first = queue.enter("run-reused", max_entries=64)
    queue.leave(first)
    replacement = queue.enter("run-reused", max_entries=64)

    from oh_goon.core.errors import StateConflict
    with pytest.raises(StateConflict, match="queue entry changed"):
        queue.cancel("run-reused", expected_sequence=first.sequence)

    assert queue.snapshot().entries[0].sequence == replacement.sequence
    assert queue.snapshot().entries[0].state == "waiting"
    queue.leave(replacement)


def test_concurrent_enqueue_assigns_unique_durable_fifo_age(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    barrier = threading.Barrier(9)
    tickets = []
    failures: list[BaseException] = []

    def enqueue(index: int) -> None:
        try:
            barrier.wait()
            tickets.append(queue.enter(f"concurrent-{index}", max_entries=64))
        except BaseException as exc:
            failures.append(exc)

    workers = [threading.Thread(target=enqueue, args=(index,)) for index in range(8)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(timeout=2)

    assert failures == []
    assert len(tickets) == 8
    assert len({ticket.sequence for ticket in tickets}) == 8
    entries = queue.snapshot().entries
    assert [entry.sequence for entry in entries] == sorted(entry.sequence for entry in entries)


def test_restart_reclaims_exact_dead_owner_ticket_without_reenqueueing(tmp_path: Path) -> None:
    path = tmp_path / "restart.db"
    database = LocalDatabase(path)
    queue = ExecutorPlacementQueue(database)
    ticket = queue.enter("restart-me", max_entries=64)

    from oh_goon.core.models.scheduling import SchedulingPriority

    queue.reprioritize(
        "restart-me",
        admitted_priority=SchedulingPriority.HIGH,
        expected_revision=0,
        reason="durable priority before restart",
    )
    with database.transaction(write=True, track_revision=False) as connection:
        connection.execute(
            "UPDATE executor_placement_queue "
            "SET owner_session_id='dead-session', owner_pid=999999999, owner_identity='dead-owner' "
            "WHERE fleet_run_id='restart-me'"
        )

    restarted = ExecutorPlacementQueue(LocalDatabase(path))
    replay = restarted.enter("restart-me", max_entries=64)
    entry = restarted.snapshot().entries[0]

    assert replay.sequence == ticket.sequence
    assert entry.sequence == ticket.sequence
    assert entry.scheduling_revision == 1
    assert entry.admitted_priority is SchedulingPriority.HIGH


def test_cancel_and_reprioritize_race_never_restores_runnable_authority(tmp_path: Path) -> None:
    _, queue = _queue(tmp_path)
    ticket = queue.enter("race", max_entries=64)
    barrier = threading.Barrier(3)
    results: list[object] = []

    from oh_goon.core.models.scheduling import SchedulingPriority

    def cancel() -> None:
        barrier.wait()
        try:
            results.append(queue.cancel("race", expected_sequence=ticket.sequence))
        except BaseException as exc:
            results.append(exc)

    def reprioritize() -> None:
        barrier.wait()
        try:
            results.append(
                queue.reprioritize(
                    "race",
                    admitted_priority=SchedulingPriority.HIGH,
                    expected_revision=0,
                    reason="concurrent escalation",
                )
            )
        except BaseException as exc:
            results.append(exc)

    cancel_thread = threading.Thread(target=cancel)
    reprioritize_thread = threading.Thread(target=reprioritize)
    cancel_thread.start()
    reprioritize_thread.start()
    barrier.wait()
    cancel_thread.join(timeout=2)
    reprioritize_thread.join(timeout=2)

    assert len(results) == 2
    with pytest.raises(ExecutorAdmissionCancelled):
        queue.is_turn(ticket)
    assert queue.snapshot().entries[0].state == "cancellation_requested"
