import subprocess
from pathlib import Path

from oh_goon.api.app import app as api_app
from tests.integration.api.client import authenticated_api_client


def test_browser_exclusion_is_shared_local_policy(tmp_path: Path) -> None:
    database = tmp_path / "oh-goon.db"
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repository)], check=True)

    client = authenticated_api_client(api_app)
    assert client.post("/api/locations", json={"path": str(root)}).status_code == 200
    initial = client.get("/api/repositories").json()
    assert initial[0]["scope"] == "INCLUDED"
    initial_fleet = client.get("/api/fleet").json()
    repository_instance_id = initial_fleet["repositories"][0]["repository_instance_id"]

    response = client.post(
        "/api/repositories/exclude",
        json={
            "repository": str(repository),
            "reason": "Legacy service",
            "expected_repository_instance_id": repository_instance_id,
        },
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "EXCLUDED"

    refreshed = client.get("/api/fleet").json()
    assert refreshed["summary"]["excluded"] == 1
    assert refreshed["repositories"][0]["init"] is None
    assert database.exists()

    excluded_fleet = client.get("/api/fleet").json()
    excluded_instance_id = excluded_fleet["repositories"][0]["repository_instance_id"]
    response = client.post(
        "/api/repositories/include",
        json={
            "repository": str(repository),
            "expected_repository_instance_id": excluded_instance_id,
        },
    )
    assert response.status_code == 200
    assert response.json()["scope"] == "INCLUDED"
