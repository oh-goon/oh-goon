from __future__ import annotations

import multiprocessing
from pathlib import Path
from queue import Empty

import pytest

from oh_goon.integrations.source_snapshot import snapshot_source_directory, source_snapshot_identity
from oh_goon.runtime.database import LocalDatabase, inspect_database_path
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.local_state_maintenance import LocalStateCompatibility


def _cross_process_sqlite_observer(database_path: str, iterations: int, result_queue) -> None:
    """Independent process repeatedly reopens/reads the same admitted DELETE-journal DB."""

    try:
        database = LocalDatabase(Path(database_path), journal_mode="DELETE")
        for index in range(iterations):
            database.durable_revision()
            if index % 25 == 0:
                inspection = inspect_database_path(database.path, quick_check=True)
                if inspection.status is not LocalStateCompatibility.READY:
                    raise RuntimeError(inspection.detail)
        result_queue.put(("ok", iterations))
    except BaseException as exc:  # pragma: no cover - surfaced to parent assertion
        result_queue.put(("error", f"{exc.__class__.__name__}: {exc}"))


def test_real_source_snapshot_pressure_keeps_admitted_sqlite_identity_healthy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the real hash/copy preparation loop while SQLite owns cancellation reads.

    The monotonic clock is compressed so repeated real source-snapshot checkpoints drive
    multiple durable observations without wall-clock sleeps. This reproduces the relevant
    preparation shape before local/certification APP launch instead of merely looping SQL.
    """

    import oh_goon.runtime.execution.coordinator as coordinator

    source = tmp_path / "large-source"
    destination = tmp_path / "snapshot"
    for group in range(12):
        directory = source / f"group-{group:02d}"
        directory.mkdir(parents=True)
        for index in range(30):
            (directory / f"file-{index:03d}.txt").write_bytes(
                (f"{group}:{index}:".encode("utf-8") + b"x" * 4096)
            )

    database = LocalDatabase(tmp_path / "prelaunch-pressure.db", journal_mode="DELETE")
    execution = ExecutionCoordinator(database)
    ticket = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    session = execution.activate(ticket)

    original_connect = database._connect
    original_observer_open = database._open_admitted_observer_connection
    rw_connects = 0
    observer_opens = 0

    def counted_connect():
        nonlocal rw_connects
        rw_connects += 1
        return original_connect()

    def counted_observer_open():
        nonlocal observer_opens
        observer_opens += 1
        return original_observer_open()

    clock = [1000.0]

    def compressed_monotonic() -> float:
        clock[0] += 0.05
        return clock[0]

    monkeypatch.setattr(database, "_connect", counted_connect)
    monkeypatch.setattr(database, "_open_admitted_observer_connection", counted_observer_open)
    monkeypatch.setattr(coordinator, "monotonic", compressed_monotonic)

    with session.preparation_cancellation_observer() as cancel_requested:
        observed = snapshot_source_directory(
            source,
            destination,
            label="prelaunch preparation pressure",
            cancel_requested=cancel_requested,
        )

    assert observed == source_snapshot_identity(source)
    assert observed == source_snapshot_identity(destination)
    # The compressed clock crosses many 250-ms observation windows. The preparation
    # scope owns exactly one query-only native handle; each due observation still gets
    # a fresh read transaction and full admitted-identity/lease proof. This removes the
    # reconnect surface reproduced by native certification without pinning snapshots.
    assert observer_opens == 1
    assert rw_connects == 0
    inspection = inspect_database_path(
        database.path,
        integrity_check=True,
        foreign_key_check=True,
    )
    assert inspection.status is LocalStateCompatibility.READY
    session.finish(lambda _connection: None)


def test_many_fresh_runtime_connections_preserve_delete_journal_identity(tmp_path: Path) -> None:
    """Keep the ordinary fresh-open graph under pressure beyond historical smoke coverage.

    Certification can cross hundreds of durable cuts. This test deliberately avoids the
    execution connection scope so every durable_revision call exercises the exact fresh
    admitted RW connection path that reports SQLITE_NOTADB when storage observation fails.
    """

    database = LocalDatabase(tmp_path / "fresh-open-pressure.db", journal_mode="DELETE")
    for index in range(600):
        if index % 20 == 0:
            with database.transaction(write=True) as connection:
                connection.execute(
                    "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
                    (f"/tmp/fresh-open-{index}", index + 1, "2026-09-09T00:00:00Z"),
                )
        else:
            assert database.durable_revision() >= 0

    inspection = inspect_database_path(
        database.path,
        integrity_check=True,
        foreign_key_check=True,
    )
    assert inspection.status is LocalStateCompatibility.READY


@pytest.mark.process
def test_cross_process_observer_pressure_preserves_admitted_sqlite_owner(
    tmp_path: Path,
) -> None:
    """Use a real child process while the parent holds and exercises an execution lease."""

    database = LocalDatabase(tmp_path / "cross-process-pressure.db", journal_mode="DELETE")
    execution = ExecutionCoordinator(database)
    ticket = execution.reserve_direct_with(
        expected_durable_revision=database.durable_revision(),
        mutate=lambda _connection: None,
    )[0]
    session = execution.activate(ticket)

    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    process = context.Process(
        target=_cross_process_sqlite_observer,
        args=(str(database.path), 150, result_queue),
    )
    process.start()
    try:
        # Alternate exact owner reads with BEGIN IMMEDIATE owner transactions while the
        # independent observer repeatedly opens and reads the same SQLite file.
        for _ in range(60):
            assert (
                session.observe(lambda connection: connection.execute("SELECT 1").fetchone()[0])
                == 1
            )
            assert (
                session.mutate(lambda connection: connection.execute("SELECT 1").fetchone()[0]) == 1
            )
        process.join(timeout=30)
        assert process.exitcode == 0
        try:
            status, detail = result_queue.get(timeout=2)
        except Empty as exc:  # pragma: no cover - diagnostic guard
            raise AssertionError("cross-process SQLite observer returned no result") from exc
        assert (status, detail) == ("ok", 150)
        inspection = inspect_database_path(
            database.path,
            integrity_check=True,
            foreign_key_check=True,
        )
        assert inspection.status is LocalStateCompatibility.READY
        session.assert_owner()
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if not session.finished:
            session.finish(lambda _connection: None)
