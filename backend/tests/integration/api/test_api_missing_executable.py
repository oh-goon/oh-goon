"""Local executable failures stay native; selected strict admission stays explicit."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.application import ApplicationRuntime
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan import (
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
    PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
)
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.integration.api.client import authenticated_api_client


def _write_lock(repository: Path) -> None:
    path = repository / "goon.yml"
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(path)
    plan = resolver.resolve_source_graph(graph)
    write_execution_lock(repository / "goon.lock", build_execution_lock(plan))


def _team_authority(client, repository: Path) -> tuple[str, str]:
    response = client.get("/api/league")
    assert response.status_code == 200
    teams = response.json()["teams"]
    team = next(item for item in teams if Path(item["repository"]["path"]) == repository)
    assert team["pipeline"]["state"] == "CONFIGURED"
    return team["pipeline"]["sha256"], team["repository_instance_id"]


@pytest.mark.parametrize(
    "kind,standard_local",
    [
        ("missing", True),
        ("permission", True),
        ("invalid-format", True),
        ("missing-interpreter", True),
        ("missing", False),
    ],
)
def test_executable_failure_respects_selected_execution_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    standard_local: bool,
) -> None:
    factory = ApplicationRuntime.build
    monkeypatch.setattr(ApplicationRuntime, "build", lambda: factory(standard_local=standard_local))
    root = tmp_path / "repos"
    repository = root / "project"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "pyproject.toml").write_text("[project]\nname='example'\nversion='0.0.0'\n")
    command = "definitely-not-an-oh-goon-executable-9d61 --version"
    if kind != "missing":
        executable = repository / "tool.sh"
        content, mode = {
            "permission": ("#!/bin/sh\necho should-not-run\n", 0o644),
            "invalid-format": ("echo no-shebang\n", 0o755),
            "missing-interpreter": ("#!/definitely/missing/interpreter\necho hi\n", 0o755),
        }[kind]
        executable.write_text(content)
        executable.chmod(mode)
        command = "./tool.sh"
    (repository / "goon.yml").write_text(
        f'version: 1\nsteps:\n- id: executable\n  run: "{command}"\n'
    )
    _write_lock(repository)
    run_id = "executable-api"
    with authenticated_api_client(api_app) as client:
        assert client.post("/api/locations", json={"path": str(root)}).status_code == 200
        pipeline_sha256, repository_instance_id = _team_authority(client, repository)
        accepted = client.post(
            "/api/league/team-runs",
            json={
                "repository": str(repository),
                "fleet_run_id": run_id,
                "expected_pipeline_sha256": pipeline_sha256,
                "expected_repository_instance_id": repository_instance_id,
                "inputs": {},
            },
        )
        if not standard_local:
            assert accepted.status_code == 409
            body = accepted.json()
            assert body["code"] == "STATE_CONFLICT"
            assert (
                body["message"]
                == "Fleet Plan execution requires valid authenticated admission authority"
            )
            assert client.get(f"/api/fleet/runs/{run_id}").status_code == 404
            return
        assert accepted.status_code == 202
        detail = dependencies.application().runs.wait_for_terminal(run_id, timeout=10)
        assert detail.run.status.terminal
        assert detail.outcome.value == "ATTENTION_REQUIRED"
        assert len(detail.operations) == 1
        step = detail.operations[0].evidence.plan.steps[0]
        assert step.status == "FAILED"
        expected = (
            PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE
            if kind == "missing"
            else PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE
        )
        assert step.returncode == expected
        response = client.get(f"/api/fleet/runs/{run_id}")
        assert response.status_code == 200
        assert response.json()["outcome"] == "ATTENTION_REQUIRED"
        assert (
            response.json()["operations"][0]["evidence"]["plan"]["steps"][0]["returncode"]
            == expected
        )
