"""Campaign identity remains readable when admission produced no child Runs."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import StateConflict
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_repair import AgentRepairBudget, AgentRepairStopReason
from oh_goon.core.product_identity import repository_id
from oh_goon.services.execution_lock_persistence import write_execution_lock
from tests.factories.agent_review import agent_review_case
from tests.integration.api.client import authenticated_api_client


def test_initial_admission_refusal_reopens_by_campaign_id_in_api_and_fresh_cli(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    resolver, plan_path, repository, options = agent_review_case(tmp_path)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    plan, _, _ = resolver.resolve_frozen_agents(plan_path, **options)
    write_execution_lock(plan_path.parent / "goon.lock", build_execution_lock(plan))
    runtime = ApplicationRuntime.build(standard_local=True)
    admissions = []

    def refuse_initial_admission(target, **selection):
        admissions.append((target, selection))
        raise StateConflict("Selected executor refused initial admission")

    try:
        runtime.locations.add(repository.parent)
        monkeypatch.setattr(runtime.runs, "start_prepared_plan", refuse_initial_admission)
        result = runtime.plans.repair(
            plan_path,
            repository_id=repository_id(repository),
            budget=AgentRepairBudget(max_attempts=2, timeout_seconds=60, step_timeout_seconds=10),
            inputs=options["inputs"],
            agent_repository_root=options["repository_root"],
            agent_skill_root=options["skill_root"],
            allowed_agent_acceptance=options["allowed_acceptance"],
            agent_connection=options["connection"],
            review_write_roots=("code.txt",),
            fleet_run_id="refused-campaign",
        )
        assert result.stop_reason is AgentRepairStopReason.EXECUTION_UNRESOLVED
        assert result.admitted_run_ids == ()
        assert len(admissions) == 1
        assert runtime.runs.campaign_journal(result.repair_id).phases == ()
    finally:
        runtime.shutdown(wait_for_runs=True)

    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        app = create_app(local_access_token="campaign-readback-test-token")
        app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: reopened.runs
        route = f"/api/fleet/runs/campaigns/{result.repair_id}"
        with closing(authenticated_api_client(app)) as client:
            denied = client.get(route, headers={"X-Oh-Goon-Local-Access": "wrong-token"})
            assert denied.status_code == 401
            response = client.get(route)
            assert response.status_code == 200, response.json()
            report = response.json()
            assert report["campaign_id"] == result.repair_id
            assert report["runs"] == []
            assert report["controller_result"]["availability"] == "journal"
            assert report["controller_result"]["stop_reason"] == "execution_unresolved"
            assert report["unstarted_selection"]["selected_run_id"] == result.repair_id
            assert report["unstarted_selection"]["selected_step_ids"] == [
                step.step_id for step in admissions[0][0].plan.steps
            ]
            assert client.get("/api/fleet/runs/campaigns/missing-campaign").status_code == 404
        cli = subprocess.run(
            [sys.executable, "-m", "oh_goon", "runs", "campaign", result.repair_id, "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert json.loads(cli.stdout) == report
    finally:
        reopened.shutdown(wait_for_runs=True)
