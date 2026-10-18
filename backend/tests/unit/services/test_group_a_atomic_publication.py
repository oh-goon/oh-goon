"""M150 pre-live Enforcer transactional co-publication: no second SQLite cut."""

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import (
    AdmittedRunContract, TrustProtocolError, admission_receipt, rejected_receipt,
)
from oh_goon.core.errors import FleetPlanInvalid
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.core.test_enforcer_lace_trust import authority, authenticator
from tests.unit.services.test_group_a_enforcer_owner_bridge import _environment


def test_atomic_legacy_lease_run_event_and_authenticated_admission_one_transaction():
    db = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(db)
    journal.initialize_schema()
    db.execute("CREATE TABLE operational_lease(run_id TEXT PRIMARY KEY)")
    db.execute("CREATE TABLE operational_event(run_id TEXT, event_type TEXT)")
    env = _environment()
    fields = authority()
    fields.update(execution_environment_identity=env.execution_environment_identity,
                  backend_capability_manifest_identity=env.capability_manifest_identity,
                  executor_generation=env.executor_generation)
    contract = AdmittedRunContract.freeze(fields)
    auth = authenticator()
    receipt = admission_receipt(auth, contract)
    called = []

    def operational(connection):
        assert connection is db and connection.in_transaction
        connection.execute("INSERT INTO operational_lease VALUES (?)", ("run-one",))
        connection.execute("INSERT INTO operational_event VALUES (?,?)", ("run-one", "RUN_ADMITTED"))
        called.append("operational")
        return "reserved"

    def publish(**kwargs):
        return FleetAdmissionPlanner.publish_atomic_run_admission(
            journal=journal, contract=contract, receipt=receipt,
            authenticator=auth, configured=env, current=env,
            control_epoch=1, lease_identity="lease-one",
            operational_admission=operational, **kwargs,
        )

    assert publish() == "reserved"
    assert called == ["operational"]
    assert db.execute("SELECT * FROM operational_lease").fetchall() == [("run-one",)]
    assert db.execute("SELECT * FROM operational_event").fetchall() == [("run-one", "RUN_ADMITTED")]
    assert journal.verify("run-one", auth=auth) == (
        {"run_seq": 1, "kind": "RUN_ADMITTED", "payload": {"contract_identity": contract.identity}},
    )
    with pytest.raises(sqlite3.IntegrityError):
        publish()
    assert db.execute("SELECT COUNT(*) FROM operational_event").fetchone()[0] == 1
    assert len(journal.verify("run-one", auth=auth)) == 1
    db.close()


def test_operational_exception_rolls_back_all_effect_and_authenticated_truth():
    db = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(db)
    journal.initialize_schema()
    db.execute("CREATE TABLE operational_lease(run_id TEXT PRIMARY KEY)")
    env = _environment()
    fields = authority()
    fields.update(execution_environment_identity=env.execution_environment_identity,
                  backend_capability_manifest_identity=env.capability_manifest_identity,
                  executor_generation=env.executor_generation)
    contract = AdmittedRunContract.freeze(fields)
    auth = authenticator()

    def crash(connection):
        connection.execute("INSERT INTO operational_lease VALUES ('run-one')")
        raise RuntimeError("crash during operational admission")

    with pytest.raises(RuntimeError, match="crash during"):
        FleetAdmissionPlanner.publish_atomic_run_admission(
            journal=journal, contract=contract, receipt=admission_receipt(auth, contract),
            authenticator=auth, configured=env, current=env,
            control_epoch=1, lease_identity="lease-one", operational_admission=crash,
        )
    assert db.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 0
    db.close()


@pytest.mark.parametrize("attack", ["drift", "forged", "rejected", "wrong-run", "stale-epoch", "missing-lease"])
def test_authentication_drift_and_stale_control_reject_before_any_operational_mutation(attack):
    db = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(db)
    journal.initialize_schema()
    env = _environment()
    fields = authority()
    fields.update(execution_environment_identity=env.execution_environment_identity,
                  backend_capability_manifest_identity=env.capability_manifest_identity,
                  executor_generation=env.executor_generation)
    contract = AdmittedRunContract.freeze(fields)
    auth = authenticator()
    receipt = admission_receipt(auth, contract)
    current = env
    epoch, lease = 1, "lease-one"
    if attack == "drift":
        current = _environment(executor_generation=10)
    elif attack == "forged":
        receipt = replace(receipt, mac_hex="0" * 64)
    elif attack == "rejected":
        receipt = rejected_receipt(auth, run_id="run-one", reason="blocked")
    elif attack == "wrong-run":
        receipt = admission_receipt(auth, AdmittedRunContract.freeze(authority("run-two")))
    elif attack == "stale-epoch":
        epoch = 0
    else:
        lease = ""
    calls = []
    with pytest.raises((FleetPlanInvalid, TrustProtocolError)):
        FleetAdmissionPlanner.publish_atomic_run_admission(
            journal=journal, contract=contract, receipt=receipt,
            authenticator=auth, configured=env, current=current,
            control_epoch=epoch, lease_identity=lease,
            operational_admission=lambda _: calls.append("mutated"),
        )
    assert calls == []
    assert db.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    db.close()


def test_journal_failure_after_operational_admission_rolls_back_both():
    db = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(db)
    journal.initialize_schema()
    db.execute("CREATE TABLE operational_lease(run_id TEXT PRIMARY KEY)")
    db.execute("CREATE TRIGGER deny_causal_admission BEFORE INSERT ON lace_run_events "
               "BEGIN SELECT RAISE(ABORT, 'journal write failed'); END")
    env = _environment()
    fields = authority()
    fields.update(execution_environment_identity=env.execution_environment_identity,
                  backend_capability_manifest_identity=env.capability_manifest_identity,
                  executor_generation=env.executor_generation)
    contract = AdmittedRunContract.freeze(fields)
    auth = authenticator()
    with pytest.raises(sqlite3.IntegrityError, match="journal write failed"):
        FleetAdmissionPlanner.publish_atomic_run_admission(
            journal=journal, contract=contract, receipt=admission_receipt(auth, contract),
            authenticator=auth, configured=env, current=env,
            control_epoch=1, lease_identity="lease-one",
            operational_admission=lambda c: c.execute(
                "INSERT INTO operational_lease VALUES ('run-one')"
            ),
        )
    assert db.execute("SELECT COUNT(*) FROM operational_lease").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM lace_run_authority").fetchone()[0] == 0
    db.close()


def test_nested_publisher_transaction_rejected_before_operational_work():
    db = sqlite3.connect(":memory:", isolation_level=None)
    journal = CausalRunJournal(db)
    journal.initialize_schema()
    env = _environment()
    fields = authority()
    fields.update(execution_environment_identity=env.execution_environment_identity,
                  backend_capability_manifest_identity=env.capability_manifest_identity,
                  executor_generation=env.executor_generation)
    contract = AdmittedRunContract.freeze(fields)
    auth = authenticator()
    db.execute("BEGIN")
    touched = []
    from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
    with pytest.raises(CausalJournalError, match="outstanding transaction"):
        FleetAdmissionPlanner.publish_atomic_run_admission(
            journal=journal, contract=contract, receipt=admission_receipt(auth, contract),
            authenticator=auth, configured=env, current=env,
            control_epoch=1, lease_identity="lease-one",
            operational_admission=lambda _: touched.append(True),
        )
    assert touched == []
    db.rollback()
    db.close()
