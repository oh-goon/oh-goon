from __future__ import annotations

from pathlib import Path

from oh_goon.api.app import app as api_app
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.integration.api.client import authenticated_api_client

TERMINAL = {"COMPLETED", "CANCELLED", "FAILED"}


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


def test_missing_declared_executable_does_not_bypass_unprovisioned_fleet_admission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    repository = root / "missing-tool"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='missing-tool'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    executable = "definitely-not-an-oh-goon-executable-9d61"
    (repository / "goon.yml").write_text(
        f"""version: 1
execution:
  purpose: verification
steps:
- id: missing-tool-step
  run: "{executable} --version"
""",
        encoding="utf-8",
    )

    _write_lock(repository)

    with authenticated_api_client(api_app) as client:
        assert client.post("/api/locations", json={"path": str(root)}).status_code == 200
        pipeline_sha256, repository_instance_id = _team_authority(client, repository)
        accepted = client.post(
            "/api/league/team-runs",
            json={
                "repository": str(repository),
                "fleet_run_id": "missing-executable-api",
                "expected_pipeline_sha256": pipeline_sha256,
                "expected_repository_instance_id": repository_instance_id,
                "inputs": {},
            },
        )
        # The authenticated Fleet Plan issuer is not provisioned in this API
        # fixture. Invalid or missing executables must not create a Run by
        # falling back to an untrusted step-execution path.
        assert accepted.status_code == 409
        body = accepted.json()
        assert body["code"] == "STATE_CONFLICT"
        assert (
            body["message"]
            == "Fleet Plan execution requires valid authenticated admission authority"
        )
        assert client.get("/api/fleet/runs/missing-executable-api").status_code == 404


def test_non_executable_command_does_not_bypass_unprovisioned_fleet_admission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    repository = root / "permission-tool"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='permission-tool'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    executable = repository / "tool.sh"
    executable.write_text("#!/bin/sh\necho should-not-run\n", encoding="utf-8")
    executable.chmod(0o644)
    (repository / "goon.yml").write_text(
        """version: 1
execution:
  purpose: verification
steps:
- id: permission-step
  run: ./tool.sh
""",
        encoding="utf-8",
    )

    _write_lock(repository)

    with authenticated_api_client(api_app) as client:
        assert client.post("/api/locations", json={"path": str(root)}).status_code == 200
        pipeline_sha256, repository_instance_id = _team_authority(client, repository)
        accepted = client.post(
            "/api/league/team-runs",
            json={
                "repository": str(repository),
                "fleet_run_id": "permission-executable-api",
                "expected_pipeline_sha256": pipeline_sha256,
                "expected_repository_instance_id": repository_instance_id,
                "inputs": {},
            },
        )
        # The authenticated Fleet Plan issuer is not provisioned in this API
        # fixture. Invalid or missing executables must not create a Run by
        # falling back to an untrusted step-execution path.
        assert accepted.status_code == 409
        body = accepted.json()
        assert body["code"] == "STATE_CONFLICT"
        assert (
            body["message"]
            == "Fleet Plan execution requires valid authenticated admission authority"
        )
        assert client.get("/api/fleet/runs/permission-executable-api").status_code == 404


def test_invalid_executable_format_does_not_bypass_unprovisioned_fleet_admission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    repository = root / "invalid-format-tool"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='invalid-format-tool'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    executable = repository / "tool"
    executable.write_text("echo no-shebang\n", encoding="utf-8")
    executable.chmod(0o755)
    (repository / "goon.yml").write_text(
        """version: 1
execution:
  purpose: verification
steps:
- id: invalid-format-step
  run: ./tool
""",
        encoding="utf-8",
    )

    _write_lock(repository)

    with authenticated_api_client(api_app) as client:
        assert client.post("/api/locations", json={"path": str(root)}).status_code == 200
        pipeline_sha256, repository_instance_id = _team_authority(client, repository)
        accepted = client.post(
            "/api/league/team-runs",
            json={
                "repository": str(repository),
                "fleet_run_id": "invalid-format-executable-api",
                "expected_pipeline_sha256": pipeline_sha256,
                "expected_repository_instance_id": repository_instance_id,
                "inputs": {},
            },
        )
        # The authenticated Fleet Plan issuer is not provisioned in this API
        # fixture. Invalid or missing executables must not create a Run by
        # falling back to an untrusted step-execution path.
        assert accepted.status_code == 409
        body = accepted.json()
        assert body["code"] == "STATE_CONFLICT"
        assert (
            body["message"]
            == "Fleet Plan execution requires valid authenticated admission authority"
        )
        assert client.get("/api/fleet/runs/invalid-format-executable-api").status_code == 404


def test_missing_interpreter_does_not_bypass_unprovisioned_fleet_admission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    repository = root / "missing-interpreter-tool"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='missing-interpreter-tool'\nversion='0.0.0'\n",
        encoding="utf-8",
    )
    executable = repository / "tool.sh"
    executable.write_text("#!/definitely/missing/interpreter\necho hi\n", encoding="utf-8")
    executable.chmod(0o755)
    (repository / "goon.yml").write_text(
        """version: 1
execution:
  purpose: verification
steps:
- id: missing-interpreter-step
  run: ./tool.sh
""",
        encoding="utf-8",
    )

    _write_lock(repository)

    with authenticated_api_client(api_app) as client:
        assert client.post("/api/locations", json={"path": str(root)}).status_code == 200
        pipeline_sha256, repository_instance_id = _team_authority(client, repository)
        accepted = client.post(
            "/api/league/team-runs",
            json={
                "repository": str(repository),
                "fleet_run_id": "missing-interpreter-api",
                "expected_pipeline_sha256": pipeline_sha256,
                "expected_repository_instance_id": repository_instance_id,
                "inputs": {},
            },
        )
        # The authenticated Fleet Plan issuer is not provisioned in this API
        # fixture. Invalid or missing executables must not create a Run by
        # falling back to an untrusted step-execution path.
        assert accepted.status_code == 409
        body = accepted.json()
        assert body["code"] == "STATE_CONFLICT"
        assert (
            body["message"]
            == "Fleet Plan execution requires valid authenticated admission authority"
        )
        assert client.get("/api/fleet/runs/missing-interpreter-api").status_code == 404
