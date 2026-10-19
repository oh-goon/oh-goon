"""B5 regression-first: original dead-owner cut, signed successor, never Step authority."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import OwnershipLost
from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_restart_transfer import commit_original_restart_transfer_in
from oh_goon.runtime.execution.coordinator import ExecutionCoordinator
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c1_cleanup_preparation import seeded as predecessor_seeded


@dataclass
class Host:
    predecessor_live: bool | None = False
    new_live: bool | None = True
    new_birth: str | None = "new-birth"

    def process_id(self):
        return 99

    def process_alive(self, pid):
        return self.predecessor_live if pid == 41 else self.new_live

    def process_identity(self, pid):
        return "old-birth" if pid == 41 else self.new_birth

    def now(self):
        return datetime(2026, 10, 12, tzinfo=UTC)


def seeded(tmp_path: Path):
    path = tmp_path / "original.sqlite"
    conn, journal, previous_owner, _ = predecessor_seeded(tmp_path)
    conn.row_factory = sqlite3.Row
    previous_owner.reconcile_crash(
        intent_run_seq=2,
        native_observer=lambda sub: LocalLaceCrashObservation(
            sub.subject_identity, "PRESENT_MATCHING"
        ),
    )
    conn.execute("""CREATE TABLE execution_lease (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), lease_token TEXT UNIQUE,
        owner_session_id TEXT, owner_pid INTEGER, owner_identity TEXT,
        kind TEXT, fleet_run_id TEXT, acquired_at TEXT, cancel_requested_at TEXT)""")
    conn.execute(
        "INSERT INTO execution_lease VALUES (1,?,?,?,?,?,?,?,NULL)",
        ("lease-one", "old-session", 41, "old-birth", "FLEET", "run-one", "before"),
    )
    env = Host()
    owner = SimpleNamespace(path=path, session_id="new-session")

    @contextmanager
    def transaction(*, write=False):
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    owner.transaction = transaction
    coordinator = ExecutionCoordinator(owner, lease_environment=env)
    return conn, journal, coordinator, env


def transfer(
    conn,
    journal,
    coordinator,
    *,
    epoch=1,
    token="lease-one",
    signer=None,
    generation=8,
    trust_epoch=0,
):
    conn.execute("BEGIN IMMEDIATE")
    try:
        ticket = commit_original_restart_transfer_in(
            journal=journal,
            coordinator=coordinator,
            run_id="run-one",
            predecessor_token=token,
            expected_epoch=epoch,
            auth=signer or authenticator(),
            admitted_generation=generation,
            admitted_trust_epoch=trust_epoch,
            admitted_issuer_key_id="key-1",
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
        return ticket


def test_atomic_transfer_persists_signed_custody_but_cannot_execute(tmp_path):
    conn, journal, coordinator, env = seeded(tmp_path)
    ticket = transfer(conn, journal, coordinator)
    assert ticket.recovery_only
    old = conn.execute(
        "SELECT lease_token,owner_session_id,owner_identity FROM execution_lease"
    ).fetchone()
    assert tuple(old) == (ticket._lease.token, "recovery:new-session", "new-birth")
    row = journal._row("run-one")
    assert (row[1], row[2]) == (2, ticket._lease.token)
    events = journal.verify("run-one", auth=authenticator())
    signed = events[-1]
    assert signed["kind"] == "RUN_RECOVERY_CUSTODY_TRANSFERRED"
    assert signed["payload"]["binding"]["predecessor_reconciliation"] == "UNRESOLVED"
    assert signed["payload"]["binding"]["effect_permission"] == "NONE"
    with pytest.raises(OwnershipLost, match="recovery custody"):
        coordinator.activate(ticket)
    conn.close()
    # Independence from the issuing process's SQLite connection.
    reopened = sqlite3.connect(tmp_path / "original.sqlite", isolation_level=None)
    assert CausalRunJournal(reopened).verify("run-one", auth=authenticator())[-1] == signed
    reopened.close()


@pytest.mark.parametrize(
    "attack",
    [
        "live",
        "unobservable",
        "pid-reuse",
        "wrong-epoch",
        "foreign-token",
        "wrong-generation",
        "wrong-issuer",
        "cancel",
        "old-birth-unknown",
        "new-birth-unknown",
        "new-process-dead",
        "changed-session",
    ],
)
def test_transfer_fails_closed_without_any_committed_mutation(tmp_path, attack):
    conn, journal, coordinator, env = seeded(tmp_path)
    if attack == "live":
        env.predecessor_live = True
    if attack == "unobservable":
        env.predecessor_live = None
    if attack == "pid-reuse":
        env.predecessor_live = True  # PID alive with changed birth: never reclaim
    if attack == "cancel":
        conn.execute("UPDATE execution_lease SET cancel_requested_at='now'")
    if attack == "old-birth-unknown":
        conn.execute("UPDATE execution_lease SET owner_identity=NULL")
    if attack == "new-birth-unknown":
        env.new_birth = None
    if attack == "new-process-dead":
        env.new_live = False
    if attack == "changed-session":
        conn.execute("UPDATE execution_lease SET owner_session_id='new-session'")
    before = conn.execute("SELECT * FROM execution_lease").fetchall()
    original = tuple(journal._row("run-one"))
    events = journal.verify("run-one", auth=authenticator())
    with pytest.raises((OwnershipLost, CausalJournalError, TrustProtocolError)):
        transfer(
            conn,
            journal,
            coordinator,
            epoch=2 if attack == "wrong-epoch" else 1,
            token="foreign" if attack == "foreign-token" else "lease-one",
            generation=9 if attack == "wrong-generation" else 8,
            signer=authenticator("foreign") if attack == "wrong-issuer" else None,
        )
    assert conn.execute("SELECT * FROM execution_lease").fetchall() == before
    assert tuple(journal._row("run-one")) == original
    assert journal.verify("run-one", auth=authenticator()) == events
    conn.close()


def test_second_takeover_of_stale_predecessor_rolls_back(tmp_path):
    conn, journal, coordinator, env = seeded(tmp_path)
    ticket = transfer(conn, journal, coordinator)
    before = conn.execute("SELECT lease_token FROM execution_lease").fetchone()
    with pytest.raises((OwnershipLost, CausalJournalError)):
        transfer(conn, journal, coordinator)
    assert conn.execute("SELECT lease_token FROM execution_lease").fetchone() == before
    assert journal._row("run-one")[2] == ticket._lease.token
    conn.close()


def test_signed_custody_does_not_accept_modified_payload_or_mac(tmp_path):
    conn, journal, coordinator, env = seeded(tmp_path)
    transfer(conn, journal, coordinator)
    original = conn.execute(
        "SELECT event_payload FROM lace_run_events WHERE event_kind='RUN_RECOVERY_CUSTODY_TRANSFERRED'"
    ).fetchone()[0]
    # A mutated payload + original chain identity is detected before signature check.
    conn.execute(
        "UPDATE lace_run_events SET event_payload=? WHERE event_kind='RUN_RECOVERY_CUSTODY_TRANSFERRED'",
        (original.replace(b"NONE", b"ROOT"),),
    )
    with pytest.raises((CausalJournalError, TrustProtocolError)):
        journal.verify("run-one", auth=authenticator())
    conn.close()


def test_atomic_rollback_after_transfer_issuer_commit_failure(tmp_path, monkeypatch):
    conn, journal, coordinator, env = seeded(tmp_path)
    original = conn.execute("SELECT * FROM execution_lease").fetchall()
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal as Journal

    def fail(_self, *args, **kwargs):
        raise RuntimeError("commit event failed")

    monkeypatch.setattr(Journal, "_append_locked", fail)
    with pytest.raises(RuntimeError, match="commit event"):
        transfer(conn, journal, coordinator)
    assert conn.execute("SELECT * FROM execution_lease").fetchall() == original
    assert (journal._row("run-one")[1], journal._row("run-one")[2]) == (1, "lease-one")
    conn.close()


def test_original_intent_without_predecessor_reconciliation_cannot_transfer(tmp_path):
    from tests.unit.runtime.test_b12_local_resource_intent import _reserve, _store

    conn, journal = _store(tmp_path / "original.sqlite")
    _reserve(journal)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE execution_lease (
        singleton INTEGER PRIMARY KEY, lease_token TEXT UNIQUE, owner_session_id TEXT,
        owner_pid INTEGER, owner_identity TEXT, kind TEXT, fleet_run_id TEXT,
        acquired_at TEXT, cancel_requested_at TEXT)""")
    conn.execute(
        "INSERT INTO execution_lease VALUES (1,'lease-one','old-session',41,"
        "'old-birth','FLEET','run-one','before',NULL)"
    )
    coordinator = ExecutionCoordinator(
        SimpleNamespace(path=tmp_path / "original.sqlite", session_id="new-session"),
        lease_environment=Host(),
    )
    before = tuple(journal._row("run-one"))
    lease = conn.execute("SELECT * FROM execution_lease").fetchone()
    with pytest.raises(CausalJournalError, match="unreconciled predecessor"):
        transfer(conn, journal, coordinator)
    assert tuple(journal._row("run-one")) == before
    assert conn.execute("SELECT * FROM execution_lease").fetchone() == lease
    assert journal.verify("run-one", auth=authenticator())[-1]["kind"] == "RESOURCE_INTENT"
    conn.close()


