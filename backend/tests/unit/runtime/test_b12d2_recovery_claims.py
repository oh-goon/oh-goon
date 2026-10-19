"""B12D2A: only the authenticated Fleet epoch can claim orphaned LACE intent.

A claim is cleanup-only and does not assert native absence or physical safety.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceRecoveryOwner
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12_local_resource_intent import _reserve, _store
from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _OriginalSession
from tests.unit.runtime.test_b12d1_native_allocation_journal import publish


def _claim(journal, *, epoch=1, lease="lease-one", seq=2):
    return journal._transaction(
        lambda: journal.claim_local_resource_recovery_in(
            "run-one",
            epoch=epoch,
            lease_identity=lease,
            auth=authenticator(),
            intent_run_seq=seq,
        )
    )


@pytest.mark.parametrize("observed", [False, True])
def test_original_recovery_claim_survives_sqlite_restart_without_claiming_physical_absence(
    tmp_path, observed
):
    path = tmp_path / "authority.sqlite"
    conn, journal = _store(path)
    assert _reserve(journal) == 2
    if observed:
        assert publish(journal) == 3
    assert _claim(journal) == (4 if observed else 3)
    events = journal.verify("run-one", auth=authenticator())
    receipt = events[-1]["payload"]
    assert receipt["stage"] == (
        "PRELAUNCH_OBSERVED_NOT_QUIESCENT" if observed else "INTENT_ONLY_RESOURCE_EXISTENCE_UNKNOWN"
    )
    assert receipt["allocation_run_seq"] == (3 if observed else None)
    assert receipt["cleanup_only"] is True
    for name in (
        "native_absence_proven",
        "mounts_proven",
        "process_tree_proven",
        "teardown_proven",
    ):
        assert receipt[name] is False
    conn.close()
    restart = sqlite3.connect(path, isolation_level=None)
    assert CausalRunJournal(restart).verify("run-one", auth=authenticator()) == events
    with pytest.raises(CausalJournalError, match="replay"):
        _claim(CausalRunJournal(restart))
    restart.close()


def test_takeover_requires_fresh_epoch_and_never_recycles_prior_claim(tmp_path):
    conn, journal = _store(tmp_path / "authority.sqlite")
    _reserve(journal)
    _claim(journal)
    journal.transfer_control(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        next_epoch=2,
        next_lease_identity="new-lease",
    )
    with pytest.raises(CausalJournalError, match="stale"):
        _claim(journal)
    assert _claim(journal, epoch=2, lease="new-lease") == 4
    with pytest.raises(CausalJournalError, match="replay"):
        _claim(journal, epoch=2, lease="new-lease")
    assert [
        e["payload"]["control_epoch"]
        for e in journal.verify("run-one", auth=authenticator())
        if e["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    ] == [1, 2]
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "foreign-intent",
        "wrong-lease",
        "wrong-epoch",
        "forged-auth",
        "tampered-history",
        "uncommitted",
        "inactive",
    ],
)
def test_recovery_denial_has_zero_new_journal_effects(tmp_path, attack):
    conn, journal = _store(tmp_path / "authority.sqlite")
    _reserve(journal)
    if attack == "tampered-history":
        conn.execute("UPDATE lace_run_events SET event_payload=? WHERE run_seq=2", (b"{}",))
    if attack == "inactive":
        journal.transfer_control(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            next_epoch=2,
            next_lease_identity="new-owner",
        )
    count = conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0]
    args = {"epoch": 1, "lease_identity": "lease-one", "auth": authenticator(), "intent_run_seq": 2}
    if attack == "foreign-intent":
        args["intent_run_seq"] = 1
    if attack == "wrong-lease":
        args["lease_identity"] = "foreign"
    if attack == "wrong-epoch":
        args["epoch"] = 10
    if attack == "forged-auth":
        args["auth"] = authenticator("foreign")
    if attack == "uncommitted":
        with pytest.raises(CausalJournalError, match="transaction"):
            journal.claim_local_resource_recovery_in("run-one", **args)
    else:
        with pytest.raises((CausalJournalError, TrustProtocolError)):
            journal._transaction(
                lambda: journal.claim_local_resource_recovery_in("run-one", **args)
            )
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == count
    conn.close()


def test_original_session_recovery_claim_rolls_back_if_authority_changes_after_append(tmp_path):
    conn, journal = _store(tmp_path / "authority.sqlite")
    _reserve(journal)
    session = _OriginalSession(conn)
    checks = []

    def verify_original(_connection):
        checks.append("original")
        if len(checks) == 2:
            raise TrustProtocolError("original Fleet recovery revoked")

    owner = OriginalFleetLocalLaceRecoveryOwner(
        session=session,
        validate_authority_in=verify_original,
        authenticator=authenticator(),
        run_id="run-one",
    )
    with pytest.raises(TrustProtocolError, match="revoked"):
        owner.claim(intent_run_seq=2)
    assert session.commit_count == 0
    assert [e["kind"] for e in journal.verify("run-one", auth=authenticator())] == [
        "RUN_ADMITTED",
        "RESOURCE_INTENT",
    ]
    owner = replace(owner, validate_authority_in=lambda _: None)
    assert owner.claim(intent_run_seq=2) == 3
    assert session.commit_count == 1
    session.token = "foreign-owner"
    with pytest.raises(CausalJournalError, match="original Fleet owner"):
        owner.claim(intent_run_seq=2)
    conn.close()
