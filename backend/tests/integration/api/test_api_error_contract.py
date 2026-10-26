from contextlib import closing

from oh_goon.api.app import app as api_app
from tests.integration.api.client import authenticated_api_client


def test_api_errors_use_stable_machine_envelope() -> None:
    with closing(authenticated_api_client(api_app)) as client:
        response = client.get("/api/fleet/runs/fleet-does-not-exist")

    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "RUN_NOT_FOUND"
    assert payload["kind"] == "NOT_FOUND"
    assert payload["message"] == "Unknown fleet run: fleet-does-not-exist"
    assert payload["retryable"] is False
    assert payload["details"] == {}
    assert payload["request_id"].startswith("request-")
    assert response.headers["x-request-id"] == payload["request_id"]


def test_validation_errors_use_same_envelope() -> None:
    with closing(authenticated_api_client(api_app)) as client:
        response = client.post("/api/fleet/runs", json={"action": "not-an-action"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert payload["kind"] == "INVALID_REQUEST"
    assert payload["message"] == "Request validation failed"
    assert payload["retryable"] is False
    assert payload["details"]["errors"]
    assert payload["request_id"].startswith("request-")


def test_generic_fleet_run_api_rejects_client_supplied_plan_authority() -> None:
    from tests.factories.schedules import schedule_create

    target = schedule_create(repository_ids=("repo-a",)).target
    with closing(authenticated_api_client(api_app)) as client:
        response = client.post(
            "/api/fleet/runs",
            json={
                "fleet_run_id": "fleet-direct-plan-authority",
                "action": "execute-plan",
                "target": target.model_dump(mode="json"),
                "repository_ids": ["repo-a"],
            },
        )

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "VALIDATION_ERROR"
    assert "Team or Shared Plan boundary" in str(payload["details"])


def test_successful_responses_also_publish_request_correlation() -> None:
    with closing(authenticated_api_client(api_app)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["x-request-id"].startswith("request-")


def test_filesystem_failures_use_domain_error_envelope(tmp_path) -> None:
    missing = tmp_path / "does-not-exist"
    with closing(authenticated_api_client(api_app)) as client:
        response = client.get("/api/filesystem/browse", params={"path": str(missing)})

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "FILESYSTEM_BROWSE_ERROR"
    assert payload["kind"] == "INVALID_REQUEST"
    assert payload["request_id"] == response.headers["x-request-id"]


def test_shared_plan_preview_domain_resolution_failure_is_400_not_500() -> None:
    from oh_goon.api import dependencies
    from oh_goon.core.errors import FleetPlanInvalid

    class InvalidSharedPlanService:
        def preview(self, *args, **kwargs):
            raise FleetPlanInvalid(
                "Fleet repository requirements are supported only for execution.purpose=cicd"
            )

    api_app.dependency_overrides[dependencies.shared_plan_service] = (
        lambda: InvalidSharedPlanService()
    )
    try:
        with closing(authenticated_api_client(api_app)) as client:
            response = client.post(
                "/api/league/shared-plan-previews",
                json={
                    "plan_key": "invalid",
                    "expected_revision": "a" * 64,
                    "repository_ids": ["repo-a"],
                    "inputs": {},
                },
            )
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)

    assert response.status_code == 400
    payload = response.json()
    assert payload["code"] == "FLEET_PLAN_INVALID"
    assert payload["kind"] == "INVALID_REQUEST"
    assert "execution.purpose=cicd" in payload["message"]


def test_shared_plan_history_route_passes_cursor_to_service() -> None:
    from unittest.mock import Mock

    from oh_goon.api import dependencies
    from oh_goon.core.models.shared_plan import SharedPlanRunHistory

    service = Mock()
    service.history.return_value = SharedPlanRunHistory(
        plan_key="python-quality",
        current_sha256="a" * 64,
        runs=(),
        next_before_revision=None,
    )
    api_app.dependency_overrides[dependencies.shared_plan_service] = lambda: service
    try:
        with closing(authenticated_api_client(api_app)) as client:
            response = client.get(
                "/api/league/shared-plans/python-quality/runs",
                params={"limit": 8, "before_revision": 42},
            )
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)

    assert response.status_code == 200
    service.history.assert_called_once_with("python-quality", limit=8, before_revision=42)


def test_shared_plan_template_creation_uses_template_identity_only() -> None:
    from unittest.mock import Mock

    from oh_goon.api import dependencies
    from oh_goon.core.models.shared_plan import SharedPlanDocument

    service = Mock()
    service.create_from_template.return_value = SharedPlanDocument(
        plan_key="python-quality",
        sha256="a" * 64,
        byte_count=32,
        content="version: 1\ngoons:\n- python/quality\n",
    )
    api_app.dependency_overrides[dependencies.shared_plan_service] = lambda: service
    try:
        with closing(authenticated_api_client(api_app)) as client:
            response = client.post(
                "/api/league/shared-plans/from-template",
                json={"template_key": "python/quality", "expected_template_sha256": "b" * 64},
            )
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)

    assert response.status_code == 200
    assert response.json()["plan_key"] == "python-quality"
    service.create_from_template.assert_called_once_with("python/quality", "b" * 64)


def test_shared_plan_document_route_requests_backend_input_projection() -> None:
    from unittest.mock import Mock

    from oh_goon.api import dependencies
    from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputType
    from oh_goon.core.models.shared_plan import SharedPlanDocument

    service = Mock()
    service.read.return_value = SharedPlanDocument(
        plan_key="python-quality",
        sha256="a" * 64,
        byte_count=32,
        content="version: 1\ngoons:\n- python/quality\n",
        inputs=(
            PlanInputDefinition(
                name="coverage_threshold",
                input_type=PlanInputType.INTEGER,
                default=70,
                minimum=70,
                maximum=100,
            ),
        ),
    )
    api_app.dependency_overrides[dependencies.shared_plan_service] = lambda: service
    try:
        with closing(authenticated_api_client(api_app)) as client:
            response = client.get("/api/league/shared-plans/python-quality")
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)

    assert response.status_code == 200
    assert response.json()["inputs"][0]["name"] == "coverage_threshold"
    service.read.assert_called_once_with("python-quality", include_inputs=True)


def test_shared_plan_lock_preview_route_uses_backend_resolved_lock() -> None:
    from unittest.mock import Mock

    from oh_goon.api import dependencies
    from oh_goon.core.models.shared_plan import SharedPlanLockPreview

    service = Mock()
    service.lock_preview.return_value = SharedPlanLockPreview(
        plan_key="python-quality",
        plan_id="plan-1",
        sha256="a" * 64,
        execution_digest="sha256:" + "c" * 64,
        content="# Generated by Oh Goon. Do not edit manually.\nlock_version: 1\nplan: sha256:"
        + "b" * 64
        + "\nexecution: []\ndigest: sha256:"
        + "c" * 64
        + "\n",
    )
    api_app.dependency_overrides[dependencies.shared_plan_service] = lambda: service
    try:
        with closing(authenticated_api_client(api_app)) as client:
            response = client.post(
                "/api/league/shared-plan-lock-previews",
                json={
                    "plan_key": "python-quality",
                    "expected_revision": "a" * 64,
                    "inputs": {"coverage_threshold": "85"},
                },
            )
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)

    assert response.status_code == 200
    assert response.json()["plan_id"] == "plan-1"
    assert "goon" not in response.json()
    service.lock_preview.assert_called_once_with(
        "python-quality",
        inputs={"coverage_threshold": "85"},
        expected_revision="a" * 64,
        agent=None,
        repository_ids=set(),
    )
