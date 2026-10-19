"""B8 negative proof: original issuer/epoch survives the recovery write cut.

TEST KEYS ONLY. A passing test does not provision production signing authority,
independent public roots, external OS custody or native LACE protection.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_recovery_issuer_fence as fence_module
from oh_goon.services import fleet_run_lifecycle as lifecycle_module
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal, SignerTrustEpochAuthority
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c3b5_atomic_restart_transfer import seeded
from tests.unit.services.test_group_a_verified_ingress_authorities import roots


@dataclass
class Configured:
    backend_configuration_identity: str = "backend-one"

    def require_unchanged(self, other):
        if self != other:
            raise TrustProtocolError("native executor configuration changed")


@dataclass
class Binding:
    run_id: str
    key_directory: Path
    trusted_public_roots: dict
    configured: Configured
    current: Configured
    authenticator: object


class Provisioner:
    def __init__(self, binding):
        self.binding = binding
        self.calls = 0
        self.change_on_call = None
        # Test-only positive witness; production calls the exact original
        # FleetPreworkProvisioner method, which reopens all six real roots and
        # checks an independently owned OS directory ancestry.
        import os

        self.public_root_owner_uid = os.geteuid() + 1
        self.custody_calls = 0
        self.custody_attack = None

    def inspect_independent_root_custody(self, run_id):
        import os

        from oh_goon.core.enforcer_lace_trust import TrustProtocolError

        self.custody_calls += 1
        if self.custody_attack == "observer_reject":
            raise TrustProtocolError("independent OS root unavailable")
        report = {
            "schema": "oh-goon.fleet-deployment-os-custody/v1",
            "status": "OBSERVED_NOT_ATTESTED",
            "run_id": run_id,
            "signer_process_euid": os.geteuid(),
            "independent_public_root_owner_uid": self.public_root_owner_uid,
            "issuer_roots_checked": 6,
            "enrollment": "OBSERVED_NOT_ADMITTED",
            "external_provisioning_attestation": "NOT_ESTABLISHED",
        }
        if self.custody_attack == "insufficient_roots":
            report["issuer_roots_checked"] = 5
        if self.custody_attack == "wrong_run":
            report["run_id"] = "foreign-run"
        if self.custody_attack == "foreign_schema":
            report["schema"] = "untrusted"
        if self.custody_attack == "mismatched_owner":
            report["independent_public_root_owner_uid"] += 1
        if self.custody_attack == "changed_signer_uid":
            report["signer_process_euid"] += 1
        if self.custody_attack == "fake_attestation":
            report["external_provisioning_attestation"] = "PROVEN"
        return report

    def resolve(self, run_id):
        self.calls += 1
        if run_id != self.binding.run_id:
            raise TrustProtocolError("wrong Run")
        if self.change_on_call == self.calls:
            return Binding(
                self.binding.run_id,
                self.binding.key_directory,
                self.binding.trusted_public_roots,
                self.binding.configured,
                self.binding.current,
                self.binding.authenticator,
            )
        return self.binding


class StrictPlacement:
    def __init__(self):
        self.placement = SimpleNamespace(
            isolation_backend=SimpleNamespace(
                backend_id="bubblewrap", configuration_id="backend-one"
            ),
            executor_capacity=SimpleNamespace(executor_generation=8),
            executor_trust_epoch=0,
        )


class JournalView:
    def read_view_from_connection(self, connection):
        return self

    def required_run_detail(self, run_id):
        assert run_id == "run-one"
        return SimpleNamespace(
            run=SimpleNamespace(
                status=SimpleNamespace(terminal=False, cancel_request_satisfied=False),
            )
        )


@pytest.fixture
def owner(tmp_path, monkeypatch):
    conn, journal, coordinator, environment = seeded(tmp_path)
    conn.execute(
        "CREATE TABLE executor_placement_queue (fleet_run_id TEXT, cancellation_requested INTEGER)"
    )
    authority = SignerTrustEpochAuthority(conn)
    authority.initialize_schema()
    keys = roots()  # deterministic, test-only signing fixtures
    snapshot = ((), (), b"test-only-native-signer-policy")
    grant = authority.enroll(policy_snapshot=snapshot, roots=keys)
    binding = Binding("run-one", tmp_path, keys, Configured(), Configured(), authenticator())
    provisioner = Provisioner(binding)

    monkeypatch.setattr(fence_module, "FleetPreworkProvisioner", Provisioner)
    monkeypatch.setattr(fence_module, "StrictFleetPolicyEnrollment", Binding)
    monkeypatch.setattr(fence_module, "deployment_signer_policy_snapshot", lambda **_kw: snapshot)
    # The B5 test-only Run predates admission signer-epoch binding. This
    # fixture models the separately verified original B8 signed record so
    # transaction rollback regressions isolate the service boundary. Actual
    # cryptographic verification is tested against strict atomic ingress below.
    from oh_goon.runtime import enforcer_lace_admitted_signer_epoch as admitted_epoch

    def original_binding(_journal, *, run_id, auth, expected):
        if run_id != "run-one" or auth.key_id != "key-1" or expected != grant:
            raise TrustProtocolError("original Run signer epoch revoked or rotated since admission")

    monkeypatch.setattr(
        admitted_epoch, "require_original_admitted_signer_epoch_in", original_binding
    )
    monkeypatch.setattr(lifecycle_module, "FleetPreworkProvisioner", Provisioner)
    monkeypatch.setattr(lifecycle_module, "StrictFleetPolicyEnrollment", Binding)
    monkeypatch.setattr(lifecycle_module, "StrictFleetExecutionAdmission", StrictPlacement)
    monkeypatch.setattr(
        lifecycle_module,
        "_execution_authority_in",
        lambda *_args: SimpleNamespace(execution=StrictPlacement()),
    )
    service = FleetRunLifecycle.__new__(FleetRunLifecycle)
    service._require_authenticated_fleet = True
    service._fleet_provisioner = provisioner
    service._database = coordinator._leases._database
    service._journal = JournalView()
    service._executor_pool = object()
    service._execution = coordinator
    yield (conn, journal, service, provisioner, authority, grant, snapshot)
    conn.close()


def _snapshot(conn):
    return (
        [tuple(row) for row in conn.execute("SELECT * FROM execution_lease")],
        [tuple(row) for row in conn.execute("SELECT * FROM lace_run_authority")],
        [tuple(row) for row in conn.execute("SELECT * FROM lace_run_events")],
    )


def _transfer(service):
    return service.transfer_original_dead_fleet_recovery(
        run_id="run-one",
        predecessor_token="lease-one",
        expected_epoch=1,
    )


def test_captured_original_signer_epoch_allows_only_cleanup_custody(owner):
    conn, journal, service, provisioner, *_ = owner
    ticket = _transfer(service)
    assert ticket.recovery_only
    assert provisioner.calls == 3  # preflight, pre-write, precommit
    events = CausalRunJournal(conn).verify("run-one", auth=authenticator())
    assert [e["kind"] for e in events].count("RUN_RECOVERY_CUSTODY_TRANSFERRED") == 1
    assert events[-1]["payload"]["binding"]["step_replay_permitted"] is False


@pytest.mark.parametrize(
    "attack",
    [
        "unprovisioned",
        "revoked",
        "rotated",
        "new-enrollment",
        "different-configuration",
        "changed-precommit-enrollment",
    ],
)
def test_recovery_issuer_preflight_fails_without_original_authority(owner, attack):
    conn, journal, service, provisioner, authority, grant, _ = owner
    if attack == "unprovisioned":
        conn.execute("DELETE FROM lace_signer_trust_epoch")
    if attack == "revoked":
        authority.revoke(expected=grant)
    if attack == "rotated":
        conn.execute("UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1")
    if attack == "new-enrollment":
        provisioner.change_on_call = 2
    if attack == "different-configuration":
        provisioner.binding.current = Configured("wrong-backend")
    if attack == "changed-precommit-enrollment":
        provisioner.change_on_call = 3
    original = _snapshot(conn)
    with pytest.raises(TrustProtocolError):
        _transfer(service)
    assert _snapshot(conn) == original
    assert journal._row("run-one")[1:3] == (1, "lease-one")


@pytest.mark.parametrize(
    "attack",
    [
        "revoke-after-signed-write",
        "epoch-change-after-signed-write",
        "policy-drift-after-signed-write",
        "foreign-roots-after-signed-write",
    ],
)
def test_postwrite_issuer_drift_rolls_back_lease_and_signed_journal(owner, monkeypatch, attack):
    conn, journal, service, provisioner, authority, grant, snapshot = owner
    original = _snapshot(conn)
    # The production function is imported inside the method; intercept at its
    # owning module rather than introducing a second transfer authority.
    from oh_goon.runtime import enforcer_lace_restart_transfer as transfer_module

    actual = transfer_module.commit_original_restart_transfer_in

    def provisional(**kwargs):
        ticket = actual(**kwargs)
        if attack == "revoke-after-signed-write":
            conn.execute(
                "UPDATE lace_signer_trust_epoch SET state='REVOKED',trust_epoch=trust_epoch+1"
            )
        elif attack == "epoch-change-after-signed-write":
            conn.execute("UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1")
        elif attack == "policy-drift-after-signed-write":
            monkeypatch.setattr(
                fence_module,
                "deployment_signer_policy_snapshot",
                lambda **_kw: ((), (), b"test-only-mutated-policy"),
            )
        elif attack == "foreign-roots-after-signed-write":
            provisioner.binding.trusted_public_roots = {
                **provisioner.binding.trusted_public_roots,
                next(iter(provisioner.binding.trusted_public_roots)): ("foreign-root", b"F" * 32),
            }
        return ticket

    monkeypatch.setattr(transfer_module, "commit_original_restart_transfer_in", provisional)
    with pytest.raises(TrustProtocolError):
        _transfer(service)
    assert _snapshot(conn) == original
    assert journal._row("run-one")[1:3] == (1, "lease-one")
    assert tuple(
        conn.execute("SELECT trust_epoch,state FROM lace_signer_trust_epoch").fetchone()
    ) == (grant.epoch, "ACTIVE")


def test_standalone_or_foreign_connection_cannot_assert_issuer_fence(owner, tmp_path):
    conn, journal, service, provisioner, authority, grant, snapshot = owner
    binding = provisioner.binding
    with pytest.raises(TrustProtocolError, match="transaction"):
        fence_module.require_original_restart_issuer_in(
            conn, provisioner=provisioner, binding=binding, run_id="run-one", grant=grant
        )
    # Even an active transaction with an epoch row in a *different* database
    # must not inherit original authority; its active grant is absent.
    import sqlite3

    foreign = sqlite3.connect(tmp_path / "foreign.sqlite", isolation_level=None)
    foreign.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises((TrustProtocolError, sqlite3.OperationalError)):
            fence_module.require_original_restart_issuer_in(
                foreign, provisioner=provisioner, binding=binding, run_id="run-one", grant=grant
            )
    finally:
        foreign.execute("ROLLBACK")
        foreign.close()


def test_strict_atomic_ingress_records_cryptographically_signed_epoch(tmp_path, monkeypatch):
    from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
        require_original_admitted_signer_epoch_in,
    )
    from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import prepared, publish

    args, conn, _calls = prepared(tmp_path, monkeypatch)
    try:
        original = args["signer_authority"].active()
        publish(args)
        run_id = args["contract"].fields["run_id"]
        events = args["causal_journal"].verify(run_id, auth=args["authenticator"])
        assert [e["kind"] for e in events] == ["RUN_ADMITTED", "RUN_SIGNER_EPOCH_BOUND"]
        conn.execute("BEGIN IMMEDIATE")
        try:
            require_original_admitted_signer_epoch_in(
                args["causal_journal"], run_id=run_id, auth=args["authenticator"], expected=original
            )
            from dataclasses import replace

            with pytest.raises(TrustProtocolError, match="revoked or rotated"):
                require_original_admitted_signer_epoch_in(
                    args["causal_journal"],
                    run_id=run_id,
                    auth=args["authenticator"],
                    expected=replace(original, epoch=original.epoch + 1),
                )
        finally:
            conn.execute("ROLLBACK")
    finally:
        conn.close()


@pytest.mark.parametrize("attack", ["mutated-mac", "mutated-binding", "foreign-run-binding"])
def test_admission_epoch_signature_or_order_mutation_rejected(tmp_path, monkeypatch, attack):
    import json

    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.runtime.enforcer_lace_journal import _event_identity
    from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import prepared, publish

    args, conn, _ = prepared(tmp_path, monkeypatch)
    try:
        publish(args)
        run_id = args["contract"].fields["run_id"]
        journal = args["causal_journal"]
        original = conn.execute(
            "SELECT event_payload,previous_identity FROM lace_run_events WHERE run_id=? AND run_seq=2",
            (run_id,),
        ).fetchone()
        item = json.loads(original[0])
        if attack == "mutated-mac":
            item["signature"] = "0" * 64
        elif attack == "mutated-binding":
            item["binding"]["trust_epoch"] += 1
        else:
            item["binding"]["run_id"] = "different-run"
        encoded = canonical_bytes(item)
        changed_identity = _event_identity(
            run_id, 2, "RUN_SIGNER_EPOCH_BOUND", encoded, original[1]
        )
        conn.execute(
            "UPDATE lace_run_events SET event_payload=?, event_identity=? WHERE run_id=? AND run_seq=2",
            (encoded, changed_identity, run_id),
        )
        conn.execute(
            "UPDATE lace_run_authority SET head_identity=? WHERE run_id=?",
            (changed_identity, run_id),
        )
        with pytest.raises(TrustProtocolError):
            journal.verify(run_id, auth=args["authenticator"])
    finally:
        conn.close()


def test_legacy_run_without_signed_admission_epoch_cannot_be_promoted(tmp_path):
    from oh_goon.runtime.enforcer_lace_admitted_signer_epoch import (
        require_original_admitted_signer_epoch_in,
    )

    conn, journal, coordinator, _environment = seeded(tmp_path)
    issuer = SignerTrustEpochAuthority(conn)
    issuer.initialize_schema()
    grant = issuer.enroll(policy_snapshot=((), (), b"test-only-native-policy"), roots=roots())
    original = _snapshot(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(TrustProtocolError, match="signed Run signer epoch missing"):
            require_original_admitted_signer_epoch_in(
                journal, run_id="run-one", auth=authenticator(), expected=grant
            )
    finally:
        conn.execute("ROLLBACK")
    assert _snapshot(conn) == original
    conn.close()


def test_duplicate_signed_epoch_is_not_a_second_authority(tmp_path, monkeypatch):
    from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import prepared, publish

    args, conn, _ = prepared(tmp_path, monkeypatch)
    try:
        publish(args)
        run_id = args["contract"].fields["run_id"]
        journal = args["causal_journal"]
        events = journal.verify(run_id, auth=args["authenticator"])
        conn.execute("BEGIN IMMEDIATE")
        try:
            journal._append_locked(run_id, "RUN_SIGNER_EPOCH_BOUND", events[1]["payload"])
            with pytest.raises(TrustProtocolError, match="duplicated original signed issuer epoch"):
                journal.verify(run_id, auth=args["authenticator"])
        finally:
            conn.execute("ROLLBACK")
    finally:
        conn.close()


def test_activated_cleanup_owner_rechecks_epoch_on_each_read_write_cut(owner, monkeypatch):
    conn, journal, service, provisioner, authority, grant, snapshot = owner
    ticket = _transfer(service)
    session = service.activate_recovery(ticket)

    class Expected:
        def __init__(self):
            self.execution = StrictPlacement()

    monkeypatch.setattr(lifecycle_module, "FleetExecutionAuthority", Expected)
    service.require_execution_authority_in = lambda *_a: None
    expected = Expected()
    local = service.local_lace_recovery_owner(session, expected)
    assert local.run_id == "run-one"
    prior = _snapshot(conn)
    authority.revoke(expected=grant)
    with pytest.raises(TrustProtocolError, match="no active durable signer trust epoch"):
        local.pending_takeover_recovery()
    assert _snapshot(conn) == prior
