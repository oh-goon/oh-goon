"""M153: Enforcer uses the real execution_lease authority, never a mock lease registry."""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError, admission_receipt
from oh_goon.core.errors import OwnershipLost
from oh_goon.runtime.database import LocalDatabase
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from oh_goon.runtime.execution.sqlite_lease import ExecutionLease, SQLiteExecutionLeaseStore
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.core.test_enforcer_lace_trust import authority, authenticator
from tests.unit.runtime.test_execution_lease import FakeLeaseEnvironment
from tests.unit.services.test_group_a_enforcer_owner_bridge import _environment
from tests.unit.services.test_group_a_verified_ingress_authorities import FIELDS, proof, roots


def setup(tmp_path: Path):
    # Phase 3 schema is currently opt-in: initialize it BEFORE the admitted
    # database owner captures its schema identity. Never introduce post-admission
    # DDL behind an already-admitted LocalDatabase instance.
    bootstrap = LocalDatabase(tmp_path / "state.db")
    external = sqlite3.connect(bootstrap.path, isolation_level=None)
    journal = CausalRunJournal(external)
    journal.initialize_schema()
    external.execute("CREATE TABLE operation_probe (run_id TEXT PRIMARY KEY)")
    database = LocalDatabase(tmp_path / "state.db")
    environment = FakeLeaseEnvironment()
    leases = SQLiteExecutionLeaseStore(database, environment=environment)
    lease, _ = leases.acquire_fleet_with(
        "run-one", expected_durable_revision=database.durable_revision(), mutate=lambda _: None,
    )
    bound_environment = _environment()
    fields = authority()
    fields.update(execution_environment_identity=bound_environment.execution_environment_identity,
                  backend_capability_manifest_identity=bound_environment.capability_manifest_identity,
                  executor_generation=bound_environment.executor_generation)
    contract = AdmittedRunContract.freeze(fields)
    assert contract.fields["run_id"] == "run-one"
    keys = roots()
    evidence = tuple(proof(field, contract, keys) for field in FIELDS)
    return database, environment, leases, lease, external, journal, bound_environment, contract, keys, evidence


def publish(bundle, *, operational=None, lease=None, journal=None):
    database, environment, leases, original, connection, default_journal, bound, contract, keys, evidence = bundle
    auth = authenticator()
    return FleetAdmissionPlanner.publish_with_owned_fleet_control(
        journal=journal or default_journal, lease_store=leases,
        lease=original if lease is None else lease,
        contract=contract, receipt=admission_receipt(auth, contract), authenticator=auth,
        configured=bound, current=bound,
        issuer_verifier=TrustedIssuerVerifier(keys), issuer_evidence=evidence,
        operational_admission=operational or (lambda c: c.execute(
            "INSERT INTO operation_probe VALUES ('run-one')")),
    )


def counts(connection):
    return tuple(connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                 for name in ("operation_probe", "lace_run_authority", "lace_run_events"))


def test_exact_durable_fleet_lease_commits_with_causal_admission(tmp_path):
    b = setup(tmp_path)
    _, _, leases, lease, connection, _, _, _, _, _ = b
    assert leases.initial_fleet_control_is_current("run-one", 1, lease.token)
    assert publish(b).rowcount == 1
    assert counts(connection) == (1, 1, 1)
    with pytest.raises(sqlite3.IntegrityError):
        publish(b)
    assert counts(connection) == (1, 1, 1)
    connection.close()


