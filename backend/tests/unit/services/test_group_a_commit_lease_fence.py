"""M152 regressions: no causal Run admission without a commit-time durable lease proof."""

from __future__ import annotations

import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from tests.unit.services.test_group_a_verified_ingress_authorities import publish, setup


@pytest.mark.parametrize("attack", (
    "missing", "false", "none", "exception", "revoked_after_precheck", "wrong_lease", "wrong_epoch",
))
def test_commit_time_fence_rejects_and_rolls_back_both_journals(attack: str) -> None:
    db, journal, env, contract, keys, witnesses = setup()
    db.execute("CREATE TABLE durable_lease (run_id TEXT PRIMARY KEY, epoch INTEGER, identity TEXT, active INTEGER)")
    db.execute("INSERT INTO durable_lease VALUES ('run-one', 7, 'lease-exact', 1)")
    seen: list[bool] = []

    def fence(connection: sqlite3.Connection, run_id: str, epoch: int, identity: str) -> bool:
        assert connection is db
        assert connection.in_transaction
        # This must run *after* the operational reservation and causal event,
        # and therefore before neither transaction's commit has happened.
        assert connection.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
        seen.append(True)
        if attack == "exception":
            raise RuntimeError("trusted lease store unavailable")
        if attack in {"false", "none"}:
            return False if attack == "false" else None
        if attack == "wrong_epoch":
            epoch += 1
        if attack == "wrong_lease":
            identity = "other"
        return connection.execute(
            "SELECT 1 FROM durable_lease WHERE run_id=? AND epoch=? AND identity=? AND active=1",
            (run_id, epoch, identity),
        ).fetchone() == (1,)

    def reserve(connection: sqlite3.Connection) -> sqlite3.Cursor:
        result = connection.execute("INSERT INTO operational_lease VALUES ('run-one')")
        if attack == "revoked_after_precheck":
            connection.execute("UPDATE durable_lease SET active=0 WHERE run_id='run-one'")
        return result

    overrides = dict(
        commit_lease_is_current=None if attack == "missing" else fence,
        operational_admission=reserve,
    )
    with pytest.raises((TrustProtocolError, RuntimeError)):
        publish(db, journal, env, contract, keys, witnesses, override=overrides)
    assert db.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 0
    assert db.execute("SELECT active FROM durable_lease").fetchone()[0] == 1
    assert len(seen) == (0 if attack == "missing" else 1)
    db.close()


def test_valid_commit_time_fence_publishes_once_then_replay_rejected() -> None:
    db, journal, env, contract, keys, witnesses = setup()
    db.execute("CREATE TABLE durable_lease (run_id TEXT PRIMARY KEY, epoch INTEGER, identity TEXT)")
    db.execute("INSERT INTO durable_lease VALUES ('run-one', 7, 'lease-exact')")

    def fence(connection, run_id, epoch, identity):
        assert connection.in_transaction
        return connection.execute(
            "SELECT 1 FROM durable_lease WHERE run_id=? AND epoch=? AND identity=?",
            (run_id, epoch, identity),
        ).fetchone() == (1,)

    overrides = {"commit_lease_is_current": fence}
    assert publish(db, journal, env, contract, keys, witnesses, override=overrides).rowcount == 1
    assert db.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        publish(db, journal, env, contract, keys, witnesses, override=overrides)
    assert db.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
    db.close()
