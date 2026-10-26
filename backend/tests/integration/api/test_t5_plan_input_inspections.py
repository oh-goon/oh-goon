"""T5 read-only API contract: one canonical resolver result, no raw exception leaks."""

from contextlib import closing
from unittest.mock import Mock

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.core.errors import FleetPlanInputRejected, FleetPlanInvalid, FleetPlanSourceStale
from tests.integration.api.client import authenticated_api_client

REV = "a" * 64
URL = "/api/league/shared-plan-input-inspections"


def call(service, request=None):
    api_app.dependency_overrides[dependencies.shared_plan_service] = lambda: service
    try:
        with closing(authenticated_api_client(api_app)) as client:
            return client.post(
                URL,
                json=request
                or {
                    "plan_key": "python-quality",
                    "expected_revision": REV,
                    "inputs": {"coverage_threshold": "85"},
                },
            )
    finally:
        api_app.dependency_overrides.pop(dependencies.shared_plan_service, None)


def test_inspection_accepted_reports_source_identity_but_never_lock_or_run():
    service = Mock()
    service.inspect_inputs.return_value = ("python-quality", REV, "plan-1")
    response = call(service)
    assert response.status_code == 200
    assert response.json() == {
        "schema": "oh-goon.shared-plan-input-inspection/v1",
        "status": "accepted",
        "plan_key": "python-quality",
        "sha256": REV,
        "plan_id": "plan-1",
        "diagnostic": None,
        "execution": "not_started_by_inspection",
    }
    service.inspect_inputs.assert_called_once_with(
        "python-quality",
        expected_revision=REV,
        inputs={"coverage_threshold": "85"},
        agent=None,
        repository_ids=set(),
    )
    service.lock_preview.assert_not_called()
    service.run.assert_not_called()


@pytest.mark.parametrize(
    "failure,code,names",
    [
        (
            FleetPlanInputRejected(
                "PRIVATE_SSH_KEY secret",
                diagnostic_code="INVALID_INPUT_VALUE",
                input_names=("coverage_threshold",),
            ),
            "INVALID_INPUT_VALUE",
            ["coverage_threshold"],
        ),
        (
            FleetPlanInputRejected(
                "PRIVATE_SSH_KEY secret",
                diagnostic_code="UNKNOWN_INPUT",
                input_names=("nonexistent",),
            ),
            "UNKNOWN_INPUT",
            ["nonexistent"],
        ),
        (
            FleetPlanInputRejected(
                "PRIVATE_SSH_KEY secret",
                diagnostic_code="MISSING_REQUIRED_INPUT",
                input_names=("skill",),
            ),
            "MISSING_REQUIRED_INPUT",
            ["skill"],
        ),
        (FleetPlanInvalid("PRIVATE_SSH_KEY secret /tmp/secret"), "PLAN_RESOLUTION_REJECTED", []),
    ],
)
def test_inspection_rejection_is_exact_t4_safe_projection(failure, code, names):
    service = Mock()
    service.inspect_inputs.side_effect = failure
    response = call(
        service,
        {
            "plan_key": "python-quality",
            "expected_revision": REV,
            "inputs": {"coverage_threshold": "PRIVATE_SSH_KEY"},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "rejected"
    assert body["plan_key"] is None and body["sha256"] is None and body["plan_id"] is None
    assert body["diagnostic"]["schema"] == "oh-goon.plan-validation-diagnostic/v1"
    assert body["diagnostic"]["code"] == code
    assert body["diagnostic"]["inputs"] == names
    action = body["diagnostic"]["safe_next_action"].casefold()
    assert action
    assert all(
        command not in action
        for command in ("run.yml", "config inspect", "config validate", "plan inputs")
    )
    assert "PRIVATE_SSH_KEY" not in response.text and "/tmp/secret" not in response.text
    service.run.assert_not_called()


def test_stale_revision_is_not_downgraded_to_success_or_generic_input_rejection():
    service = Mock()
    service.inspect_inputs.side_effect = FleetPlanSourceStale("stale")
    response = call(service)
    assert response.status_code == 409
    assert response.json()["code"] == "FLEET_PLAN_SOURCE_STALE"


def test_malformed_body_never_escapes_rejected_secret_via_pydantic_context():
    response = call(
        Mock(),
        {
            "plan_key": "python-quality",
            "expected_revision": "PRIVATE_SSH_KEY",
            "inputs": {"foo": ["PRIVATE_SSH_KEY"]},
        },
    )
    assert response.status_code == 422
    assert "PRIVATE_SSH_KEY" not in response.text
    assert response.json()["details"] == {}
