from __future__ import annotations

import errno
import multiprocessing
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch

import pytest

from oh_goon.runtime import _sqlite_authority_gate as gate_module
from oh_goon.runtime._database_policy import inspect_database_path
from oh_goon.runtime._sqlite_authority_gate import sqlite_authority_gate
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.local_state_maintenance import LocalStateCompatibility, reset_local_state
from oh_goon.runtime.storage_health import probe_sqlite_prelaunch


def _child_write_gate(
    path_text: str, connection: Connection, inherited_descriptor_state: list[int] | None
) -> None:
    with connection:
        if inherited_descriptor_state is not None:
            assert inherited_descriptor_state == [errno.EBADF]
        connection.send("started")
        with sqlite_authority_gate(Path(path_text), write=True):
            connection.send("acquired")


def _wait_message(connection: Connection, timeout: float) -> str | None:
    return connection.recv() if connection.poll(timeout) else None


@contextmanager
def _fork_writer(
    path: Path, *, inherited_descriptor: int | None = None
) -> Iterator[tuple[BaseProcess, Connection]]:
    context = multiprocessing.get_context("fork")
    reader, writer = context.Pipe(duplex=False)
    inherited_descriptor_state: list[int] | None = None
    if inherited_descriptor is not None:
        inherited_descriptor_state = []

        def observe_after_fork() -> None:
            # Observe immediately after the product callback, before native
            # multiprocessing bootstrap may reuse this descriptor number.
            assert inherited_descriptor is not None
            assert inherited_descriptor_state is not None
            try:
                os.fstat(inherited_descriptor)
            except OSError as error:
                inherited_descriptor_state.append(error.errno or 0)
            else:
                inherited_descriptor_state.append(0)

        os.register_at_fork(after_in_child=observe_after_fork)
    process = context.Process(
        target=_child_write_gate, args=(str(path), writer, inherited_descriptor_state)
    )
    try:
        yield process, reader
    finally:
        with ExitStack() as cleanup:
            cleanup.callback(reader.close)
            cleanup.callback(writer.close)
            try:
                if process.pid is not None:
                    try:
                        if process.is_alive():
                            process.terminate()
                        process.join(2)
                    finally:
                        if process.is_alive():
                            process.kill()
                            process.join(2)
                    assert not process.is_alive(), "forked gate writer did not stop"
            finally:
                if process.pid is None or process.exitcode is not None:
                    process.close()


def _run_fork_scenario(scenario: str, tmp_path: Path) -> None:
    # A fresh interpreter keeps unrelated pytest/service threads out of the
    # process that genuinely forks. The scenario still inherits real gate FDs
    # and Python at-fork callbacks, with deprecations treated as errors.
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-W",
            "error::DeprecationWarning",
            "-c",
            "import runpy, sys; from pathlib import Path; "
            "runpy.run_path(sys.argv[1])[sys.argv[2]](Path(sys.argv[3]))",
            str(Path(__file__).resolve()),
            scenario,
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_independent_writer_blocks_observer_cut_without_losing_query_only(tmp_path: Path) -> None:
    path = tmp_path / "state" / "authority.db"
    writer = LocalDatabase(path, journal_mode="DELETE")
    observer = LocalDatabase(path, journal_mode="DELETE")
    entered = Event()
    release = Event()
    observed: list[int] = []

    def write() -> None:
        with writer.transaction(write=True) as connection:
            connection.execute(
                "UPDATE state_metadata SET durable_revision = durable_revision WHERE singleton = 1"
            )
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
    _run_fork_scenario("_forked_child_rebinds_inherited_gate_state", tmp_path)


def _forked_child_rebinds_inherited_gate_state(tmp_path: Path) -> None:
    path = tmp_path / "state" / "fork.db"
    path.parent.mkdir(parents=True)
    with _fork_writer(path) as (process, reader):
        with sqlite_authority_gate(path, write=False):
            process.start()
            assert _wait_message(reader, 1) == "started"
            assert _wait_message(reader, 0.1) is None

        assert _wait_message(reader, 2) == "acquired"
        process.join(2)
        assert process.exitcode == 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork authority applies on POSIX")
def test_forked_child_drops_gate_descriptor_opened_by_quiescent_parent_worker(
    tmp_path: Path,
) -> None:
    _run_fork_scenario("_forked_child_drops_gate_descriptor_opened_by_parent_worker", tmp_path)


def _forked_child_drops_gate_descriptor_opened_by_parent_worker(tmp_path: Path) -> None:
    path = tmp_path / "state" / "fork-thread.db"
    path.parent.mkdir(parents=True)
    opened: list[int] = []
    native_open = os.open

    def observe_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        descriptor = native_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    with ExitStack() as parent_gate:
        # Retain the real context after its originating thread exits. Its FD
        # and kernel lock remain live, but fork has no live sibling thread.
        with patch.object(os, "open", side_effect=observe_open):
            holder = Thread(
                target=lambda: parent_gate.enter_context(sqlite_authority_gate(path, write=False))
            )
            holder.start()
            holder.join(2)
            assert not holder.is_alive()
        (descriptor,) = opened
        os.fstat(descriptor)
        with _fork_writer(path, inherited_descriptor=descriptor) as (process, reader):
            process.start()
            assert _wait_message(reader, 1) == "started"
            assert _wait_message(reader, 0.1) is None
            parent_gate.close()
            assert _wait_message(reader, 2) == "acquired"
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
        inspection = Thread(target=lambda: inspected.append(inspect_database_path(path).status))
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
