"""B7 native-process scheduler/cancel versus original signed custody, test key only.

These workers invoke production startup/cancel service methods with real SQLite
BEGIN IMMEDIATE connections and real PID/birth witnesses. No real issuer/root,
physical LACE cleanup, APP PASS or WSL platform is established by these tests.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.runtime.execution.coordinator import ExecutionCoordinator
from oh_goon.runtime.execution.lease_environment import SystemLeaseEnvironment
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.integration.test_b12d2c3b6_native_restart_witness import (
    _join,
    _replacement_worker,
    _stop_old,
    original_native_owner,  # noqa: F401 - dependent test fixture
)
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c3b7_scheduler_reclaim_fence import FakeJournal

pytestmark = pytest.mark.skipif(
    not Path("/proc/sys/kernel/random/boot_id").is_file(),
    reason="real Linux process identity required; no fallback",
)


def _generic_writer(path: str, start, result, mode: str):
    if start is not None:
        if not start.poll(12):
            result.send(("ERROR", "no synchronized start"))
            return
        start.recv()
        start.close()
    connection = sqlite3.connect(path, timeout=8, isolation_level=None)
    connection.row_factory = sqlite3.Row

    class OriginalOwner:
        def __init__(self):
            self.path = Path(path)
            self.session_id = f"b7-generic-{os.getpid()}"

        @contextmanager
        def transaction(self, *, write=False):
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")

    original = OriginalOwner()
    coordinator = ExecutionCoordinator(original, lease_environment=SystemLeaseEnvironment())
    fake = FakeJournal()
    try:
        if mode == "startup":
            RepositoryEffectRecovery(
                original, None, fake, coordinator, None, None
            )._terminalize_interrupted_fleet()
        else:
            FleetRunLifecycle(original, fake, coordinator, None).cancel("run-one")
        result.send(("ERROR", "generic reclaim unexpectedly succeeded"))
    except StateConflict as exc:
        result.send(("BLOCKED", str(exc)))
    except Exception as exc:
        result.send(("ERROR", f"{type(exc).__name__}: {exc}"))
    finally:
        result.close()
        connection.close()


@pytest.mark.parametrize("mode", ["startup", "cancel"])
def test_independent_generic_writer_cannot_erase_original_dead_lease(
    original_native_owner,  # noqa: F811 - pytest fixture injection
    tmp_path,
    mode,
):
    ctx, _connection, _journal, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    read, write = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_generic_writer, args=(str(path), None, write, mode))
    proc.start()
    write.close()
    try:
        _join(proc)
        assert proc.exitcode == 0
        assert read.poll(5)
        status, message = read.recv()
        assert status == "BLOCKED", message
        with sqlite3.connect(path) as check:
            assert (
                check.execute("SELECT lease_token FROM execution_lease").fetchone()[0]
                == "lease-one"
            )
            assert (
                CausalRunJournal(check).verify("run-one", auth=authenticator())[-1]["kind"]
                != "RUN_RECOVERY_CUSTODY_TRANSFERRED"
            )
    finally:
        read.close()


@pytest.mark.parametrize("mode", ["startup", "cancel"])
def test_generic_writer_racing_signed_recovery_must_lose(original_native_owner, tmp_path, mode):  # noqa: F811 - pytest fixture injection
    ctx, _, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    work = []
    for fn, args in [(_generic_writer, (str(path), mode)), (_replacement_worker, (str(path),))]:
        read, write = ctx.Pipe(duplex=False)
        start_read, trigger = ctx.Pipe(duplex=False)
        if fn is _generic_writer:
            proc = ctx.Process(target=fn, args=(args[0], start_read, write, args[1]))
        else:
            proc = ctx.Process(target=fn, args=(args[0], start_read, write))
        work.append((proc, read, write, start_read, trigger))
    try:
        for proc, *_ in work:
            proc.start()
        for _, _, write, start_read, _ in work:
            write.close()
            start_read.close()
        for _, _, _, _, trigger in work:
            trigger.send("go")
            trigger.close()
        for proc, *_ in work:
            _join(proc)
            assert proc.exitcode == 0
        results = []
        for _, read, *_ in work:
            assert read.poll(5)
            results.append(read.recv())
        assert results[0][0] == "BLOCKED", results
        assert results[1][0] == "TRANSFERRED", results
        with sqlite3.connect(path) as check:
            lease = check.execute(
                "SELECT lease_token,owner_session_id,cancel_requested_at FROM execution_lease"
            ).fetchone()
            events = CausalRunJournal(check).verify("run-one", auth=authenticator())
            assert lease[0] == results[1][1]
            assert lease[1].startswith("recovery:")
            assert lease[2] is None
            assert [e["kind"] for e in events].count("RUN_RECOVERY_CUSTODY_TRANSFERRED") == 1
    finally:
        for proc, read, _write, _start_read, _trigger in work:
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
            read.close()
