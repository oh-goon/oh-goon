"""DB1B2: production has one SQLite owner; replay requires exact durable truth."""

from __future__ import annotations

import multiprocessing
import os
import sqlite3
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    admission_receipt,
)
from oh_goon.runtime.database import CausalAdmissionCommit, LocalDatabase
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError, CausalRunJournal
from oh_goon.runtime.enforcer_lace_signer_trust_epoch import (
    SignerTrustEpochAuthority,
    SignerTrustGrant,
)
from tests.unit.core.test_enforcer_lace_trust import authenticator, authority
from tests.unit.services.test_group_a_verified_ingress_authorities import roots as trusted_roots

SNAPSHOT = ((1, 2), (3, 4), b"test-owned-signer-policy")


def _database(path: Path) -> LocalDatabase:
    LocalDatabase(path)
    # Optional pre-live schemas are installed before operational owner admission.
    setup = sqlite3.connect(path, isolation_level=None)
    CausalRunJournal(setup).initialize_schema()
    SignerTrustEpochAuthority(setup).initialize_schema()
    setup.execute("CREATE TABLE operational_lease (run_id TEXT PRIMARY KEY, lease TEXT NOT NULL)")
    setup.close()
    return LocalDatabase(path)


def _publish(
    db: LocalDatabase,
    *,
    contract,
    auth,
    epoch=1,
    lease="lease-one",
    callback=None,
    before_commit=None,
    proof=None,
):
    return db.publish_causal_admission_atomically(
        contract=contract,
        receipt=admission_receipt(auth, contract),
        authenticator=auth,
        control_epoch=epoch,
        lease_identity=lease,
        operational_admission=callback
        or (
            lambda conn: conn.execute(
                "INSERT INTO operational_lease VALUES (?, ?)", (contract.fields["run_id"], lease)
            )
        ),
        before_commit=before_commit,
        operational_committed=proof,
    )


def _lease_proof(connection):
    return tuple(
        connection.execute("SELECT lease FROM operational_lease WHERE run_id='run-one'").fetchone()
        or ()
    ) == ("lease-one",)


def test_runtime_legacy_transaction_wrappers_fail_without_effects(tmp_path):
    path = tmp_path / "single.sqlite"
    db = _database(path)
    with db.transaction(write=True) as conn:
        with pytest.raises(CausalJournalError, match="standalone causal transaction forbidden"):
            CausalRunJournal(conn)._transaction(
                lambda: conn.execute("DELETE FROM operational_lease")
            )
        with pytest.raises(TrustProtocolError, match="standalone signer transaction forbidden"):
            SignerTrustEpochAuthority(conn)._transaction(
                lambda: conn.execute("DELETE FROM operational_lease")
            )


def test_typed_retry_after_reopen_proves_original_commit_without_callback_reexecution(tmp_path):
    path = tmp_path / "single.sqlite"
    db = _database(path)
    contract, auth = AdmittedRunContract.freeze(authority()), authenticator()
    invoked = []

    def write(conn):
        invoked.append(1)
        conn.execute("INSERT INTO operational_lease VALUES ('run-one', 'lease-one')")

    first = _publish(db, contract=contract, auth=auth, callback=write, proof=_lease_proof)
    assert first == CausalAdmissionCommit("COMMITTED", "run-one", contract.identity, 1, "lease-one")
    reopened = LocalDatabase(path)
    second = _publish(reopened, contract=contract, auth=auth, callback=write, proof=_lease_proof)
    assert second.disposition == "ALREADY_COMMITTED"
    assert invoked == [1]
    with reopened.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1


@pytest.mark.parametrize(
    "attack",
    [
        "wrong-lease",
        "wrong-epoch",
        "wrong-contract",
        "missing-operational",
        "proof-mutates",
        "bad-receipt",
    ],
)
def test_typed_replay_rejects_mismatched_or_unverifiable_durable_truth(tmp_path, attack):
    path = tmp_path / "single.sqlite"
    db = _database(path)
    contract, auth = AdmittedRunContract.freeze(authority()), authenticator()
    _publish(db, contract=contract, auth=auth, proof=_lease_proof)

    def callback(_conn):
        return (_ for _ in ()).throw(AssertionError("retry must not call operational writer"))

    kwargs = {}
    if attack == "wrong-lease":
        kwargs["lease"] = "other-lease"
    elif attack == "wrong-epoch":
        kwargs["epoch"] = 2
    elif attack == "wrong-contract":
        fields = authority()
        fields["repository_identity"] = "sha256:" + "f" * 64
        kwargs["contract"] = AdmittedRunContract.freeze(fields)
    elif attack == "bad-receipt":
        # Same contract but another authenticator whose admission MAC cannot match.
        from oh_goon.core.enforcer_lace_trust import ReceiptAuthenticator

        kwargs["auth"] = ReceiptAuthenticator(
            key_id="forged-issuer", secret=b"different-secret-32-byte-secret-longer"
        )
    elif attack == "missing-operational":
        with db.transaction(write=True) as conn:
            conn.execute("DELETE FROM operational_lease")
    else:

        def mutate(conn):
            conn.execute("UPDATE operational_lease SET lease='forged'")
            return True

        kwargs["proof"] = mutate
    with pytest.raises((CausalJournalError, sqlite3.DatabaseError, TrustProtocolError)):
        _publish(
            db,
            contract=kwargs.pop("contract", contract),
            auth=kwargs.pop("auth", auth),
            callback=callback,
            proof=kwargs.pop("proof", _lease_proof),
            **kwargs,
        )
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
        if attack == "proof-mutates":
            assert tuple(conn.execute("SELECT lease FROM operational_lease").fetchone()) == (
                "lease-one",
            )


