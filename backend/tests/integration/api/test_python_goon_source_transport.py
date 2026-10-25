"""Public API consumers execute retained Python package sources after cold restart."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.product_identity import repository_id, schedule_repository_fleet_run_id
from tests.factories import python_goon_projects
from tests.factories.python_goon_projects import SharedPackageScheduleOptions

execution = python_goon_projects.execution
shared_execution = python_goon_projects.shared_execution

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


def test_cold_api_rerun_and_schedule_execute_captured_mixed_plan_without_authoring_files(
    execution: tuple[ApplicationRuntime, Path, Path],
) -> None:
    from oh_goon.api import dependencies
    from oh_goon.api.app import create_app
    from tests.integration.api.client import authenticated_api_client

    runtime, repository, plan = execution
    subject = plan.parent / "goons/general/subject"
    subject.mkdir(parents=True)
    (subject / "README.md").write_text("# general/subject\n\nObserve the selected target.\n")
    (subject / "goon.yml").write_text(
        "steps:\n- run: git rev-parse --show-toplevel\n  outputs:\n    SUBJECT: {stream: stdout, max_bytes: 4096}\n"
    )
    plan.write_text(
        "goons:\n- import: general/subject\n  from: goons\n"
        "- import: general/orders\n  from: goons\n"
        "- import: general/subject\n  from: goons\n"
    )
    runtime.plans.lock(plan)
    initial = runtime.plans.run(plan, repository_ids={repository_id(repository)})
    baseline = runtime.runs.wait_for_terminal(initial.fleet_run_id, timeout=30)
    assert baseline.game_tape.status.value == "PASSED"
    saved = runtime.plans.schedule(
        plan,
        schedule_id="cold-package-schedule",
        repository_ids={repository_id(repository)},
        interval_minutes=60,
    )
    runtime.shutdown(wait_for_runs=True)
    shutil.rmtree(plan.parent)

    cold = ApplicationRuntime.build(standard_local=True)
    try:
        app = create_app(local_access_token="cold-package-api-test-token")
        app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: cold.runs
        app.dependency_overrides[dependencies.schedule_service] = lambda: cold.schedules
        with closing(authenticated_api_client(app)) as client:
            rerun = client.post(
                f"/api/fleet/runs/{initial.fleet_run_id}/rerun",
                json={"new_fleet_run_id": "cold-package-api-rerun"},
            )
            assert rerun.status_code == 202, rerun.json()
            repeated = cold.runs.wait_for_terminal("cold-package-api-rerun", timeout=30)
            assert repeated.game_tape.status.value == "PASSED"
            catalog = client.get("/api/schedules")
            assert catalog.status_code == 200
            schedule = next(
                item["definition"]
                for item in catalog.json()["schedules"]
                if item["definition"]["schedule_id"] == saved.schedule_id
            )
            replacement = {
                key: schedule[key]
                for key in (
                    "name",
                    "repository_ids",
                    "repository_instance_ids",
                    "target",
                    "state",
                    "generation_id",
                    "record_version",
                )
            }
            metadata = client.put(
                f"/api/schedules/{saved.schedule_id}",
                json={
                    **replacement,
                    "interval_minutes": 120,
                },
            )
            assert metadata.status_code == 200, metadata.json()
            current = metadata.json()
            occurrence = client.post(
                f"/api/schedules/{saved.schedule_id}/runs",
                json={
                    "schedule_run_id": "cold-package-api-occurrence",
                    "expected_generation_id": current["generation_id"],
                    "expected_record_version": current["record_version"],
                },
            )
            assert occurrence.status_code == 202, occurrence.json()
            child = schedule_repository_fleet_run_id(
                "cold-package-api-occurrence", repository_id(repository)
            )
            scheduled = cold.runs.wait_for_terminal(child, timeout=30)
            assert scheduled.game_tape.status.value == "PASSED"

        expected_subject = hashlib.sha256(str(repository).encode()).hexdigest()
        expected_result = hashlib.sha256(
            (json.dumps({"total_order_value": "1236.70"}) + "\n").encode()
        ).hexdigest()
        for detail in (baseline, repeated, scheduled):
            evidence = detail.operations[0].evidence
            assert isinstance(evidence, FleetPlanOperationEvidence)
            assert evidence.plan.steps[0].outputs[0].sha256 == expected_subject
            assert evidence.plan.steps[-1].outputs[0].sha256 == expected_subject
            result = evidence.plan.steps[-2].artifacts[0]
            assert result.status == "PRESENT" and result.sha256 == expected_result
        assert not (repository / ".venv").exists()
        assert not (repository / ".showcase").exists()
    finally:
        cold.shutdown(wait_for_runs=True)


def test_shared_plan_schedule_api_retains_package_across_restart_and_catalog_removal(
    shared_execution: tuple[ApplicationRuntime, Path, Path, SharedPackageScheduleOptions],
) -> None:
    from oh_goon.api import dependencies
    from oh_goon.api.app import create_app
    from tests.integration.api.client import authenticated_api_client

    runtime, repository, plan, options = shared_execution
    app = create_app(local_access_token="shared-package-schedule-api-test-token")
    app.dependency_overrides[dependencies.shared_plan_schedule_service] = (
        lambda: runtime.shared_plan_schedules
    )
    with closing(authenticated_api_client(app)) as client:
        created = client.post("/api/schedules/shared-orders/from-shared-plan", json=options)
        assert created.status_code == 201, created.json()
        saved = created.json()
        assert saved["target"]["project_deliveries"]
        replay = client.post("/api/schedules/shared-orders/from-shared-plan", json=options)
        assert replay.status_code == 201, replay.json()
        assert replay.json() == saved

    stored = runtime.schedules.definition("shared-orders")
    assert stored.target.plan._project_archives == {}
    runtime.shutdown(wait_for_runs=True)
    shutil.rmtree(plan.parent)
    shutil.rmtree(runtime.shared_plan.root)

    cold = ApplicationRuntime.build(standard_local=True)
    try:
        app = create_app(local_access_token="shared-package-occurrence-api-test-token")
        app.dependency_overrides[dependencies.schedule_service] = lambda: cold.schedules
        with closing(authenticated_api_client(app)) as client:
            started = client.post(
                "/api/schedules/shared-orders/runs",
                json={
                    "schedule_run_id": "cold-shared-orders",
                    "expected_generation_id": saved["generation_id"],
                    "expected_record_version": saved["record_version"],
                },
            )
            assert started.status_code == 202, started.json()
        child = schedule_repository_fleet_run_id("cold-shared-orders", repository_id(repository))
        completed = cold.runs.wait_for_terminal(child, timeout=30)
        assert completed.game_tape.status.value == "PASSED"
        evidence = completed.operations[0].evidence
        assert isinstance(evidence, FleetPlanOperationEvidence)
        artifact = evidence.plan.steps[-1].artifacts[0]
        expected = (json.dumps({"total_order_value": "1236.70"}) + "\n").encode()
        assert artifact.status == "PRESENT"
        assert artifact.sha256 == hashlib.sha256(expected).hexdigest()
        assert not (repository / ".venv").exists()
        assert not (repository / ".showcase").exists()
    finally:
        cold.shutdown(wait_for_runs=True)
