"""M182: negative-evidence guard for generic Fleet HTTP operation admission.

Generic non-Plan actions keep their original Fleet lifecycle. Fleet Plans must
enter the canonical prepared-Plan owner, never the generic HTTP/control path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api import dependencies
from oh_goon.api.routes import fleet_runs
from oh_goon.api.schemas.fleet import FleetRunRequest
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource, OperationAction
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


def _plan_target(tmp_path: Path) -> FleetPlanTarget:
    source = tmp_path / "goon.yml"
    source.write_text(
        "version: 1\nexecution:\n  purpose: verification\nsteps:\n  - id: check\n    run: 'true'\n"
    )
    return structural_fleet_plan_target(FleetPlanResolver().resolve(source))


def _http(manager: object) -> TestClient:
    app = FastAPI()
    app.include_router(fleet_runs.router, prefix="/api")
    app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager
    return TestClient(app)


@pytest.mark.parametrize(
    "payload",
    [
        {"fleet_run_id": "run", "action": "execute-plan"},
        {"fleet_run_id": "run", "action": "status", "target": {"kind": "fleet_plan"}},
        {"fleet_run_id": "run", "action": "status", "repository_ids": ["repo-a"]},
        {"fleet_run_id": "run", "action": "status", "repository_instance_ids": {"repo-a": "x"}},
        {"fleet_run_id": "run", "action": "status", "all_repositories": True},
        {
            "fleet_run_id": "run",
            "action": "status",
            "repository_ids": ["repo-a", "repo-a"],
            "repository_instance_ids": {"repo-a": "x"},
        },
        {
            "fleet_run_id": "run",
            "action": "status",
            "repository_ids": ["repo-a"],
            "repository_instance_ids": {"repo-b": "x"},
        },
    ],
)
def test_http_generic_rejects_plan_and_unauthorized_selection_before_control_plane(payload):
    manager = Mock(spec=FleetRunControlPlane)
    with _http(manager) as client:
        response = client.post("/api/fleet/runs", json=payload)
    assert response.status_code == 422
    manager.start.assert_not_called()
    manager.start_prepared_plan.assert_not_called()


def test_direct_generic_control_plane_denies_execute_plan_before_routing(monkeypatch):
    from oh_goon.services import fleet_run_control_plane as module

    monkeypatch.setattr(
        module,
        "resolve_capability_intent",
        lambda *a, **kw: pytest.fail("generic path must not resolve Fleet Plan"),
    )
    manager = object.__new__(FleetRunControlPlane)
    manager._start_intent = Mock(side_effect=AssertionError("must not start"))
    with pytest.raises(StateConflict, match="FleetPlanService"):
        manager.start(OperationAction.EXECUTE_PLAN, fleet_run_id="run")
    manager._start_intent.assert_not_called()


def test_direct_generic_control_plane_denies_plan_shaped_target_before_routing(monkeypatch):
    from oh_goon.services import fleet_run_control_plane as module

    monkeypatch.setattr(
        module,
        "resolve_capability_intent",
        lambda *a, **kw: pytest.fail(
            "Plan-shaped target must not enter generic capability routing"
        ),
    )
    manager = object.__new__(FleetRunControlPlane)
    manager._start_intent = Mock(side_effect=AssertionError("must not start"))
    # Construct only the shape: no Plan loading/locking is needed to test this fence.
    target = FleetPlanTarget.model_construct(kind="fleet_plan")
    with pytest.raises(StateConflict, match="FleetPlanService"):
        manager.start(OperationAction.STATUS, target=target, fleet_run_id="run")
    manager._start_intent.assert_not_called()


def test_valid_frozen_plan_target_is_rejected_by_generic_http_schema(tmp_path):
    target = _plan_target(tmp_path)
    manager = Mock(spec=FleetRunControlPlane)
    with _http(manager) as client:
        response = client.post(
            "/api/fleet/runs",
            json={
                "fleet_run_id": "forged",
                "action": "status",
                "target": target.model_dump(mode="json"),
            },
        )
    assert response.status_code == 422
    assert "Fleet Plan execution must use" in str(response.json())
    manager.start.assert_not_called()
    manager.start_prepared_plan.assert_not_called()


def test_http_generic_valid_non_plan_identity_priority_and_scope_are_forwarded_unchanged():
    manager = Mock(spec=FleetRunControlPlane)
    manager.start.return_value = Mock()
    payload = {
        "fleet_run_id": "explicit-run",
        "action": "status",
        "repository_ids": ["repo-a"],
        "repository_instance_ids": {"repo-a": "instance-1"},
        "requested_priority": "HIGH",
    }
    request = FleetRunRequest.model_validate(payload)
    assert request.fleet_run_id == "explicit-run"
    assert request.requested_priority is SchedulingPriority.HIGH
    # FastAPI response serialization depends on a full FleetRunState; inspect the
    # route's exact delegation without substituting another admission owner.
    result = fleet_runs.start_fleet_run(request, control_plane=manager)
    assert result is manager.start.return_value
    manager.start.assert_called_once_with(
        OperationAction.STATUS,
        target=None,
        repository_ids={"repo-a"},
        all_repositories=False,
        expected_repository_instance_ids={"repo-a": "instance-1"},
        fleet_run_id="explicit-run",
        requested_priority=SchedulingPriority.HIGH,
    )
    manager.start_prepared_plan.assert_not_called()


def test_original_non_plan_control_plane_still_routes_through_single_start_intent(monkeypatch):
    from oh_goon.services import fleet_run_control_plane as module

    intent = module.resolve_capability_intent(OperationAction.STATUS)
    manager = object.__new__(FleetRunControlPlane)
    manager._start_intent = Mock(return_value=object())
    result = manager.start(
        OperationAction.STATUS,
        repository_ids={"repo-a"},
        fleet_run_id="explicit-run",
        expected_repository_instance_ids={"repo-a": "instance-1"},
        requested_priority=SchedulingPriority.HIGH,
    )
    assert result is manager._start_intent.return_value
    call = manager._start_intent.call_args
    assert call.args == (intent,)
    assert call.kwargs["repository_scope"].repository_ids == ("repo-a",)
    assert call.kwargs["fleet_run_id"] == "explicit-run"
    assert call.kwargs["expected_repository_instance_ids"] == {"repo-a": "instance-1"}
    assert call.kwargs["source"] is FleetRunSource.MANUAL
    assert call.kwargs["requested_priority"] is SchedulingPriority.HIGH
    assert call.kwargs["readmitted_from_run_id"] is None
    assert call.kwargs["rerun_from_run_id"] is None
