from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon.api.app import app as api_app
from oh_goon.api.dependencies import fleet_run_control_plane, fleet_service
from oh_goon.api.routes.fleet_runs import router as fleet_runs_router
from oh_goon.api.routes.fleet_snapshot import router as fleet_snapshot_router
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentModel,
    AgentProvider,
    NativeAgentDefinition,
    NativeAgentProfile,
    PiSessionIntent,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CancelledFleetRun,
    FleetRunDetail,
    FleetRunProgress,
    RunningFleetRun,
)
from oh_goon.core.models.run_admission import DirectFleetExecutionAdmission, fleet_repository_scope
from oh_goon.core.models.target import AgentStepDeliveryReference, FleetPlanTarget
from oh_goon.core.projections import build_fleet_snapshot
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import FleetRunStatus, OperationAction
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.run_admission import repository_admissions_for
from tests.integration.api.client import authenticated_api_client


def test_fleet_snapshot_does_not_infer_readiness_during_discovery(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")

    client = authenticated_api_client(api_app)
    assert client.post("/api/locations", json={"path": str(root)}).status_code == 200

    snapshot = client.get("/api/fleet").json()

    assert snapshot["summary"]["included"] == 1
    assert snapshot["summary"]["not_checked"] == 1
    assert snapshot["summary"]["ready_for_update"] == 0
    assert snapshot["repositories"][0]["init"] is None
    assert snapshot["repositories"][0]["guidance"]["reason_code"] == "NOT_CHECKED"


_PRIVATE_PI_TASK = "synthetic private Pi task excerpt"


@pytest.fixture
def private_pi_runs(
    tmp_path: Path,
) -> tuple[RunningFleetRun, CancelledFleetRun]:
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="fixture",
        definition=NativeAgentDefinition(executable="/fixture/pi"),
        profile=NativeAgentProfile(
            backend="bubblewrap", allowed_agents=("pi",), inference_profile="fixture", pi_rpc=True
        ),
        executable_sha256="a" * 64,
        connection=AgentConnection(
            profile="fixture",
            provider_name="fixture",
            provider=AgentProvider(api="openai-chat-completions", base_url="http://127.0.0.1:9/v1"),
            selection=AgentModel(provider="fixture", model="fixture"),
        ),
        backend_configuration_id="fixture-bubblewrap",
        session=PiSessionIntent(mode="new"),
    )
    step = ResolvedAgentStep(
        step_id="agent",
        timeout_seconds=5,
        workload=ResolvedNativeAgentWorkload(role="report", native_agent=selected),
        delivery_sha256="b" * 64,
        handoff_identity="frozen-fixture",
        source_step_id="agent",
        task_summary=_PRIVATE_PI_TASK,
        task_sha256="c" * 64,
    )
    plan = ResolvedFleetPlan.create(
        name="pi-session-snapshot",
        description="Admitted Pi snapshot fixture",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
        native_agent=selected,
    )
    target = FleetPlanTarget(
        kind="fleet_plan",
        plan=plan,
        source=PlanFileSource(sha256="d" * 64),
        identity=structural_execution_identity(plan),
        agent_deliveries=(
            AgentStepDeliveryReference(
                step_id=step.step_id,
                delivery_sha256=step.delivery_sha256,
                handoff_identity=step.handoff_identity,
                reference_id="retained-fixture",
            ),
        ),
    )
    repository = RepositoryIdentity.model_validate({"name": "api", "path": tmp_path})
    now = utc_now()
    live = RunningFleetRun(
        status=FleetRunStatus.RUNNING,
        fleet_run_id="pi-live",
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        target=target,
        repository_scope=fleet_repository_scope({repository.repository_id}),
        targets=(repository,),
        accepted_at=now,
        started_at=now,
        progress=FleetRunProgress(total=1, succeeded=0, failed=0),
    )
    stopped = CancelledFleetRun.model_validate(
        {
            **live.model_dump(exclude_computed_fields=True),
            "status": FleetRunStatus.CANCELLED,
            "fleet_run_id": "pi-stopped",
            "cancel_requested_at": now,
            "finished_at": now,
        }
    )
    return live, stopped


