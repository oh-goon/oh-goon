"""DB1B1: SQLite stays the only engine; domain participants never own COMMIT."""

from __future__ import annotations

import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract,
    TrustProtocolError,
    admission_receipt,
)
from oh_goon.runtime.database import LocalDatabase
from tests.fixtures.db1b2_legacy_sqlite import (
    CausalRunJournal,
    SignerTrustEpochAuthority,
    SignerTrustGrant,
)
from tests.unit.core.test_enforcer_lace_trust import authenticator, authority


def _owners(tmp_path):
    path = tmp_path / "single.sqlite"
    LocalDatabase(path)
    # The optional pre-live schemas are provisioned ONLY before binding the
    # canonical operational owner. Do not introduce runtime schema writers.
    setup = sqlite3.connect(path, isolation_level=None)
    CausalRunJournal(setup).initialize_schema()
    SignerTrustEpochAuthority(setup).initialize_schema()
    setup.execute("CREATE TABLE prelive_lease (run_id TEXT PRIMARY KEY)")
    setup.close()
    return LocalDatabase(path), LocalDatabase(path)


def _signer_snapshot():
    return ((1, 2), (3, 4), b"signed-policy")


def _signer_roots(monkeypatch):
    monkeypatch.setattr(
        "oh_goon.runtime.enforcer_lace_signer_trust_epoch.identity_for_roots",
        lambda roots: "test-root-id",
    )
    return {}


def test_one_canonical_signer_admin_commit_revocation_and_stale_generation(tmp_path, monkeypatch):
    primary, other = _owners(tmp_path)
    roots = _signer_roots(monkeypatch)
    grant = primary.enroll_signer_trust_atomically(policy_snapshot=_signer_snapshot(), roots=roots)
    with pytest.raises(TrustProtocolError, match="already provisioned"):
        other.enroll_signer_trust_atomically(policy_snapshot=_signer_snapshot(), roots=roots)
    primary.revoke_signer_trust_atomically(expected=grant)
    with pytest.raises(TrustProtocolError, match="stale"):
        other.revoke_signer_trust_atomically(expected=grant)
    with other.transaction() as connection:
        with pytest.raises(TrustProtocolError, match="no active"):
            SignerTrustEpochAuthority(connection).active()
        assert tuple(
            connection.execute("SELECT trust_epoch, state FROM lace_signer_trust_epoch").fetchone()
        ) == (2, "REVOKED")


def test_signer_admin_rollback_on_fault_after_domain_update(tmp_path, monkeypatch):
    primary, _ = _owners(tmp_path)
    roots = _signer_roots(monkeypatch)
    grant = primary.enroll_signer_trust_atomically(policy_snapshot=_signer_snapshot(), roots=roots)
    with pytest.raises(RuntimeError, match="cut failed"):
        with primary.transaction(write=True) as connection:
            primary.require_atomic_write_cut(connection)
            SignerTrustEpochAuthority(connection).revoke_in(connection, expected=grant)
            raise RuntimeError("cut failed")
    with primary.transaction() as connection:
        assert SignerTrustEpochAuthority(connection).active() == grant


def _admission(primary, *, before_commit=None):
    contract = AdmittedRunContract.freeze(authority())
    auth = authenticator()
    return (
        contract,
        auth,
        lambda: primary.publish_causal_admission_atomically(
            contract=contract,
            receipt=admission_receipt(auth, contract),
            authenticator=auth,
            control_epoch=1,
            lease_identity="lease-1",
            operational_admission=lambda connection: connection.execute(
                "INSERT INTO prelive_lease VALUES ('run-one')"
            ),
            before_commit=before_commit,
        ),
    )


def test_one_canonical_causal_and_operational_atomic_commit_and_replay(tmp_path):
    primary, other = _owners(tmp_path)
    contract, auth, publish = _admission(primary)
    publish()
    with pytest.raises(sqlite3.IntegrityError):
        publish()  # Crash after commit but before acknowledgement: replay is not duplicate success.
    with other.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM prelive_lease").fetchone()[0] == 1
        assert [
            item["kind"] for item in CausalRunJournal(connection).verify("run-one", auth=auth)
        ] == ["RUN_ADMITTED"]


def test_crash_before_commit_rolls_back_operational_and_causal_facts(tmp_path):
    primary, _ = _owners(tmp_path)
    _, _, publish = _admission(
        primary, before_commit=lambda: (_ for _ in ()).throw(RuntimeError("simulated crash"))
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        publish()
    with primary.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM prelive_lease").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM lace_run_authority").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM lace_run_events").fetchone()[0] == 0


@pytest.mark.parametrize("operation", ["commit", "rollback", "sql-commit", "sql-rollback"])
def test_participant_cannot_end_owner_cut(tmp_path, operation):
    primary, _ = _owners(tmp_path)

    def premature(connection):
        connection.execute("INSERT INTO prelive_lease VALUES ('run-one')")
        if operation.startswith("sql-"):
            connection.execute(operation.split("-")[1].upper())
        else:
            getattr(connection, operation)()

    contract = AdmittedRunContract.freeze(authority())
    auth = authenticator()
    with pytest.raises(sqlite3.DatabaseError):
        primary.publish_causal_admission_atomically(
            contract=contract,
            receipt=admission_receipt(auth, contract),
            authenticator=auth,
            control_epoch=1,
            lease_identity="lease-1",
            operational_admission=premature,
        )
    with primary.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM prelive_lease").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM lace_run_authority").fetchone()[0] == 0


def test_participant_methods_reject_foreign_and_unstarted_connections(tmp_path):
    primary, _ = _owners(tmp_path)
    with primary.transaction() as connection:
        signer = SignerTrustEpochAuthority(connection, owner=primary)
        with pytest.raises(RuntimeError, match="atomic write cut required"):
            signer.revoke_in(connection, expected=SignerTrustGrant(1, "p", "r"))
    with primary.transaction(write=True) as connection:
        signer = SignerTrustEpochAuthority(connection, owner=primary)
        foreign = sqlite3.connect(":memory:", isolation_level=None)
        with pytest.raises(TrustProtocolError, match="active original"):
            signer.revoke_in(foreign, expected=SignerTrustGrant(1, "p", "r"))
        foreign.close()


def test_canonical_signer_rotation_keeps_one_owner_and_stale_epoch_fence(tmp_path, monkeypatch):
    primary, other = _owners(tmp_path)
    monkeypatch.setattr(
        "oh_goon.runtime.enforcer_lace_signer_trust_epoch.identity_for_roots",
        lambda roots: "new-root" if "next" in roots else "old-root",
    )
    original = primary.enroll_signer_trust_atomically(policy_snapshot=_signer_snapshot(), roots={})
    rotated = other.rotate_signer_trust_atomically(
        expected=original,
        policy_snapshot=((1, 2), (3, 4), b"changed-signed-policy"),
        roots={"next": ("issuer", b"public")},
    )
    assert rotated.epoch == original.epoch + 1
    assert rotated.root_identity == "new-root"
    with pytest.raises(TrustProtocolError, match="stale"):
        primary.revoke_signer_trust_atomically(expected=original)
    with primary.transaction() as connection:
        assert SignerTrustEpochAuthority(connection).active() == rotated
