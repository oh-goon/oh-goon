from __future__ import annotations

import multiprocessing
import os
import sqlite3
import time
from pathlib import Path
from queue import Empty
from threading import Event, Thread

import pytest

from oh_goon.runtime import _sqlite_authority_gate as gate_module
from oh_goon.runtime._database_policy import inspect_database_path
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.local_state_maintenance import LocalStateCompatibility, reset_local_state
from oh_goon.runtime.storage_health import probe_sqlite_prelaunch


def _child_write_gate(path_text: str, queue: multiprocessing.Queue[str]) -> None:
    queue.put("started")
    with sqlite_authority_gate(Path(path_text), write=True):
        queue.put("acquired")


def _wait_queue(queue: multiprocessing.Queue[str], timeout: float) -> str | None:
    try:
        return queue.get(timeout=timeout)
    except Empty:
        return None


def test_independent_writer_blocks_observer_cut_without_losing_query_only(tmp_path: Path) -> None:
    path = tmp_path / "state" / "authority.db"
    writer = LocalDatabase(path, journal_mode="DELETE")
    observer = LocalDatabase(path, journal_mode="DELETE")
    entered = Event()
    release = Event()
    observed: list[int] = []

    def write() -> None:
        with writer.transaction(write=True) as connection:
            connection.execute("UPDATE state_metadata SET durable_revision = durable_revision WHERE singleton = 1")
            entered.set()
            assert release.wait(2)

    def observe() -> None:
        with observer.admitted_observer_connection() as connection:
            observed.append(int(connection.execute("PRAGMA query_only").fetchone()[0]))

    writer_thread = Thread(target=write)
    observer_thread = Thread(target=observe)
    writer_thread.start()
    assert entered.wait(2)
    observer_thread.start()
    time.sleep(0.05)
    assert observed == []
    release.set()
    writer_thread.join(2)
    observer_thread.join(2)
    assert not writer_thread.is_alive()
    assert not observer_thread.is_alive()
    assert observed == [1]


def test_runtime_identity_proof_occurs_inside_transaction_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state" / "snapshot.db", journal_mode="DELETE")
    original = database._prove_admitted_sqlite_identity
    observations: list[tuple[str, bool]] = []

    def prove(connection: sqlite3.Connection, *, context: str) -> None:
        observations.append((context, connection.in_transaction))
        original(connection, context=context)

    monkeypatch.setattr(database, "_prove_admitted_sqlite_identity", prove)
    with database.transaction():
        pass
    with database.execution_connection_scope():
        with database.transaction():
            pass

    assert ("runtime transaction", True) in observations
    assert ("scoped runtime transaction", True) in observations


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork authority applies on POSIX")
def test_forked_child_rebinds_inherited_gate_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "fork.db"
    path.parent.mkdir(parents=True)
    queue: multiprocessing.Queue[str] = multiprocessing.get_context("fork").Queue()

    with sqlite_authority_gate(path, write=False):
        process = multiprocessing.get_context("fork").Process(
            target=_child_write_gate, args=(str(path), queue)
        )
        process.start()
        assert _wait_queue(queue, 1) == "started"
        assert _wait_queue(queue, 0.1) is None

    assert _wait_queue(queue, 2) == "acquired"
    process.join(2)
    assert process.exitcode == 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork authority applies on POSIX")
def test_forked_child_drops_gate_descriptor_owned_by_parent_thread(tmp_path: Path) -> None:
    path = tmp_path / "state" / "fork-thread.db"
    path.parent.mkdir(parents=True)
    entered = Event()
    release = Event()

    def hold_parent_gate() -> None:
        with sqlite_authority_gate(path, write=False):
            entered.set()
            assert release.wait(3)

    holder = Thread(target=hold_parent_gate)
    holder.start()
    assert entered.wait(2)
    queue: multiprocessing.Queue[str] = multiprocessing.get_context("fork").Queue()
    process = multiprocessing.get_context("fork").Process(
        target=_child_write_gate, args=(str(path), queue)
    )
    process.start()
    assert _wait_queue(queue, 1) == "started"
    assert _wait_queue(queue, 0.1) is None
    release.set()
    holder.join(2)
    assert _wait_queue(queue, 2) == "acquired"
    process.join(2)
    assert process.exitcode == 0


def test_gate_acquisition_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state" / "bounded.db"
    path.parent.mkdir(parents=True)
    entered = Event()
    release = Event()

    def hold_write() -> None:
        with sqlite_authority_gate(path, write=True):
            entered.set()
            assert release.wait(2)

    holder = Thread(target=hold_write)
    holder.start()
    assert entered.wait(2)
    monkeypatch.setattr(gate_module, "_GATE_ACQUIRE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(gate_module, "_GATE_RETRY_SECONDS", 0.005)
    try:
        with pytest.raises(TimeoutError, match="authority gate remained busy"):
            with sqlite_authority_gate(path, write=False):
                pass
    finally:
        release.set()
        holder.join(2)


def test_inspection_and_reset_join_runtime_authority_gate(tmp_path: Path) -> None:
    path = tmp_path / "state" / "maintenance.db"
    LocalDatabase(path, journal_mode="DELETE")
    inspected: list[LocalStateCompatibility] = []
    deleted: list[tuple[Path, ...]] = []

    with sqlite_authority_gate(path, write=True):
        inspection = Thread(
            target=lambda: inspected.append(inspect_database_path(path).status)
        )
        inspection.start()
        time.sleep(0.05)
        assert inspected == []
    inspection.join(2)
    assert inspected == [LocalStateCompatibility.READY]

    with sqlite_authority_gate(path, write=False):
        reset = Thread(target=lambda: deleted.append(reset_local_state(path)))
        reset.start()
        time.sleep(0.05)
        assert deleted == []
    reset.join(2)
    assert deleted and path in deleted[0]
    assert not path.exists()


def test_prelaunch_probe_joins_runtime_write_authority(tmp_path: Path) -> None:
    path = tmp_path / "state" / "prelaunch.db"
    LocalDatabase(path, journal_mode="DELETE")
    completed = Event()

    def probe() -> None:
        probe_sqlite_prelaunch(path)
        completed.set()

    with sqlite_authority_gate(path, write=False):
        thread = Thread(target=probe)
        thread.start()
        time.sleep(0.05)
        assert not completed.is_set()
    thread.join(2)
    assert completed.is_set()
