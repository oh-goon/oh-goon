"""B6: REAL OS process-death and independent SQLite worker *development* witness.

Uses an explicitly synthetic deterministic test signer and no physical LACE effects.
No assertion here is a provisioned Fleet Enforcer or native containment proof.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.runtime.enforcer_lace_restart_transfer import commit_original_restart_transfer_in
from oh_goon.runtime.execution.coordinator import ExecutionCoordinator
from oh_goon.runtime.execution.lease_environment import SystemLeaseEnvironment
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c3b5_atomic_restart_transfer import seeded

pytestmark = pytest.mark.skipif(
    not Path("/proc/sys/kernel/random/boot_id").is_file(),
    reason="real Linux process identity required; no synthetic process fallback",
)


def _live_predecessor(ready):
    # No effects, sockets, workers or cleanup: a real OS process only.
    ready.send(os.getpid())
    ready.close()
    time.sleep(20)


def _replacement_worker(
    path: str,
    start,
    result,
    *,
    crash_after_commit=False,
    crash_before_commit=False,
    wrong_generation=False,
):
    """Separate OS process / SQLite connection, original transfer function only."""
    if start is not None:
        if not start.poll(12):
            result.send(("BLOCKED", "start signal unavailable"))
            return
        start.recv()
        start.close()
    connection = sqlite3.connect(path, timeout=7, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        ticket = commit_original_restart_transfer_in(
            journal=CausalRunJournal(connection),
            coordinator=ExecutionCoordinator(
                SimpleNamespace(path=Path(path), session_id=f"b6-worker-{os.getpid()}"),
                lease_environment=SystemLeaseEnvironment(),
            ),
            run_id="run-one",
            predecessor_token="lease-one",
            expected_epoch=1,
            auth=authenticator(),
            admitted_issuer_key_id="key-1",
            admitted_generation=9 if wrong_generation else 8,
            admitted_trust_epoch=0,
        )
        if crash_before_commit:
            # Abandon a live SQLite transaction without COMMIT; OS must roll back.
            os._exit(38)
        connection.execute("COMMIT")
        if crash_after_commit:
            # Only the test-owned worker dies, *after* its SQLite commit.
            os._exit(37)
        result.send(("TRANSFERRED", ticket._lease.token))
    except (Exception, sqlite3.Error) as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        result.send(("REJECTED", type(exc).__name__))
    finally:
        result.close()
        connection.close()


@pytest.fixture
def original_native_owner(tmp_path):
    connection, journal, _, _ = seeded(tmp_path)
    ctx = mp.get_context("forkserver")
    recv, send = ctx.Pipe(duplex=False)
    predecessor = ctx.Process(target=_live_predecessor, args=(send,))
    predecessor.start()
    send.close()
    try:
        assert recv.poll(12), "predecessor process did not start"
        pid = recv.recv()
        host = SystemLeaseEnvironment()
        birth = host.process_identity(pid)
        if not (host.process_alive(pid) is True and isinstance(birth, str) and birth):
            pytest.skip("original host cannot positively prove process birth and liveness")
        connection.execute(
            "UPDATE execution_lease SET owner_session_id=?,owner_pid=?,owner_identity=? "
            "WHERE singleton=1 AND lease_token='lease-one'",
            ("b6-original-owner", pid, birth),
        )
        yield ctx, connection, journal, predecessor
    finally:
        if predecessor.is_alive():
            predecessor.terminate()
        predecessor.join(timeout=6)
        if predecessor.is_alive():
            predecessor.kill()
            predecessor.join(timeout=6)
        recv.close()
        connection.close()


def _join(process, timeout=15):
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("replacement did not terminate; no indefinite wait")


def _attempt(ctx, path, *, crash=False, crash_before=False, wrong_generation=False):
    read, write = ctx.Pipe(duplex=False)
    worker = ctx.Process(
        target=_replacement_worker,
        args=(str(path), None, write),
        kwargs={
            "crash_after_commit": crash,
            "crash_before_commit": crash_before,
            "wrong_generation": wrong_generation,
        },
    )
    worker.start()
    write.close()
    try:
        _join(worker)
        if crash or crash_before:
            assert worker.exitcode == (38 if crash_before else 37)
            return None
        assert worker.exitcode == 0
        assert read.poll(5), "worker produced no result"
        return read.recv()
    finally:
        read.close()


def _durable_view(path):
    # Read-only rediscovery of the ORIGINAL SQLite authority, never a new store.
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        lease = connection.execute(
            "SELECT lease_token,owner_session_id,owner_pid FROM execution_lease"
        ).fetchone()
        authority = connection.execute(
            "SELECT control_epoch,lease_identity FROM lace_run_authority WHERE run_id='run-one'"
        ).fetchone()
        events = CausalRunJournal(connection).verify("run-one", auth=authenticator())
        return tuple(lease), tuple(authority), [e["kind"] for e in events]
    finally:
        connection.close()


def _stop_old(predecessor):
    predecessor.terminate()
    _join(predecessor)
    assert predecessor.exitcode is not None
    # Native death proof is a positive result from the real OS probe.
    assert SystemLeaseEnvironment().process_alive(predecessor.pid) is False


def test_real_live_predecessor_is_never_taken_over(original_native_owner, tmp_path):
    ctx, _, _, predecessor = original_native_owner
    before = _durable_view(tmp_path / "original.sqlite")
    assert _attempt(ctx, tmp_path / "original.sqlite")[0] == "REJECTED"
    assert predecessor.is_alive()
    assert _durable_view(tmp_path / "original.sqlite") == before


def test_real_dead_predecessor_signed_transfer_and_read_only_rediscovery(
    original_native_owner, tmp_path
):
    ctx, _, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    status, token = _attempt(ctx, path)
    assert status == "TRANSFERRED" and token != "lease-one"
    lease, authority, kinds = _durable_view(path)
    assert lease[0] == authority[1] == token
    assert lease[1].startswith("recovery:b6-worker-")
    assert authority[0] == 2
    assert kinds[-1] == "RUN_RECOVERY_CUSTODY_TRANSFERRED"
    assert kinds.count("RUN_RECOVERY_CUSTODY_TRANSFERRED") == 1
    # Worker has exited; no fresh executive or cleanup permission is inferred.
    assert SystemLeaseEnvironment().process_alive(lease[2]) is False
    assert _attempt(ctx, path)[0] == "REJECTED"
    assert _durable_view(path) == (lease, authority, kinds)


def test_real_process_concurrency_one_winner_one_rejection(original_native_owner, tmp_path):
    ctx, _, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    reads, writes, starts, triggers, workers = [], [], [], [], []
    for _ in range(2):
        read, write = ctx.Pipe(duplex=False)
        start_read, start_write = ctx.Pipe(duplex=False)
        worker = ctx.Process(target=_replacement_worker, args=(str(path), start_read, write))
        reads.append(read)
        writes.append(write)
        starts.append(start_read)
        triggers.append(start_write)
        workers.append(worker)
    for worker in workers:
        worker.start()
    for write, start_read in zip(writes, starts, strict=False):
        write.close()
        start_read.close()
    for trigger in triggers:
        trigger.send("go")
        trigger.close()
    for worker in workers:
        _join(worker)
        assert worker.exitcode == 0
    outcomes = []
    for read in reads:
        assert read.poll(5)
        outcomes.append(read.recv()[0])
        read.close()
    assert sorted(outcomes) == ["REJECTED", "TRANSFERRED"]
    lease, authority, kinds = _durable_view(path)
    assert lease[0] == authority[1] and authority[0] == 2
    assert kinds.count("RUN_RECOVERY_CUSTODY_TRANSFERRED") == 1


def test_crash_after_commit_is_durable_and_cannot_replay_old_owner(original_native_owner, tmp_path):
    ctx, _, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    _attempt(ctx, path, crash=True)
    state = _durable_view(path)
    assert state[1][0] == 2
    assert state[2].count("RUN_RECOVERY_CUSTODY_TRANSFERRED") == 1
    assert _attempt(ctx, path)[0] == "REJECTED"
    assert _durable_view(path) == state


def test_wrong_executor_generation_preserves_original_durable_authority(
    original_native_owner, tmp_path
):
    ctx, _, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    before = _durable_view(path)
    assert _attempt(ctx, path, wrong_generation=True)[0] == "REJECTED"
    assert _durable_view(path) == before


def test_cancelled_predecessor_blocks_native_transfer(original_native_owner, tmp_path):
    ctx, conn, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    conn.execute("UPDATE execution_lease SET cancel_requested_at='2026-10-12T00:00:00+00:00'")
    before = _durable_view(path)
    assert _attempt(ctx, path)[0] == "REJECTED"
    assert _durable_view(path) == before


def test_interrupted_sqlite_transfer_without_commit_rolls_back_exactly(
    original_native_owner, tmp_path
):
    ctx, _, _, predecessor = original_native_owner
    path = tmp_path / "original.sqlite"
    _stop_old(predecessor)
    before = _durable_view(path)
    _attempt(ctx, path, crash_before=True)
    assert _durable_view(path) == before
    status, _ = _attempt(ctx, path)
    assert status == "TRANSFERRED"
    assert _durable_view(path)[2].count("RUN_RECOVERY_CUSTODY_TRANSFERRED") == 1
