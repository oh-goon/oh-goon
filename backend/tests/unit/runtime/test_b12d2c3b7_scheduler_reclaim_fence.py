"""B7: production startup/cancel writer must not erase original LACE custody.

Tests seed the B5 *test-only* issuer and original SQLite lease; assertions
are negative/admission-only and never prove provisioned signer or native LACE.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.runtime.enforcer_lace_restart_reclaim_fence import (
    require_no_original_lace_intent_before_reclamation_in as fence,
)
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.unit.runtime.test_b12d2c3b5_atomic_restart_transfer import seeded, transfer


class FakeJournal:
    def __init__(self, status=None):
        self.status = status or SimpleNamespace(
            status=SimpleNamespace(cancel_request_satisfied=False, terminal=False)
        )
        self.called = []

    def read_view_from_connection(self, connection):
        return self

    def read_view(self):
        return self

    def active_run_detail(self):
        return SimpleNamespace(run=SimpleNamespace(fleet_run_id="run-one"))

    def required_run_detail(self, run_id):
        assert run_id == "run-one"
        return SimpleNamespace(run=self.status)

    def run_detail(self, run_id):
        return self.required_run_detail(run_id)

    def terminalize_run_in(self, *_args):
        self.called.append("terminalized")
        raise AssertionError("generic writer terminalized original LACE run")

    def request_cancel_in(self, *_args):
        self.called.append("cancelled")
        return self.status


def _snapshot(conn):
    return (
        [tuple(row) for row in conn.execute("SELECT * FROM execution_lease")],
        [tuple(row) for row in conn.execute("SELECT * FROM lace_run_authority")],
        [tuple(row) for row in conn.execute("SELECT * FROM lace_run_events")],
    )


def test_startup_recovery_blocks_before_deleting_dead_original_lease(tmp_path):
    conn, _journal, coordinator, _env = seeded(tmp_path)
    original = _snapshot(conn)
    fake = FakeJournal()
    recovery = RepositoryEffectRecovery(
        coordinator._leases._database,
        None,
        fake,
        coordinator,
        None,
        None,
    )
    with pytest.raises(StateConflict, match="signed recovery custody"):
        recovery._terminalize_interrupted_fleet()
    assert _snapshot(conn) == original
    assert not fake.called
    conn.close()


def test_dead_owner_cancel_fails_before_reclaiming_original_lease(tmp_path):
    conn, _journal, coordinator, _env = seeded(tmp_path)
    original = _snapshot(conn)
    fake = FakeJournal()
    lifecycle = FleetRunLifecycle(coordinator._leases._database, fake, coordinator, None)
    with pytest.raises(StateConflict, match="signed recovery custody"):
        lifecycle.cancel("run-one")
    assert _snapshot(conn) == original
    assert not fake.called
    conn.close()


def test_recovery_only_lease_cannot_be_generic_cancelled(tmp_path):
    conn, journal, coordinator, _env = seeded(tmp_path)
    transfer(conn, journal, coordinator)
    original = _snapshot(conn)
    fake = FakeJournal()
    lifecycle = FleetRunLifecycle(coordinator._leases._database, fake, coordinator, None)
    with pytest.raises(StateConflict, match="recovery-only LACE custody"):
        lifecycle.cancel("run-one")
    assert _snapshot(conn) == original
    assert not fake.called
    conn.close()


def test_live_owner_cancellation_remains_possible(tmp_path):
    conn, journal, coordinator, env = seeded(tmp_path)
    env.predecessor_live = True
    fake = FakeJournal()
    lifecycle = FleetRunLifecycle(coordinator._leases._database, fake, coordinator, None)
    lifecycle.cancel("run-one")
    assert fake.called == ["cancelled"]
    assert conn.execute("SELECT cancel_requested_at FROM execution_lease").fetchone()[0]
    assert journal._row("run-one")[1] == 1
    conn.close()


def test_pre_lace_database_keeps_original_legacy_path(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.sqlite", isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    fence(conn, "run-one")
    conn.execute("ROLLBACK")
    conn.close()


def test_partial_or_unreadable_authority_fails_closed(tmp_path):
    conn = sqlite3.connect(tmp_path / "partial.sqlite", isolation_level=None)
    conn.execute("CREATE TABLE lace_run_events (run_id TEXT, event_kind TEXT)")
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(StateConflict, match="partial original LACE schema"):
        fence(conn, "run-one")
    conn.execute("ROLLBACK")
    conn.close()


def test_read_only_cursor_cannot_authorize_lease_reclamation(tmp_path):
    conn, _, _, _ = seeded(tmp_path)
    with pytest.raises(StateConflict, match="original SQLite writer"):
        fence(conn, "run-one")
    conn.close()


def test_admitted_original_lace_without_resource_intent_is_not_absence_proof(tmp_path):
    conn = sqlite3.connect(tmp_path / "admitted.sqlite", isolation_level=None)
    conn.executescript(
        "CREATE TABLE lace_run_authority (run_id TEXT);"
        "CREATE TABLE lace_run_events (run_id TEXT, event_kind TEXT);"
        "INSERT INTO lace_run_authority VALUES ('run-one');"
    )
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(StateConflict, match="authenticated recovery"):
        fence(conn, "run-one")
    conn.execute("ROLLBACK")
    conn.close()


def test_shared_lease_store_cannot_bypass_original_lace_reclaim_fence(tmp_path):
    conn, journal, coordinator, _ = seeded(tmp_path)
    original = _snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(StateConflict, match="signed recovery custody"):
            coordinator.assert_idle(conn)
        assert _snapshot(conn) == original
    finally:
        conn.execute("ROLLBACK")
    assert journal._row("run-one")[1] == 1
    conn.close()
