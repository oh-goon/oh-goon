"""Unbound control-plane journal adversarial transaction vectors."""

import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import (
    PROTOCOL_VERSION,
    AdmittedRunContract,
    TrustProtocolError,
    admission_receipt,
    rejected_receipt,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.core.test_enforcer_lace_trust import authenticator, authority


def store():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(conn)
    journal.initialize_schema()
    return conn, journal


def admit(journal, *, run="run-one", epoch=1, lease="lease-one"):
    contract = AdmittedRunContract.freeze(authority(run))
    auth = authenticator()
    journal.publish_admission(
        contract, admission_receipt(auth, contract), auth, control_epoch=epoch, lease_identity=lease
    )
    return contract, auth


def test_atomic_admission_and_sequenced_append():
    conn, journal = store()
    c, auth = admit(journal)
    assert journal.verify("run-one", auth=authenticator()) == (
        {"run_seq": 1, "kind": "RUN_ADMITTED", "payload": {"contract_identity": c.identity}},
    )
    assert (
        journal.append(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            kind="STEP_WORK_STARTED",
            payload={"step_id": "s1"},
        )
        == 2
    )
    assert journal.verify("run-one", auth=authenticator())[-1]["run_seq"] == 2
    with pytest.raises(sqlite3.IntegrityError):
        journal.publish_admission(
            c, admission_receipt(auth, c), auth, control_epoch=1, lease_identity="lease-one"
        )
    assert len(journal.verify("run-one", auth=authenticator())) == 2
    conn.close()


def test_reject_mismatched_rejected_cross_run_receipts_before_mutation():
    conn, journal = store()
    c = AdmittedRunContract.freeze(authority())
    auth = authenticator()
    for receipt in [
        rejected_receipt(auth, run_id="run-one", reason="blocked"),
        admission_receipt(auth, AdmittedRunContract.freeze(authority("other"))),
    ]:
        with pytest.raises(CausalJournalError):
            journal.publish_admission(c, receipt, auth, control_epoch=1, lease_identity="lease-one")
    assert conn.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    conn.close()


def test_atomic_admission_crash_before_commit_leaves_no_run_no_journal():
    conn, journal = store()
    c = AdmittedRunContract.freeze(authority())
    auth = authenticator()

    def crash():
        raise RuntimeError("crash after append, before durable commit")

    with pytest.raises(RuntimeError):
        journal.publish_admission(
            c,
            admission_receipt(auth, c),
            auth,
            control_epoch=1,
            lease_identity="lease-one",
            before_commit=crash,
        )
    assert conn.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 0
    conn.close()


def test_append_crash_does_not_reserve_authoritative_sequence():
    conn, journal = store()
    admit(journal)
    with pytest.raises(RuntimeError):
        journal.append(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            kind="OBSERVED",
            payload={"observation": "network.connect"},
            before_commit=lambda: (_ for _ in ()).throw(RuntimeError("crash")),
        )
    assert len(journal.verify("run-one", auth=authenticator())) == 1
    assert (
        journal.append(
            "run-one", epoch=1, lease_identity="lease-one", kind="ABORT_REQUESTED", payload={}
        )
        == 2
    )
    conn.close()


def test_control_epoch_and_lease_fence_old_executor():
    conn, journal = store()
    admit(journal)
    journal.transfer_control(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        next_epoch=2,
        next_lease_identity="lease-two",
    )
    for epoch, lease in [(1, "lease-one"), (2, "lease-one"), (3, "lease-two")]:
        with pytest.raises(CausalJournalError):
            journal.append(
                "run-one", epoch=epoch, lease_identity=lease, kind="OBSERVED", payload={}
            )
    assert (
        journal.append("run-one", epoch=2, lease_identity="lease-two", kind="OBSERVED", payload={})
        == 2
    )
    assert len(journal.verify("run-one", auth=authenticator())) == 2
    conn.close()


def test_terminal_receipt_binds_exact_committed_cut_and_cannot_replay():
    conn, journal = store()
    c, auth = admit(journal)
    journal.append(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        kind="QUIESCENCE_PROVEN",
        payload={"scope": "run"},
    )
    row = journal._row("run-one")
    signed = auth.issue(
        "terminal",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "final_run_seq": row[3] + 1,
            "preterminal_head_identity": row[4],
            "outcome": "EXECUTION_COMPLETED",
        },
    )
    journal.seal(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        outcome="EXECUTION_COMPLETED",
        auth=auth,
        receipt=signed,
    )
    assert journal.verify("run-one", auth=auth)[-1]["kind"] == "RUN_OUTCOME"
    with pytest.raises(CausalJournalError):
        journal.append("run-one", epoch=1, lease_identity="lease-one", kind="OBSERVED", payload={})
    with pytest.raises(CausalJournalError):
        journal.seal(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            outcome="EXECUTION_COMPLETED",
            auth=auth,
            receipt=signed,
        )
    with pytest.raises(CausalJournalError):
        journal.verify("run-one", auth=None)
    conn.close()


