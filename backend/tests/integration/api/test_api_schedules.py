import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oh_goon.api.app import app as api_app
from oh_goon.api.app import create_app
from oh_goon.api.schemas.schedule import (
    RetainedReviewScheduleCreateRequest,
    WritableScheduleCreateRequest,
)
from oh_goon.core.models.schedule import ScheduleDefinition, ScheduleDeleteRequest
from oh_goon.core.models.writable_schedule import WritableScheduleTask
from tests.factories.schedules import schedule_create
from tests.integration.api.client import authenticated_api_client


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
            item.definition
            for item in current.schedules
            if item.definition.schedule_id == schedule_id
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


def test_retained_review_schedule_requires_local_access_and_exact_run_selection() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from oh_goon.api.dependencies import fleet_plan_service
    from tests.factories.schedules import schedule_definition

    calls = []

    def schedule(
        run_id: str, *, schedule_id: str, name: str | None, interval_minutes: int
    ) -> ScheduleDefinition:
        calls.append((run_id, schedule_id, name, interval_minutes))
        return schedule_definition(
            now=datetime(2026, 8, 21, tzinfo=UTC),
            schedule_id=schedule_id,
            name=name or "Native review",
            repository_ids=("review-source",),
            interval_minutes=interval_minutes,
        )

    app = create_app(local_access_token="schedule-review-test-token")
    app.dependency_overrides[fleet_plan_service] = lambda: SimpleNamespace(
        schedule_retained_review=schedule
    )
    route = "/api/schedules/review/from-review-run"
    request = RetainedReviewScheduleCreateRequest(
        fleet_run_id="fleet-original", name="Nightly review", interval_minutes=60
    ).model_dump(mode="json")
    with closing(TestClient(app)) as anonymous:
        assert anonymous.post(route, json=request).status_code == 401
    with closing(authenticated_api_client(app)) as client:
        assert client.post(route, json={**request, "target": {}}).status_code == 422
        assert client.post(route, json={**request, "interval_minutes": 0}).status_code == 422
        assert client.post(route, json={**request, "fleet_run_id": "x" * 161}).status_code == 422
        assert client.post(route, json={**request, "name": "x" * 81}).status_code == 422
        assert calls == []
        result = client.post(route, json=request)
        assert result.status_code == 201
        assert result.json()["schedule_id"] == "review"
    assert calls == [("fleet-original", "review", "Nightly review", 60)]


def test_writable_schedule_accepts_ordered_tasks_under_local_access() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from oh_goon.api.dependencies import schedule_service
    from tests.factories.schedules import schedule_definition

    authored = WritableScheduleCreateRequest(
        seed_advance_id="published-seed",
        tasks=(
            WritableScheduleTask(task_id="first", prompt="Check the first private task."),
            WritableScheduleTask(task_id="second", prompt="Check the second private task."),
        ),
        interval_minutes=2,
    )
    calls: list[tuple[object, ...]] = []

    def schedule(
        schedule_id: str,
        *,
        seed_advance_id: str,
        tasks: tuple[WritableScheduleTask, ...],
        interval_minutes: int,
        name: str | None,
    ) -> ScheduleDefinition:
        calls.append((schedule_id, seed_advance_id, tasks, interval_minutes, name))
        return schedule_definition(
            now=datetime(2026, 8, 21, tzinfo=UTC),
            schedule_id=schedule_id,
            name="Writable tasks",
            repository_ids=("receiving",),
            interval_minutes=interval_minutes,
        )

    app = create_app(local_access_token="schedule-writable-test-token")
    app.dependency_overrides[schedule_service] = lambda: SimpleNamespace(
        create_writable_rounds=schedule
    )
    route = "/api/schedules/writable/from-branch-advance"
    body = authored.model_dump(mode="json")
    with closing(TestClient(app)) as anonymous:
        assert anonymous.post(route, json=body).status_code == 401
    with closing(authenticated_api_client(app)) as client:
        assert client.post(route, json={**body, "tasks": []}).status_code == 422
        assert client.post(route, json={**body, "tasks": [body["tasks"][0]] * 2}).status_code == 422
        assert client.post(route, json={**body, "native_profile": "override"}).status_code == 422
        assert calls == []
        result = client.post(route, json=body)
        assert result.status_code == 201
        assert result.json()["schedule_id"] == "writable"
        assert all(task.prompt not in result.text for task in authored.tasks)
    assert calls == [("writable", "published-seed", authored.tasks, 2, None)]


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