def test_recovery_ticket_cannot_be_relabelled_as_execution_ticket(tmp_path):
    conn, journal, coordinator, _ = seeded(tmp_path)
    custody = transfer(conn, journal, coordinator)
    from oh_goon.runtime.execution.coordinator import ExecutionTicket

    relabelled = ExecutionTicket(custody._lease, recovery_only=False)
    # Same lease token cannot bypass the recovery marker held by the original store.
    with pytest.raises(OwnershipLost, match="recovery custody"):
        coordinator.activate(relabelled)
    conn.close()


def test_process_liveness_permission_denied_is_not_dead_owner_proof(monkeypatch):
    from oh_goon.runtime.execution import lease_environment as authority

    host = authority.SystemLeaseEnvironment()
    monkeypatch.setattr(authority, "observe_process_liveness", lambda pid: None)

    def denied(pid, signal):
        raise PermissionError("EPERM")

    monkeypatch.setattr(authority.os, "kill", denied)
    assert host.process_alive(41) is True

    def absent(pid, signal):
        raise ProcessLookupError("ESRCH")

    monkeypatch.setattr(authority.os, "kill", absent)
    assert host.process_alive(41) is False


def test_recovery_session_only_allows_recovery_scoped_sqlite_cuts(tmp_path):
    conn, journal, coordinator, _ = seeded(tmp_path)
    ticket = transfer(conn, journal, coordinator)
    session = coordinator.activate_recovery(ticket)
    assert session.recovery_only is True
    assert (
        session.mutate_recovery(lambda connection: CausalRunJournal(connection)._row("run-one")[1])
        == 2
    )
    with pytest.raises(OwnershipLost, match="recovery-only"):
        session.mutate(lambda connection: connection.execute("DELETE FROM lace_run_events"))
    with pytest.raises(OwnershipLost, match="recovery-only"):
        session.finish(lambda connection: connection.execute("DELETE FROM lace_run_authority"))
    assert (
        journal.verify("run-one", auth=authenticator())[-1]["kind"]
        == "RUN_RECOVERY_CUSTODY_TRANSFERRED"
    )
    conn.close()
