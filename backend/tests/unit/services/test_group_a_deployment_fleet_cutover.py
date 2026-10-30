"""Public composition defaults to local execution; deployments retain strict admission."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from oh_goon._application_foundation import RuntimeFoundation, build_runtime_foundation
from oh_goon._application_services import _build_fleet_services
from oh_goon.application import ApplicationRuntime
from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.vocabulary import OperationAction
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_live_composition import _real_path


def test_public_application_entrypoint_passes_exact_deployment_provisioner(monkeypatch):
    import oh_goon._application_foundation as foundation
    import oh_goon._application_services as services

    sentinel = object()
    built = object()
    observed = []

    def foundation_builder(
        *,
        fleet_provisioner=None,
        standard_local=False,
        terminal=None,
        standard_process_isolation=None,
        git_service=None,
        startup_purpose="application",
    ):
        assert (terminal, standard_process_isolation, git_service) == (None, None, None)
        assert startup_purpose == "application"
        observed.append((fleet_provisioner, standard_local))
        return built

    monkeypatch.setattr(foundation, "build_runtime_foundation", foundation_builder)
    monkeypatch.setattr(services, "build_product_services", lambda obj: obj)
    assert ApplicationRuntime.build() is built
    assert ApplicationRuntime.build(fleet_provisioner=sentinel) is built
    assert ApplicationRuntime.build(standard_local=True) is built
    assert ApplicationRuntime.build(standard_local=False) is built
    assert observed == [(None, True), (sentinel, False), (None, True), (None, False)]


def test_production_foundation_defaults_are_fail_closed():
    assert RuntimeFoundation.__dataclass_fields__["require_authenticated_fleet"].default is True
    assert RuntimeFoundation.__dataclass_fields__["require_compiled_fleet_contract"].default is True


def test_invalid_deployment_provisioner_rejects_before_database_or_startup(monkeypatch):
    import oh_goon._application_foundation as foundation

    monkeypatch.setattr(
        foundation,
        "_build_local_state_foundation",
        lambda: pytest.fail("must not open startup database"),
    )
    for invalid in (object(), (), "ambient-config"):
        with pytest.raises(TrustProtocolError, match="explicit native authority"):
            build_runtime_foundation(fleet_provisioner=invalid)


@pytest.mark.parametrize("bad", ["legacy", "not-compiled", "preassembled", "fake-provisioner"])
def test_product_composition_cannot_restore_legacy_fleet_path(bad):
    good = dict(
        require_authenticated_fleet=True,
        require_compiled_fleet_contract=True,
        fleet_prework=None,
        fleet_provisioner=None,
        standard_local_fleet=False,
    )
    if bad == "legacy":
        good["require_authenticated_fleet"] = False
    elif bad == "not-compiled":
        good["require_compiled_fleet_contract"] = False
    elif bad == "preassembled":
        good["fleet_prework"] = object()
    else:
        good["fleet_provisioner"] = object()
    with pytest.raises(TrustProtocolError):
        _build_fleet_services(SimpleNamespace(**good), None, None, None)


def test_production_no_enrollment_denied_before_plan_reservation_and_work(tmp_path, monkeypatch):
    args, params, connection, calls, _, _, _ = _real_path(tmp_path, monkeypatch)
    original = FleetRunLifecycle(
        args["journal"]._database,
        args["journal"],
        params["execution"],
        object(),
        require_authenticated_fleet=True,
        require_compiled_fleet_contract=True,
        fleet_provisioner=None,
    )

    class NeverPlanner:
        def plan(self, *a, **kw):
            pytest.fail("no Plan work allowed")

    class NeverWorker:
        def submit(self, *a, **kw):
            pytest.fail("no workload submission allowed")

    control = FleetRunControlPlane(
        NeverPlanner(), args["journal"], original, object(), NeverWorker(), object()
    )
    try:
        intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target)
        with pytest.raises(TrustProtocolError, match="not provisioned"):
            control._start_intent(
                intent,
                repository_scope=args["repository_scope"],
                fleet_run_id="durable-run",
                source=args["intent"].source,
                readmitted_from_run_id=None,
                rerun_from_run_id=None,
            )
        assert facts(args, connection) == (None, 0, 0)
        assert connection.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        connection.close()