@pytest.mark.parametrize("attack", [
    "wrong_run", "wrong_token", "wrong_epoch", "cancelled", "released", "birth_reused",
    "owner_dead", "unknown_birth", "other_session", "direct", "owner_pid_changed",
])
def test_stale_or_unprovable_owner_rejects_without_publication(tmp_path, attack):
    b = setup(tmp_path)
    database, environment, leases, lease, connection, _, _, _, _, _ = b
    run, epoch, token = "run-one", 1, lease.token
    if attack == "wrong_run":
        run = "other"
    elif attack == "wrong_token":
        token = "lease-wrong"
    elif attack == "wrong_epoch":
        epoch = 2
    elif attack == "cancelled":
        connection.execute("UPDATE execution_lease SET cancel_requested_at='now'")
    elif attack == "released":
        connection.execute("DELETE FROM execution_lease")
    elif attack == "birth_reused":
        environment.identities[environment.pid] = "new-process-birth"
    elif attack == "owner_dead":
        environment.alive.clear()
    elif attack == "unknown_birth":
        environment.identities.pop(environment.pid)
    elif attack == "other_session":
        connection.execute("UPDATE execution_lease SET owner_session_id='other'")
    elif attack == "direct":
        connection.execute("UPDATE execution_lease SET kind='DIRECT', fleet_run_id=NULL")
    elif attack == "owner_pid_changed":
        connection.execute("UPDATE execution_lease SET owner_pid=99999")
    with pytest.raises(OwnershipLost):
        leases.require_initial_fleet_control_in(
            connection, run_id=run, control_epoch=epoch, lease_identity=token)
    if attack not in {"wrong_run", "wrong_token", "wrong_epoch"}:
        with pytest.raises((TrustProtocolError, OwnershipLost)):
            publish(b)
    assert counts(connection) == (0, 0, 0)
    connection.close()


def test_commit_time_revocation_rolls_back_all_admission_and_revocation(tmp_path):
    b = setup(tmp_path)
    _, _, leases, lease, connection, _, _, _, _, _ = b
    def revoke_inside_cut(c):
        c.execute("INSERT INTO operation_probe VALUES ('run-one')")
        c.execute("UPDATE execution_lease SET cancel_requested_at='now'")
        return None
    with pytest.raises(OwnershipLost):
        publish(b, operational=revoke_inside_cut)
    assert counts(connection) == (0, 0, 0)
    assert connection.execute("SELECT cancel_requested_at FROM execution_lease").fetchone()[0] is None
    assert leases.initial_fleet_control_is_current("run-one", 1, lease.token)
    connection.close()


def test_pid_reuse_between_precheck_and_commit_rolls_back_both_journals(tmp_path):
    b = setup(tmp_path)
    _, environment, _, _, connection, _, _, _, _, _ = b

    def race(c):
        c.execute("INSERT INTO operation_probe VALUES ('run-one')")
        environment.identities[environment.pid] = "later-process-birth"
        return None

    with pytest.raises(OwnershipLost, match="birth"):
        publish(b, operational=race)
    assert counts(connection) == (0, 0, 0)
    connection.close()


def test_existing_non_expiring_execution_lease_does_not_invent_time_expiry(tmp_path):
    b = setup(tmp_path)
    _, environment, store, lease, connection, _, _, _, _, _ = b
    environment.current += timedelta(days=365)
    assert store.initial_fleet_control_is_current("run-one", 1, lease.token)
    assert publish(b).rowcount == 1
    assert counts(connection) == (1, 1, 1)
    connection.close()


def test_unrelated_or_in_memory_database_cannot_replace_lease_owner(tmp_path):
    b = setup(tmp_path)
    _, _, leases, lease, connection, _, _, _, _, _ = b
    other = sqlite3.connect(":memory:")
    other.execute("CREATE TABLE execution_lease (singleton INTEGER, lease_token TEXT)")
    other.execute("INSERT INTO execution_lease VALUES (1, ?)", (lease.token,))
    with pytest.raises(OwnershipLost, match="owner database"):
        leases.require_initial_fleet_control_in(
            other, run_id="run-one", control_epoch=1, lease_identity=lease.token)
    assert counts(connection) == (0, 0, 0)
    other.close()
    connection.close()


def test_cannot_publish_with_an_unrelated_direct_or_wrong_run_handle(tmp_path):
    b = setup(tmp_path)
    _, _, _, lease, connection, _, _, _, _, _ = b
    for invalid in (ExecutionLease(token=lease.token, kind="DIRECT", fleet_run_id=None),
                    ExecutionLease(token=lease.token, kind="FLEET", fleet_run_id="different")):
        with pytest.raises(TrustProtocolError):
            publish(b, lease=invalid)
    assert counts(connection) == (0, 0, 0)
    connection.close()
