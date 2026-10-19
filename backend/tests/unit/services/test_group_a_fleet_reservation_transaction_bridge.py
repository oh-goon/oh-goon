"""M169: one actual Fleet lease reservation and both admission journals."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal as RuntimeCausalRunJournal
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts, prepared


def _bridge(tmp_path, monkeypatch):
    args, external, calls = prepared(tmp_path, monkeypatch)
    database = args["journal"]._database
    # The retained M168 fixture starts with an already-acquired lease. M169
    # explicitly starts WITHOUT it, and the native coordinator must allocate it.
    with database.transaction(write=True) as connection:
        connection.execute("DELETE FROM execution_lease")
    original = ExecutionCoordinator(
        database,
        lease_environment=args["lease_store"]._environment,
    )
    parameters = dict(
        execution=original,
        expected_durable_revision=database.durable_revision(),
        operational_journal=args["journal"],
        contract=args["contract"],
        authenticator=args["authenticator"],
        configured=args["configured"],
        current=args["current"],
        intent=args["intent"],
        repository_scope=args["repository_scope"],
        targets=args["targets"],
        path_authority=args["path_authority"],
        key_directory=args["key_directory"],
        trusted_public_roots=args["trusted_public_roots"],
    )
    return args, parameters, external, calls


def test_original_execution_reservation_owns_lease_and_both_run_admissions(tmp_path, monkeypatch):
    args, params, connection, calls = _bridge(tmp_path, monkeypatch)
    try:
        assert facts(args, connection) == (None, 0, 0)
        assert connection.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        ticket, state = FleetAdmissionPlanner.reserve_atomic_fleet_prework_ingress(**params)
        assert state is not None
        assert ticket._lease.fleet_run_id == params["contract"].fields["run_id"]
        assert (
            connection.execute("SELECT lease_token FROM execution_lease").fetchone()[0]
            == ticket._lease.token
        )
        assert facts(args, connection)[1:] == (1, 2)
        assert set(calls) == set(params["trusted_public_roots"])
    finally:
        connection.close()


def test_original_transaction_does_not_allow_second_begin_or_external_commit(tmp_path, monkeypatch):
    args, params, connection, _ = _bridge(tmp_path, monkeypatch)
    database = args["journal"]._database
    with database.transaction(write=True) as actual:
        # The old M168 API cannot open another BEGIN on the original handle.
        from oh_goon.core.enforcer_lace_trust import admission_receipt

        causal = CausalRunJournal(actual)
        with pytest.raises(TrustProtocolError, match="outstanding transaction"):
            causal.publish_admission_with_operation(
                params["contract"],
                admission_receipt(params["authenticator"], params["contract"]),
                params["authenticator"],
                control_epoch=1,
                lease_identity="not-a-lease",
                operational_admission=lambda _: None,
            )
        assert actual.in_transaction
    assert facts(args, connection) == (None, 0, 0)
    connection.close()


def test_in_transaction_publication_rejects_foreign_or_missing_transaction(tmp_path, monkeypatch):
    args, params, connection, _ = _bridge(tmp_path, monkeypatch)
    from oh_goon.core.enforcer_lace_trust import admission_receipt

    causal = CausalRunJournal(connection)
    receipt = admission_receipt(params["authenticator"], params["contract"])
    kwargs = dict(
        control_epoch=1, lease_identity="not-a-lease", operational_admission=lambda _: None
    )
    with pytest.raises(TrustProtocolError, match="original active Fleet write transaction"):
        causal.publish_admission_in(
            connection, params["contract"], receipt, params["authenticator"], **kwargs
        )
    with args["journal"]._database.transaction(write=True) as original:
        with pytest.raises(TrustProtocolError, match="original active Fleet write transaction"):
            causal.publish_admission_in(
                original, params["contract"], receipt, params["authenticator"], **kwargs
            )
    assert facts(args, connection) == (None, 0, 0)
    connection.close()


@pytest.mark.parametrize(
    "attack", ["wrong-environment", "revoked", "causal-append", "lease-cancel", "owner-birth"]
)
def test_rejection_rolls_back_original_lease_and_both_run_admissions(tmp_path, monkeypatch, attack):
    args, params, connection, _ = _bridge(tmp_path, monkeypatch)
    from oh_goon.core.errors import OwnershipLost

    if attack == "wrong-environment":
        params["current"] = replace(args["configured"], executor_generation=99)
    elif attack == "revoked":
        owner = args["signer_authority"]
        owner.revoke(expected=owner.active())
    else:
        original = RuntimeCausalRunJournal._append_locked

        def changed(self, run_id, kind, payload):
            value = original(self, run_id, kind, payload)
            if attack == "causal-append":
                raise RuntimeError("causal admission unavailable")
            if attack == "lease-cancel":
                self.connection.execute("UPDATE execution_lease SET cancel_requested_at='now'")
            if attack == "owner-birth":
                env = params["execution"]._leases._environment
                env.identities[env.pid] = "reused"
            return value

        monkeypatch.setattr(RuntimeCausalRunJournal, "_append_locked", changed)
    try:
        with pytest.raises((TrustProtocolError, RuntimeError, OwnershipLost, FleetPlanInvalid)):
            FleetAdmissionPlanner.reserve_atomic_fleet_prework_ingress(**params)
        assert facts(args, connection) == (None, 0, 0)
        assert connection.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
    finally:
        connection.close()