def test_schedule_refuses_saved_pi_continuation_before_persisting_definition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.api import dependencies
    from oh_goon.api.app import create_app
    from oh_goon.application import ApplicationRuntime
    from oh_goon.core.product_identity import repository_id, repository_subject_instance_id
    from oh_goon.runtime import goon_templates

    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "journal.sqlite"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("OH_GOON_SHARED_PLAN_DIR", str(tmp_path / "shared-plans"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    repository = tmp_path / "teams" / "example"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True, timeout=10)
    team = repository_id(repository)
    instance = repository_subject_instance_id(repository)
    catalog = tmp_path / "catalog"
    for key, content in (
        ("general/scheduled-probe", "steps:\n- run: /usr/bin/true\n"),
        (
            "agents/resume-pi",
            "steps:\n"
            "- uses: agent\n"
            "  harness: native\n"
            "  role: review\n"
            "  task: Inspect source\n"
            "  session: {mode: continue, prior_run_id: prior-pi-run}\n"
            "  requested_effects: {repository_access: read_only, recipients: [configured_model]}\n",
        ),
    ):
        package = catalog / key
        package.mkdir(parents=True)
        (package / "README.md").write_text(f"# {key}\n")
        (package / "goon.yml").write_text(content)
    monkeypatch.setattr(goon_templates, "default_goon_templates_root", lambda: catalog)

    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent)
        plan = runtime.shared_plan.save("schedule-proof", "goons: [general/scheduled-probe]\n")
        app = create_app(local_access_token="pi-schedule-admission-test-token")
        app.dependency_overrides[dependencies.shared_plan_service] = lambda: runtime.shared_plan
        app.dependency_overrides[dependencies.shared_plan_schedule_service] = (
            lambda: runtime.shared_plan_schedules
        )
        app.dependency_overrides[dependencies.schedule_service] = lambda: runtime.schedules
        with closing(authenticated_api_client(app)) as client:
            preview = client.post(
                "/api/league/shared-plan-previews",
                json={
                    "plan_key": plan.plan_key,
                    "expected_revision": plan.sha256,
                    "repository_ids": [team],
                },
            )
            assert preview.status_code == 200, preview.text
            reviewed = preview.json()
            request = {
                "name": "Schedule proof",
                "repository_ids": [team],
                "expected_repository_instance_ids": {team: instance},
                "plan_key": plan.plan_key,
                "expected_plan_revision": plan.sha256,
                "expected_plan_id": reviewed["plan_id"],
                "expected_execution_digest": reviewed["execution_digest"],
                "interval_minutes": 60,
                "state": "paused",
            }
            created = client.post("/api/schedules/plain-plan/from-shared-plan", json=request)
            assert created.status_code == 201, created.text
            assert created.json()["target"]["identity"]["digest"] == reviewed["execution_digest"]
            before = client.get("/api/schedules")
            assert before.status_code == 200

            continuation = runtime.shared_plan.save(
                plan.plan_key, "goons: [agents/resume-pi]\n", expected_revision=plan.sha256
            )
            # Select the exact saved revision for an Agent source that has no schedulable digest.
            refused = client.post(
                "/api/schedules/pi-continuation/from-shared-plan",
                json={**request, "expected_plan_revision": continuation.sha256},
            )
            assert refused.status_code == 400, refused.text
            assert refused.json()["code"] == "FLEET_PLAN_INVALID"
            assert "original Run/Step admission" in refused.json()["message"]
            after = client.get("/api/schedules")
            assert after.status_code == 200 and after.json() == before.json()
            assert not runtime.runs.history().items
    finally:
        runtime.shutdown(wait_for_runs=True)
