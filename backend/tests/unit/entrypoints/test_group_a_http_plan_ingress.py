"""M180: HTTP Shared Plan execution rejects unprovisioned Runs before source work.

The HTTP adapter and direct SharedPlanService caller share the original lifecycle
membership fence. This is not final issuer authorization and never creates a lease.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.api.routes.league import run_shared_plan
from oh_goon.api.schemas.league import SharedPlanRunRequest
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.shared_plans import SharedPlanService


class NoSourceStore:
    def read(self, *args, **kwargs):
        pytest.fail("unprovisioned HTTP Run must not read Shared Plan source")

    def path(self, *args, **kwargs):
        pytest.fail("unprovisioned HTTP Run must not select a Plan path")


def provisioner(ids: tuple[str, ...]) -> FleetPreworkProvisioner:
    # Source/issuer/placement qualifications of real enrollment are separate.
    value = object.__new__(FleetPreworkProvisioner)
    object.__setattr__(value, "enrolled", tuple(SimpleNamespace(run_id=i) for i in ids))
    return value


def service(*, provisioned: FleetPreworkProvisioner | None, authenticated=True, store=None):
    owner = object.__new__(FleetRunLifecycle)
    owner._require_authenticated_fleet = authenticated
    owner._fleet_provisioner = provisioned
    control = object.__new__(FleetRunControlPlane)
    control._lifecycle = owner
    plans = FleetPlanService(object(), control, object(), object(), object())
    plans.run_reviewed = Mock(
        side_effect=AssertionError("no execution before source is reviewed")
    )
    return SharedPlanService(
        store if store is not None else NoSourceStore(),
        object(), plans, object(), object(),
    ), plans


def run_kwargs(run_id):
    return dict(
        plan_key="release-plan",
        repository_ids={"repo-a"},
        inputs={"version": "1"},
        fleet_run_id=run_id,
        expected_plan_id="reviewed-id",
        expected_execution_digest="sha256:" + "a" * 64,
        expected_plan_revision="b" * 64,
        repository_instance_ids={"repo-a": "instance-1"},
    )


def http_request(run_id: str) -> SharedPlanRunRequest:
    return SharedPlanRunRequest(**run_kwargs(run_id))


@pytest.mark.parametrize("run_id", [None, "", "forged", 42, True])
def test_http_shared_plan_direct_caller_rejects_unenrolled_before_source(run_id):
    shared, plans = service(provisioned=provisioner(("known",)))
    with pytest.raises(TrustProtocolError):
        shared.run(**run_kwargs(run_id))
    plans.run_reviewed.assert_not_called()


@pytest.mark.parametrize("run_id", [None, "", "unknown"])
def test_http_shared_plan_rejects_absent_deployment_before_source(run_id):
    shared, plans = service(provisioned=None)
    with pytest.raises(TrustProtocolError, match="not provisioned"):
        shared.run(**run_kwargs(run_id))
    plans.run_reviewed.assert_not_called()


def test_http_route_rejects_unenrolled_run_before_source_or_native_issuer():
    shared, plans = service(provisioned=provisioner(("known",)))
    with pytest.raises(StateConflict, match="valid authenticated admission authority"):
        run_shared_plan(http_request("forged"), service=shared)
    plans.run_reviewed.assert_not_called()


def test_http_preflight_does_not_observe_signers_or_reserve(monkeypatch):
    enrolled = provisioner(("known",))
    monkeypatch.setattr(
        FleetPreworkProvisioner,
        "_observe",
        lambda *a, **kw: pytest.fail("early gate observed signer"),
    )
    shared, plans = service(provisioned=enrolled)
    # A valid Run reaches Plan source after membership, but not final admission.
    with pytest.raises(
        pytest.fail.Exception,
        match="unprovisioned HTTP Run must not read Shared Plan source",
    ):
        shared.run(**run_kwargs("known"))
    plans.run_reviewed.assert_not_called()
def test_http_exact_enrollment_preserves_reviewed_plan_and_instance_forwarding():
    document = SimpleNamespace(plan_key="release-plan", sha256="b" * 64)
    store = SimpleNamespace(
        read=Mock(return_value=document),
        path=Mock(return_value=Path("/frozen/shared/release-plan/plan.yml")),
    )
    shared, plans = service(provisioned=provisioner(("known",)), store=store)
    admitted = object()
    plans.run_reviewed = Mock(return_value=admitted)
    result = run_shared_plan(http_request("known"), service=shared)
    assert result is admitted
    store.read.assert_called_once_with("release-plan", expected_revision="b" * 64)
    store.path.assert_called_once_with("release-plan")
    call = plans.run_reviewed.call_args
    assert call.args == (Path("/frozen/shared/release-plan/plan.yml"),)
    assert call.kwargs["fleet_run_id"] == "known"
    assert call.kwargs["repository_ids"] == {"repo-a"}
    assert call.kwargs["expected_repository_instance_ids"] == {"repo-a": "instance-1"}
    assert call.kwargs["source"].plan_key == "release-plan"
    assert call.kwargs["source"].sha256 == "b" * 64
    assert call.kwargs["expected_plan_id"] == "reviewed-id"
    assert call.kwargs["expected_execution_digest"] == "sha256:" + "a" * 64
    assert call.kwargs["inputs"] == {"version": "1"}


def test_http_repo_scope_mismatch_still_denies_before_resolver():
    document = SimpleNamespace(plan_key="release-plan", sha256="b" * 64)
    store = SimpleNamespace(read=Mock(return_value=document), path=Mock())
    shared, plans = service(provisioned=provisioner(("known",)), store=store)
    kwargs = run_kwargs("known")
    kwargs["repository_instance_ids"] = {"other": "instance-1"}
    with pytest.raises(FleetPlanInvalid, match="exactly match"):
        shared.run(**kwargs)
    plans.run_reviewed.assert_not_called()
    store.path.assert_not_called()


def test_nonproduction_http_plan_preflight_stays_noop_before_source():
    shared, plans = service(provisioned=None, authenticated=False)
    with pytest.raises(
        pytest.fail.Exception,
        match="unprovisioned HTTP Run must not read Shared Plan source",
    ):
        shared.run(**run_kwargs(None))
    plans.run_reviewed.assert_not_called()


def test_cli_alias_uses_same_original_membership_owner():
    _, plans = service(provisioned=provisioner(("known",)))
    plans.require_cli_plan_preparation("known")
    with pytest.raises(TrustProtocolError):
        plans.require_cli_plan_preparation("forged")


def test_real_http_request_reports_generic_conflict_not_internal_error():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from oh_goon.api import dependencies
    from oh_goon.api.errors import domain_exception_handler
    from oh_goon.api.routes import league
    from oh_goon.core.errors import FleetDomainError

    shared, plans = service(provisioned=provisioner(("known",)))
    app = FastAPI()
    app.add_exception_handler(FleetDomainError, domain_exception_handler)
    app.include_router(league.router, prefix="/api")
    app.dependency_overrides[dependencies.shared_plan_service] = lambda: shared
    with TestClient(app) as client:
        response = client.post(
            "/api/league/shared-plan-runs",
            json={**run_kwargs("forged"), "repository_ids": ["repo-a"]},
        )
    assert response.status_code == 409
    payload = response.json()
    assert payload["kind"] == "CONFLICT"
    assert "authenticated admission authority" in payload["message"]
    assert "provisioner" not in payload["message"]
    assert "signer" not in payload["message"]
    plans.run_reviewed.assert_not_called()