def test_seal_requires_exact_receipt_and_atomic_rollback_on_failure():
    conn, journal = store()
    c, auth = admit(journal)
    wrong = auth.issue(
        "terminal",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "final_run_seq": 999,
            "preterminal_head_identity": "bad",
            "outcome": "EXECUTION_COMPLETED",
        },
    )
    with pytest.raises(CausalJournalError):
        journal.seal(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            outcome="EXECUTION_COMPLETED",
            auth=auth,
            receipt=wrong,
        )
    journal.append(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        kind="QUIESCENCE_PROVEN",
        payload={"scope": "run"},
    )
    row = journal._row("run-one")
    valid = auth.issue(
        "terminal",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "final_run_seq": row[3] + 1,
            "preterminal_head_identity": row[4],
            "outcome": "EXECUTION_COMPLETED",
        },
    )
    with pytest.raises(RuntimeError):
        journal.seal(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            outcome="EXECUTION_COMPLETED",
            auth=auth,
            receipt=valid,
            before_commit=lambda: (_ for _ in ()).throw(RuntimeError("commit crash")),
        )
    assert len(journal.verify("run-one", auth=authenticator())) == 2
    assert journal._row("run-one")[5] == "ACTIVE"
    conn.close()


@pytest.mark.parametrize("attack", ["payload", "gap", "tail", "hash", "receipt"])
def test_tampered_gap_truncated_or_forged_history_detected(attack):
    conn, journal = store()
    c, auth = admit(journal)
    journal.append(
        "run-one", epoch=1, lease_identity="lease-one", kind="OBSERVED", payload={"value": "good"}
    )
    journal.append(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        kind="QUIESCENCE_PROVEN",
        payload={"scope": "run"},
    )
    row = journal._row("run-one")
    receipt = auth.issue(
        "terminal",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "final_run_seq": row[3] + 1,
            "preterminal_head_identity": row[4],
            "outcome": "EXECUTION_COMPLETED",
        },
    )
    journal.seal(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        outcome="EXECUTION_COMPLETED",
        auth=auth,
        receipt=receipt,
    )
    if attack == "payload":
        conn.execute(
            "UPDATE lace_run_events SET event_payload=? WHERE run_seq=2", (b'{"value":"evil"}',)
        )
    elif attack == "gap":
        conn.execute("DELETE FROM lace_run_events WHERE run_seq=2")
    elif attack == "tail":
        conn.execute("DELETE FROM lace_run_events WHERE run_seq=3")
    elif attack == "hash":
        conn.execute("UPDATE lace_run_events SET previous_identity='tamper' WHERE run_seq=3")
    elif attack == "receipt":
        conn.execute("UPDATE lace_run_authority SET closure_mac=?", ("0" * 64,))
    with pytest.raises((CausalJournalError, TrustProtocolError)):
        journal.verify("run-one", auth=auth)
    conn.close()


def test_no_terminal_without_scoped_quiescence_and_no_unsupported_outcome():
    conn, journal = store()
    c, auth = admit(journal)
    row = journal._row("run-one")
    signed = auth.issue(
        "terminal",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "final_run_seq": 2,
            "preterminal_head_identity": row[4],
            "outcome": "EXECUTION_COMPLETED",
        },
    )
    with pytest.raises(CausalJournalError, match="quiescence"):
        journal.seal(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            outcome="EXECUTION_COMPLETED",
            auth=auth,
            receipt=signed,
        )
    assert journal._row("run-one")[5] == "ACTIVE"
    conn.close()


def test_authenticated_unresolved_never_rewrites_into_success():
    conn, journal = store()
    c, auth = admit(journal)
    journal.append(
        "run-one", epoch=1, lease_identity="lease-one", kind="CONTROL_LEASE_LOST", payload={}
    )
    row = journal._row("run-one")
    unresolved = auth.issue(
        "unresolved",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "control_epoch": row[1],
            "last_run_seq": row[3],
            "head_identity": row[4],
            "state": "JOURNAL_UNRESOLVED",
        },
    )
    journal.mark_unresolved("run-one", auth=auth, receipt=unresolved)
    assert journal._row("run-one")[5] == "UNRESOLVED"
    assert journal.verify("run-one", auth=auth)[-1]["kind"] == "CONTROL_LEASE_LOST"
    with pytest.raises(CausalJournalError):
        journal.mark_unresolved("run-one", auth=auth, receipt=unresolved)
    with pytest.raises(CausalJournalError):
        journal.append("run-one", epoch=1, lease_identity="lease-one", kind="OBSERVED", payload={})
    with pytest.raises((CausalJournalError, TrustProtocolError)):
        journal.verify("run-one", auth=None)
    conn.close()


