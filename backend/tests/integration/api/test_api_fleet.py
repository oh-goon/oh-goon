from pathlib import Path

from tests.integration.api.client import authenticated_api_client

from oh_goon.api.app import app as api_app


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
        "repository_total": 1,
        "current_dependency_evidence": 0,
        "aged_dependency_evidence": 0,
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
