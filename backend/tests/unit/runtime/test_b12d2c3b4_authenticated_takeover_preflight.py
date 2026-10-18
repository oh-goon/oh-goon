"""B12D2C3B4: restart boundary refuses forged lineage before native effects."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import OwnershipLost
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.enforcer_lace_takeover_preflight import (
    require_original_takeover_lineage_in,
)
from oh_goon.runtime.execution.sqlite_lease import _owned_fleet_lease_in
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c3b_takeover_recovery import setup_takeover


def original(tmp_path):
    conn, journal, owner = setup_takeover(tmp_path)
    owner = replace(owner, admitted_executor_generation=8, admitted_issuer_key_id="key-1")
    return conn, journal, owner


def test_enrolled_generation_issuer_and_intent_verified_before_cleanup_claim(tmp_path):
    conn, journal, owner = original(tmp_path)
    assert owner.pending_takeover_recovery()[0].effect_permission == "NONE"
    result = owner.session.mutate(
        lambda connection: require_original_takeover_lineage_in(
            journal,
            run_id="run-one",
            intent_run_seq=2,
            epoch=2,
            lease_identity="new",
            auth=authenticator(),
            admitted_executor_generation=8,
            admitted_issuer_key_id="key-1",
        )
    )
    assert (
        result.run_id,
        result.issuer_key_id,
        result.executor_generation,
        result.effect_permission,
    ) == ("run-one", "key-1", 8, "NONE")
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "generation",
        "issuer",
        "missing-generation",
        "missing-issuer",
        "missing-intent",
        "foreign-run",
        "admission-mac",
        "foreign-lease",
        "epoch",
        "cancelled",
    ],
)
def test_unproven_lineage_never_claims_or_scans(tmp_path, attack):
    conn, journal, owner = original(tmp_path)
    if attack == "generation":
        owner = replace(owner, admitted_executor_generation=9)
    elif attack == "issuer":
        owner = replace(owner, admitted_issuer_key_id="foreign")
    elif attack == "missing-generation":
        owner = replace(owner, admitted_executor_generation=None)
    elif attack == "missing-issuer":
        owner = replace(owner, admitted_issuer_key_id=None)
    elif attack == "admission-mac":
        conn.execute("UPDATE lace_run_authority SET admission_mac=?", ("0" * 64,))
    elif attack == "foreign-lease":
        owner.session.token = "foreign"
    elif attack == "epoch":
        journal.transfer_control(
            "run-one", epoch=2, lease_identity="new", next_epoch=3, next_lease_identity="later"
        )
    elif attack == "cancelled":
        owner.session.cancelled = True
    # Wrong intent/run cannot become another Run's recovery task.
    before = conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0]
    native = Mock()
    try:
        if attack == "missing-intent":
            owner.resume_reconciled_takeover(intent_run_seq=999, native_observer=native)
        elif attack == "foreign-run":
            owner.session.mutate(
                lambda _: require_original_takeover_lineage_in(
                    journal,
                    run_id="foreign",
                    intent_run_seq=2,
                    epoch=2,
                    lease_identity="new",
                    auth=authenticator(),
                    admitted_executor_generation=8,
                    admitted_issuer_key_id="key-1",
                )
            )
        else:
            owner.resume_reconciled_takeover(intent_run_seq=2, native_observer=native)
    except (CausalJournalError, TrustProtocolError):
        pass
    else:
        pytest.fail(f"{attack} unexpectedly allowed a recovery claim")
    native.assert_not_called()
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == before
    conn.close()


def test_preflight_requires_original_write_cut(tmp_path):
    conn, journal, _ = original(tmp_path)
    with pytest.raises(CausalJournalError, match="transaction"):
        require_original_takeover_lineage_in(
            journal,
            run_id="run-one",
            intent_run_seq=2,
            epoch=2,
            lease_identity="new",
            auth=authenticator(),
            admitted_executor_generation=8,
            admitted_issuer_key_id="key-1",
        )
    conn.close()


def test_previous_epoch_claim_fails_after_restart_and_new_epoch_succeeds_only_with_owner(tmp_path):
    conn, journal, owner = original(tmp_path)
    owner.session.token = "lease-one"
    with pytest.raises(CausalJournalError):
        owner.pending_takeover_recovery()
    owner.session.token = "new"
    assert len(owner.pending_takeover_recovery()) == 1
    # This is still an unqualified worklist, not a transfer mechanism.
    conn.close()


def _lease_connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE execution_lease (
        singleton INTEGER, lease_token TEXT, owner_session_id TEXT, owner_pid INTEGER,
        owner_identity TEXT, kind TEXT, fleet_run_id TEXT, cancel_requested_at TEXT
    )""")
    conn.execute(
        "INSERT INTO execution_lease VALUES (1,'tok','old-session',41,'birth-41','FLEET','run-one',NULL)"
    )
    return conn


@pytest.mark.parametrize(
    "session,pid,birth,expected",
    [
        ("old-session", 41, "birth-41", True),
        ("new-session", 41, "birth-41", False),  # process restart / new database session
        ("old-session", 41, "birth-99", False),  # PID reuse
        ("old-session", 41, None, False),  # birth cannot be proven
        ("old-session", 42, "birth-41", False),
    ],
)
def test_original_worker_ticket_cannot_be_minted_after_restart(session, pid, birth, expected):
    conn = _lease_connection()
    lease = _owned_fleet_lease_in(conn, "run-one", session, pid, birth)
    assert (lease is not None) is expected
    conn.close()


def test_cancelled_ticket_is_not_recoverable():
    conn = _lease_connection()
    conn.execute("UPDATE execution_lease SET cancel_requested_at='2026-10-12T00:00:00Z'")
    assert _owned_fleet_lease_in(conn, "run-one", "old-session", 41, "birth-41") is None
    conn.close()


def test_control_plane_may_delegate_live_ticket_but_never_create_one():
    plane = object.__new__(FleetRunControlPlane)
    lifecycle = SimpleNamespace(
        existing_owned_local_lace_ticket=Mock(return_value="original-ticket")
    )
    plane._lifecycle = lifecycle
    assert plane.existing_owned_local_lace_ticket("run-one") == "original-ticket"
    lifecycle.existing_owned_local_lace_ticket.assert_called_once_with("run-one")
    lifecycle.existing_owned_local_lace_ticket.side_effect = OwnershipLost("no live owner")
    with pytest.raises(OwnershipLost):
        plane.existing_owned_local_lace_ticket("run-one")


def test_generation_proof_rechecked_at_claim_commit_even_after_worklist(tmp_path):
    conn, journal, owner = original(tmp_path)
    assert len(owner.pending_takeover_recovery()) == 1
    changed = replace(owner, admitted_executor_generation=9)
    before = conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0]
    with pytest.raises(CausalJournalError, match="generation"):
        changed.claim(intent_run_seq=2)
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == before
    conn.close()
