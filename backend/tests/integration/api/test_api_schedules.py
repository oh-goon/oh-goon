from contextlib import closing

from fastapi.testclient import TestClient

from tests.integration.api.client import authenticated_api_client

from oh_goon.api.app import app as api_app
from oh_goon.core.models.schedule import ScheduleDeleteRequest
from tests.factories.schedules import schedule_create


def test_schedule_api_exposes_saved_plan_creation_and_metadata_only_replace() -> None:
    from uuid import uuid4

    from oh_goon.api import dependencies

    schedule_id = f"api-schedule-{uuid4().hex}"
    create_request = schedule_create(
        name="Nightly security",
        repository_ids=("catalog-api",),
        interval_minutes=180,
    )
    service = dependencies.schedule_service()
    service.create_prepared(schedule_id, create_request)

    try:
        with closing(authenticated_api_client(api_app)) as client:
            direct_create = client.post(
                f"/api/schedules/{schedule_id}-direct",
                json=create_request.model_dump(mode="json"),
            )
            assert direct_create.status_code == 405

            catalog = client.get("/api/schedules")
            assert catalog.status_code == 200
            definitions = [item["definition"] for item in catalog.json()["schedules"]]
            schedule = next(item for item in definitions if item["schedule_id"] == schedule_id)
            assert schedule["repository_ids"] == ["catalog-api"]
            assert schedule["target"]["plan"]["name"] == "Nightly security"
            assert "steps" not in schedule
            assert "workflow_id" not in schedule

            replacement = {
                **create_request.model_dump(mode="json"),
                "name": "Early security",
                "generation_id": schedule["generation_id"],
                "record_version": schedule["record_version"],
            }
            changed_repositories = {
                **replacement,
                "repository_ids": ["other-repository"],
                "repository_instance_ids": {"other-repository": "replacement-instance"},
            }
            rejected_repositories = client.put(
                f"/api/schedules/{schedule_id}", json=changed_repositories
            )
            assert rejected_repositories.status_code == 400

            alternate_target = schedule_create(
                name="Another Plan",
                repository_ids=("catalog-api",),
                interval_minutes=180,
            ).target.model_dump(mode="json")
            changed_target = {**replacement, "target": alternate_target}
            rejected_target = client.put(f"/api/schedules/{schedule_id}", json=changed_target)
            assert rejected_target.status_code == 400

            updated = client.put(f"/api/schedules/{schedule_id}", json=replacement)
            assert updated.status_code == 200
            assert updated.json()["name"] == "Early security"
            assert updated.json()["repository_ids"] == ["catalog-api"]
            assert updated.json()["target"] == schedule["target"]

            updated_schedule = updated.json()
            deleted = client.request(
                "DELETE",
                f"/api/schedules/{schedule_id}",
                json={
                    "generation_id": updated_schedule["generation_id"],
                    "record_version": updated_schedule["record_version"],
                },
            )
            assert deleted.status_code == 204
    finally:
        current = service.catalog()
        matching = [
            item.definition for item in current.schedules if item.definition.schedule_id == schedule_id
        ]
        if matching:
            definition = matching[0]
            service.delete(
                schedule_id,
                ScheduleDeleteRequest(
                    generation_id=definition.generation_id,
                    record_version=definition.record_version,
                ),
            )


def test_schedule_can_be_created_from_shared_plan_without_browser_resolution() -> None:
    from datetime import UTC, datetime

    from fastapi import FastAPI

    from oh_goon.api import dependencies
    from oh_goon.api.routes import schedules as schedule_routes
    from tests.factories.schedules import schedule_definition

    class _SharedPlanScheduleService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, schedule_id: str, **kwargs):
            self.calls.append({"schedule_id": schedule_id, **kwargs})
            return schedule_definition(
                now=datetime(2026, 8, 21, tzinfo=UTC),
                schedule_id=schedule_id,
                name=str(kwargs["name"]),
                repository_ids=tuple(kwargs["repository_ids"]),
                interval_minutes=int(kwargs["interval_minutes"]),
                state=kwargs["state"],
            )

    service = _SharedPlanScheduleService()
    app = FastAPI()
    app.include_router(schedule_routes.router, prefix="/api")
    app.dependency_overrides[dependencies.shared_plan_schedule_service] = lambda: service

    with closing(TestClient(app)) as client:
        response = client.post(
            "/api/schedules/nightly/from-shared-plan",
            json={
                "name": "Nightly",
                "repository_ids": ["catalog-api"],
                "expected_repository_instance_ids": {"catalog-api": "instance-catalog-api"},
                "plan_key": "security/standard",
                "expected_plan_revision": "a" * 64,
                "expected_plan_id": "reviewed-plan-id",
                "expected_execution_digest": "sha256:" + "d" * 64,
                "interval_minutes": 120,
                "state": "active",
                "inputs": {"severity": "high"},
            },
        )

    assert response.status_code == 201
    assert response.json()["schedule_id"] == "nightly"
    assert service.calls == [
        {
            "schedule_id": "nightly",
            "name": "Nightly",
            "repository_ids": ("catalog-api",),
            "expected_repository_instance_ids": {"catalog-api": "instance-catalog-api"},
            "plan_key": "security/standard",
            "expected_plan_revision": "a" * 64,
            "expected_plan_id": "reviewed-plan-id",
            "expected_execution_digest": "sha256:" + "d" * 64,
            "interval_minutes": 120,
            "state": "active",
            "inputs": {"severity": "high"},
        }
    ]
