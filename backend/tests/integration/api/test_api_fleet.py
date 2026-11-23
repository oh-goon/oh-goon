from pathlib import Path

import pytest

from oh_goon.api.app import app as api_app
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
from oh_goon.services.fleet import FleetService
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


def test_fleet_snapshot_uses_private_pi_readback_for_live_and_latest_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "synthetic private Pi task excerpt"
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
        task_summary=canary,
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
    live_detail, stopped_detail = (
        FleetRunDetail(
            run=run,
            execution=DirectFleetExecutionAdmission(),
            repository_admissions=repository_admissions_for(target, (repository,)),
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
    assert canary in observed.model_dump_json()
    monkeypatch.setattr(FleetService, "snapshot", lambda _service: observed)

    response = authenticated_api_client(api_app).get("/api/fleet")

    response.raise_for_status()
    assert canary not in response.text
    public = response.json()
    active = public["active_fleet_runs"][0]
    latest = public["latest_fleet_runs"][0]
    assert public["active_fleet_run"] == active
    assert [item["run"]["fleet_run_id"] for item in (active, latest)] == ["pi-live", "pi-stopped"]
    assert all(item["game_tape"]["pi_session"]["mode"] == "new" for item in (active, latest))


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
