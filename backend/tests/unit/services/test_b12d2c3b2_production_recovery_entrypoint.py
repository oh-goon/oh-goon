"""B12D2C3B2: production Fleet recovery never acquires independent authority."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import OwnershipLost, StateConflict
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from oh_goon.runtime.execution import ExecutionSession, ExecutionTicket
from oh_goon.services.fleet_local_lace_takeover import OriginalLocalLaceTakeoverCoordinator
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetExecutionAuthority, FleetRunLifecycle
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2c3b_takeover_recovery import setup_takeover


def coordinator(tmp_path):
    return OriginalLocalLaceTakeoverCoordinator(
        tmp_path / "cg",
        tmp_path / "cg-state",
        tmp_path / "cg.lock",
        tmp_path / "net-state",
        tmp_path / "net.lock",
        "/usr/bin/true",
        "/usr/bin/true",
    )


def production_session(monkeypatch, journal, owner, tmp_path):
    """Exercise real ExecutionSession and FleetRunLifecycle code with in-memory lease stubs."""
    from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
    from oh_goon.services import fleet_local_lace_takeover as takeover
    from oh_goon.services import fleet_run_lifecycle as module

    connection = journal.connection

    class LeaseStore:
        cancelled = False

        def mutate_as_owner(self, lease, callback):
            connection.execute("BEGIN IMMEDIATE")
            try:
                value = callback(connection)
                connection.execute("COMMIT")
                return value
            except BaseException:
                connection.execute("ROLLBACK")
                raise

        def cancellation_requested_in(self, c, lease):
            assert c is connection
            return self.cancelled

    leases = LeaseStore()
    lease = SimpleNamespace(kind="FLEET", fleet_run_id="run-one", token="new")
    session = ExecutionSession(leases, lease)
    backend = SimpleNamespace(backend_id="bubblewrap", configuration_id="sha256:" + "a" * 64)
    strict = StrictFleetExecutionAdmission.model_construct(
        placement=SimpleNamespace(
            isolation_backend=backend, executor_capacity=SimpleNamespace(executor_generation=8)
        )
    )
    expected = FleetExecutionAuthority(None, None, None, (), strict, ())

    class Provisioner:
        def resolve(self, run_id):
            assert run_id == "run-one"
            return self.binding

    class Enrollment:
        pass

    configured = SimpleNamespace(
        backend_configuration_identity=backend.configuration_id,
        require_unchanged=lambda other: None,
    )
    provisioner = Provisioner()
    provisioner.binding = Enrollment()
    provisioner.binding.configured = configured
    provisioner.binding.current = configured
    provisioner.binding.authenticator = authenticator()
    monkeypatch.setattr(module, "FleetPreworkProvisioner", Provisioner)
    monkeypatch.setattr(module, "StrictFleetPolicyEnrollment", Enrollment)
    monkeypatch.setattr(module, "_execution_authority_in", lambda *_args: expected)
    lifecycle = object.__new__(FleetRunLifecycle)
    lifecycle._journal = object()
    lifecycle._executor_pool = object()
    lifecycle._require_authenticated_fleet = True
    lifecycle._fleet_provisioner = provisioner
    native = []

    def observe(subject, **kwargs):
        native.append(subject)
        return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")

    monkeypatch.setattr(takeover, "observe_native_crash", observe)
    from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation

    original_backend = BubblewrapProcessIsolation(
        executable="/usr/bin/true",
        ip_executable="/usr/bin/true",
        nft_executable="/usr/bin/true",
        cgroup_root=tmp_path / "cg",
        cgroup_state_dir=tmp_path / "cg-state",
        cgroup_lock=tmp_path / "cg.lock",
        egress_state_dir=tmp_path / "net-state",
        egress_lock=tmp_path / "net.lock",
        require_local_resource_intent=True,
    )

    class ActiveLifecycle:
        original_local_lace_recovery_backend = staticmethod(lambda _expected: original_backend)
        execution_connection_scope = staticmethod(nullcontext)
        activate = staticmethod(lambda ticket: session)
        execution_authority = staticmethod(lambda session: expected)
        local_lace_recovery_owner = lifecycle.local_lace_recovery_owner

    execution = FleetRunExecution(ActiveLifecycle(), object(), object())
    ticket = ExecutionTicket(lease)
    return execution, session, leases, native, ticket, provisioner, lifecycle, expected


def test_production_worker_recovery_uses_original_enrolment_and_sqlite_session(
    monkeypatch, tmp_path
):
    conn, journal, owner = setup_takeover(tmp_path)
    execution, session, leases, native, ticket, *_ = production_session(
        monkeypatch, journal, owner, tmp_path
    )
    claim, receipt = execution.resume_local_lace_takeover(
        ticket, coordinator(tmp_path), intent_run_seq=2
    )
    events = journal.verify("run-one", auth=authenticator())
    assert events[-2]["run_seq"] == claim and events[-2]["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    assert events[-1]["run_seq"] == receipt and events[-1]["kind"] == "RESOURCE_CRASH_RECONCILED"
    assert events[-1]["payload"]["cleanup_permitted"] is False
    assert len(native) == 1
    with pytest.raises(CausalJournalError):
        execution.resume_local_lace_takeover(ticket, coordinator(tmp_path), intent_run_seq=2)
    assert len(native) == 1
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "foreign-ticket",
        "wrong-lease",
        "cancelled",
        "lost-execution-authority",
        "unprovisioned",
        "direct",
        "remote",
        "backend-drift",
        "no-enrolment",
        "finished-session",
        "invalid-coordinator",
        "invalid-intent",
    ],
)
def test_production_recovery_rejects_before_native_and_claim(monkeypatch, tmp_path, attack):
    conn, journal, owner = setup_takeover(tmp_path)
    execution, session, leases, native, ticket, provisioner, lifecycle, expected = (
        production_session(monkeypatch, journal, owner, tmp_path)
    )
    if attack == "foreign-ticket":
        ticket = object()
    if attack == "wrong-lease":
        session._lease.token = "foreign"
    if attack == "cancelled":
        leases.cancelled = True
    if attack == "lost-execution-authority":
        from oh_goon.services import fleet_run_lifecycle as module

        monkeypatch.setattr(module, "_execution_authority_in", lambda *_args: None)
    if attack == "unprovisioned":
        lifecycle._require_authenticated_fleet = False
    if attack == "direct":
        from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission

        expected = replace(expected, execution=DirectFleetExecutionAdmission())
        execution._lifecycle.execution_authority = staticmethod(lambda _: expected)
    if attack == "remote":
        expected.execution.placement.isolation_backend.backend_id = "opensandbox"
    if attack == "backend-drift":
        provisioner.binding.configured.backend_configuration_identity = "sha256:" + "b" * 64
    if attack == "no-enrolment":
        provisioner.binding = object()
    if attack == "finished-session":
        session._finished = True
    setup = coordinator(tmp_path)
    if attack == "invalid-coordinator":
        setup = object()
    seq = 1 if attack == "invalid-intent" else 2
    before = journal.verify("run-one", auth=authenticator())
    with pytest.raises((CausalJournalError, TrustProtocolError, OwnershipLost, StateConflict)):
        execution.resume_local_lace_takeover(ticket, setup, intent_run_seq=seq)
    assert not native and journal.verify("run-one", auth=authenticator()) == before
    conn.close()


def test_control_plane_delegates_without_new_signer_or_startup_admission():
    from unittest.mock import Mock

    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane

    executor = Mock()
    executor.resume_local_lace_takeover.return_value = (4, 5)
    plane = object.__new__(FleetRunControlPlane)
    plane._run_execution = executor
    ticket = object()
    coordinator_instance = object()
    assert plane.resume_local_lace_takeover(ticket, coordinator_instance, intent_run_seq=7) == (
        4,
        5,
    )
    executor.resume_local_lace_takeover.assert_called_once_with(
        ticket, coordinator_instance, intent_run_seq=7
    )


def test_production_worker_takeover_during_native_scan_retains_only_unresolved_claim(
    monkeypatch, tmp_path
):
    from oh_goon.core.ports.local_lace_crash_reconciliation import LocalLaceCrashObservation
    from oh_goon.services import fleet_local_lace_takeover as takeover

    conn, journal, owner = setup_takeover(tmp_path)
    execution, session, leases, native, ticket, *_ = production_session(
        monkeypatch, journal, owner, tmp_path
    )

    def lose_lease_during_scan(subject, **kwargs):
        session._lease.token = "foreign-new-controller"
        return LocalLaceCrashObservation(subject.subject_identity, "UNRESOLVED")

    monkeypatch.setattr(takeover, "observe_native_crash", lose_lease_during_scan)
    with pytest.raises(CausalJournalError, match="lease"):
        execution.resume_local_lace_takeover(ticket, coordinator(tmp_path), intent_run_seq=2)
    last = journal.verify("run-one", auth=authenticator())[-1]
    assert last["kind"] == "RESOURCE_RECOVERY_CLAIMED"
    assert last["payload"]["control_epoch"] == 2
    assert last["payload"]["cleanup_only"] is True
    conn.close()