def test_fleet_snapshot_uses_private_pi_readback_for_live_and_latest_runs(
    private_pi_runs: tuple[RunningFleetRun, CancelledFleetRun],
) -> None:
    live, stopped = private_pi_runs
    target = live.target
    assert isinstance(target, FleetPlanTarget)
    live_detail, stopped_detail = (
        FleetRunDetail(
            run=run,
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=repository_admissions_for(target, run.targets),
        )
        for run in (live, stopped)
    )
    observed = build_fleet_snapshot(
        [],
        durable_revision=1,
        journal_revision=1,
        active_fleet_runs=(live_detail,),
        latest_fleet_runs=[stopped_detail],
    )
    assert _PRIVATE_PI_TASK in observed.model_dump_json()
    api = FastAPI()
    api.include_router(fleet_snapshot_router, prefix="/api")
    api.dependency_overrides[fleet_service] = lambda: SimpleNamespace(snapshot=lambda: observed)

    with TestClient(api) as client:
        response = client.get("/api/fleet")

    response.raise_for_status()
    assert _PRIVATE_PI_TASK not in response.text
    public = response.json()
    active = public["active_fleet_runs"][0]
    latest = public["latest_fleet_runs"][0]
    assert public["active_fleet_run"] == active
    assert [item["run"]["fleet_run_id"] for item in (active, latest)] == ["pi-live", "pi-stopped"]
    assert all(item["game_tape"]["pi_session"]["mode"] == "new" for item in (active, latest))


def test_fleet_cancel_acknowledges_private_pi_without_publishing_task_evidence(
    private_pi_runs: tuple[RunningFleetRun, CancelledFleetRun],
) -> None:
    live, stopped = private_pi_runs
    stopped = stopped.model_copy(update={"fleet_run_id": live.fleet_run_id})
    original = stopped.model_dump(mode="json")
    assert original["target"]["plan"]["steps"][0]["task_summary"] == _PRIVATE_PI_TASK

    def cancel(fleet_run_id: str) -> CancelledFleetRun:
        assert fleet_run_id == live.fleet_run_id
        return stopped

    api = FastAPI()
    api.include_router(fleet_runs_router, prefix="/api")
    api.dependency_overrides[fleet_run_control_plane] = lambda: SimpleNamespace(cancel=cancel)
    with TestClient(api) as client:
        response = client.post(f"/api/fleet/runs/{live.fleet_run_id}/cancel")

    assert response.status_code == 202
    assert _PRIVATE_PI_TASK not in response.text
    public = response.json()
    assert public["fleet_run_id"] == live.fleet_run_id
    assert public["status"] == FleetRunStatus.CANCELLED
    (step,) = public["target"]["plan"]["steps"]
    assert step["step_id"] == "agent"
    assert {"task_summary", "task_sha256", "review_context", "feedback_value"}.isdisjoint(step)
    assert stopped.model_dump(mode="json") == original


def test_dependency_discovery_does_not_scan_repository_files_without_observed_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        '[project]\nname="api"\nversion="0.1.0"\ndependencies=["httpx>=0.27"]\n',
        encoding="utf-8",
    )
    (repository / "uv.lock").write_text(
        'version = 1\n[[package]]\nname = "httpx"\nversion = "0.27.2"\n',
        encoding="utf-8",
    )

    client = authenticated_api_client(api_app)
    assert client.post("/api/locations", json={"path": str(root)}).status_code == 200

    search = client.get("/api/dependencies/search", params={"q": "httpx"})
    assert search.status_code == 200
    assert search.json()["items"] == []

    coverage = client.get("/api/dependencies/coverage")
    assert coverage.status_code == 200
    assert coverage.json() == {
        "ecosystem": "pypi",
        "repository_total": 1,
        "current_dependency_evidence": 0,
        "unknown_dependency_evidence": 0,
        "stale_dependency_evidence": 0,
        "missing_dependency_evidence": 1,
        "no_current_dependency_evidence": 1,
    }


def test_uv_control_plane_read_endpoints_are_backend_projections(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        '[project]\nname="api"\nversion="0.1.0"\n',
        encoding="utf-8",
    )

    client = authenticated_api_client(api_app)
    assert client.post("/api/locations", json={"path": str(root)}).status_code == 200

    capabilities = client.get("/api/capabilities")
    assert capabilities.status_code == 200
    capability_payload = capabilities.json()
    capability_ids = {item["capability_id"] for item in capability_payload}
    assert {"initialize", "recipe:audit", "adopt-uv", "fleet-plan-execute"} <= capability_ids
    assert "dependency-update" not in capability_ids
    assert "git-ready" not in capability_ids

    commands = client.get("/api/fleet/recipes")
    assert commands.status_code == 200
    command_payload = commands.json()
    assert command_payload["recipes"]
    assert {row["spec"]["recipe"] for row in command_payload["recipes"]} >= {
        "LOCK_CHECK",
        "AUDIT",
        "SBOM",
    }

    security = client.get("/api/fleet/security")
    assert security.status_code == 200
    assert security.json()["repositories"] == 1
    assert security.json()["audited"] == 0

    migration = client.get("/api/fleet/adopt-uv")
    assert migration.status_code == 200
    assert migration.json()["discovered"] == 1
    assert migration.json()["safe_automatic"] == 1