def test_cross_run_unresolved_receipt_rejected_before_state_mutation():
    conn, journal = store()
    c, auth = admit(journal)
    row = journal._row("run-one")
    forged = auth.issue(
        "unresolved",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "other",
            "contract_identity": c.identity,
            "control_epoch": row[1],
            "last_run_seq": row[3],
            "head_identity": row[4],
            "state": "JOURNAL_UNRESOLVED",
        },
    )
    with pytest.raises(CausalJournalError):
        journal.mark_unresolved("run-one", auth=auth, receipt=forged)
    assert journal._row("run-one")[5] == "ACTIVE"
    conn.close()


def test_durable_sqlite_reopen_verifies_admission_and_causal_chain(tmp_path):
    path = tmp_path / "causal.sqlite3"
    conn = sqlite3.connect(path, isolation_level=None)
    journal = CausalRunJournal(conn)
    journal.initialize_schema()
    _, auth = admit(journal)
    journal.append(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        kind="OBSERVED",
        payload={"observation": "network.connect"},
    )
    conn.close()
    reopened = sqlite3.connect(path, isolation_level=None)
    recovered = CausalRunJournal(reopened)
    assert len(recovered.verify("run-one", auth=auth)) == 2
    assert (
        recovered.append(
            "run-one", epoch=1, lease_identity="lease-one", kind="ABORT_REQUESTED", payload={}
        )
        == 3
    )
    reopened.close()


def test_mutated_persisted_admission_or_wrong_historical_key_fails_authentication():
    conn, journal = store()
    admit(journal)
    with pytest.raises(TrustProtocolError):
        journal.verify("run-one", auth=authenticator("rotated-key"))
    conn.execute("UPDATE lace_run_authority SET admission_mac=?", ("0" * 64,))
    with pytest.raises(TrustProtocolError):
        journal.verify("run-one", auth=authenticator())
    conn.close()


def test_contract_identity_forgery_rejected_before_first_journal_event():
    from dataclasses import replace

    conn, journal = store()
    contract = AdmittedRunContract.freeze(authority())
    forged = replace(contract, identity="sha256:" + "f" * 64)
    auth = authenticator()
    with pytest.raises(TrustProtocolError):
        journal.publish_admission(
            forged,
            admission_receipt(auth, forged),
            auth,
            control_epoch=1,
            lease_identity="lease-one",
        )
    assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("kind", ["CONTROL_LEASE_LOST", "JOURNAL_APPEND_FAILED"])
def test_poisoned_control_history_cannot_seal_even_with_quiescence(kind):
    conn, journal = store()
    c, auth = admit(journal)
    journal.append("run-one", epoch=1, lease_identity="lease-one", kind=kind, payload={})
    journal.append(
        "run-one",
        epoch=1,
        lease_identity="lease-one",
        kind="QUIESCENCE_PROVEN",
        payload={"scope": "run"},
    )
    row = journal._row("run-one")
    receipt = auth.issue(
        "terminal",
        {
            "protocol": PROTOCOL_VERSION,
            "run_id": "run-one",
            "contract_identity": c.identity,
            "final_run_seq": row[3] + 1,
            "preterminal_head_identity": row[4],
            "outcome": "EXECUTION_COMPLETED",
        },
    )
    with pytest.raises(CausalJournalError, match="failure prevents"):
        journal.seal(
            "run-one",
            epoch=1,
            lease_identity="lease-one",
            outcome="EXECUTION_COMPLETED",
            auth=auth,
            receipt=receipt,
        )
    assert journal._row("run-one")[5] == "ACTIVE"
    conn.close()


def test_unknown_journal_event_fails_without_effect_or_sequence_allocation():
    conn, journal = store()
    admit(journal)
    with pytest.raises(CausalJournalError):
        journal.append(
            "run-one", epoch=1, lease_identity="lease-one", kind="SECRET_UNKNOWN_EVENT", payload={}
        )
    assert len(journal.verify("run-one", auth=authenticator())) == 1
    conn.close()