def test_failed_precommit_is_not_a_replay_success(tmp_path):
    db = _database(tmp_path / "single.sqlite")
    contract, auth = AdmittedRunContract.freeze(authority()), authenticator()

    def crash():
        raise RuntimeError("inject precommit crash")

    with pytest.raises(RuntimeError, match="precommit crash"):
        _publish(db, contract=contract, auth=auth, proof=_lease_proof, before_commit=crash)
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    assert _publish(db, contract=contract, auth=auth, proof=_lease_proof).disposition == "COMMITTED"


def _concurrent_signer_operation(
    path: str, op: str, grant: SignerTrustGrant, start, output
) -> None:
    # Spawned independent process: distinct SQLite connection, OS lock and owner.
    try:
        db = LocalDatabase(Path(path))
        start.wait(15)
        if op == "revoke":
            db.revoke_signer_trust_atomically(expected=grant)
        elif op == "transfer":
            db.transfer_causal_control_atomically(
                run_id="run-one",
                epoch=1,
                lease_identity="lease-one",
                next_epoch=2,
                next_lease_identity="lease-next",
                signer_grant=grant,
                policy_snapshot=SNAPSHOT,
                roots=trusted_roots(),
            )
        else:
            raise AssertionError("unknown operation")
        output.put((op, "COMMITTED"))
    except Exception as exc:
        output.put((op, type(exc).__name__ + ":" + str(exc)))


def _exit_during_admission(path: str, phase: str) -> None:
    db = LocalDatabase(Path(path))
    if phase == "before-commit":
        with db.transaction(write=True) as conn:
            conn.execute("INSERT INTO operational_lease VALUES ('run-one', 'lease-one')")
            os._exit(71)  # OS kills process while original SQLite owner cut is open.
    else:
        contract, auth = AdmittedRunContract.freeze(authority()), authenticator()
        _publish(db, contract=contract, auth=auth, proof=_lease_proof)
        os._exit(72)  # committed; caller/parent never received acknowledgement.


@pytest.mark.parametrize("phase,exit_code", [("before-commit", 71), ("after-commit", 72)])
def test_real_process_exit_before_or_after_commit_is_reconciled_by_durable_reread(
    tmp_path,
    phase,
    exit_code,
):
    path = tmp_path / "single.sqlite"
    db = _database(path)
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_exit_during_admission, args=(str(path), phase))
    child.start()
    child.join(timeout=30)
    if child.is_alive():
        child.kill()
        child.join(timeout=10)
    assert child.exitcode == exit_code
    contract, auth = AdmittedRunContract.freeze(authority()), authenticator()
    result = _publish(LocalDatabase(path), contract=contract, auth=auth, proof=_lease_proof)
    assert result.disposition == ("COMMITTED" if phase == "before-commit" else "ALREADY_COMMITTED")
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1


def test_interleaved_signer_revocation_and_causal_transfer_cross_process(tmp_path):
    path = tmp_path / "single.sqlite"
    db = _database(path)
    grant = db.enroll_signer_trust_atomically(policy_snapshot=SNAPSHOT, roots=trusted_roots())
    contract, auth = AdmittedRunContract.freeze(authority()), authenticator()
    _publish(db, contract=contract, auth=auth)
    ctx = multiprocessing.get_context("spawn")
    start, output = ctx.Event(), ctx.Queue()
    processes = [
        ctx.Process(target=_concurrent_signer_operation, args=(str(path), op, grant, start, output))
        for op in ("revoke", "transfer")
    ]
    for process in processes:
        process.start()
    start.set()
    results = dict(output.get(timeout=35) for _ in processes)
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert results["revoke"] == "COMMITTED", results
    assert results["transfer"] == "COMMITTED" or "revoked or superseded" in results["transfer"]
    with LocalDatabase(path).transaction() as conn:
        assert tuple(
            conn.execute("SELECT trust_epoch,state FROM lace_signer_trust_epoch").fetchone()
        ) == (2, "REVOKED")
        row = tuple(
            conn.execute("SELECT control_epoch,lease_identity FROM lace_run_authority").fetchone()
        )
        assert row in ((1, "lease-one"), (2, "lease-next"))
    with pytest.raises(TrustProtocolError):
        db.transfer_causal_control_atomically(
            run_id="run-one",
            epoch=1,
            lease_identity="lease-one",
            next_epoch=2,
            next_lease_identity="lease-next",
            signer_grant=grant,
            policy_snapshot=SNAPSHOT,
            roots=trusted_roots(),
        )
