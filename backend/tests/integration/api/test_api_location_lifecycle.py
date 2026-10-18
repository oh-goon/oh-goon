import shutil
from pathlib import Path

from oh_goon.api.app import app as api_app
from tests.integration.api.client import authenticated_api_client


def _repository(root: Path, name: str = "repo") -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        f"[project]\nname='{name}'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    return repository


def test_configured_location_reports_unavailable_and_recovers_without_history_rewrite(
    tmp_path: Path,
) -> None:
    root = tmp_path / "league"
    repository = _repository(root)
    client = authenticated_api_client(api_app)

    added = client.post("/api/locations", json={"path": str(root)})
    assert added.status_code == 200
    added_body = added.json()
    assert added_body["locations"] == [str(root.resolve())]
    assert added_body["unavailable"] == []
    assert len(added_body["revision"]) == 64
    catalog = client.get("/api/repositories").json()
    assert [entry["repository"]["path"] for entry in catalog] == [str(repository.resolve())]

    shutil.rmtree(root)
    missing = client.get("/api/locations")
    assert missing.status_code == 200
    missing_body = missing.json()
    assert missing_body["locations"] == [str(root.resolve())]
    assert missing_body["unavailable"] == [str(root.resolve())]
    assert missing_body["revision"] == added_body["revision"]
    assert client.get("/api/repositories").json() == []

    restored_repository = _repository(root)
    restored = client.get("/api/locations")
    restored_body = restored.json()
    assert restored_body["locations"] == [str(root.resolve())]
    assert restored_body["unavailable"] == []
    assert restored_body["revision"] == added_body["revision"]
    catalog = client.get("/api/repositories").json()
    assert [entry["repository"]["path"] for entry in catalog] == [
        str(restored_repository.resolve())
    ]


def test_remove_location_rejects_stale_recreated_path_identity(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    client = authenticated_api_client(api_app)

    first_snapshot = client.post("/api/locations", json={"path": str(first)}).json()
    stale_revision = first_snapshot["revision"]
    client.post("/api/locations", json={"path": str(second)}).raise_for_status()

    stale = client.request(
        "DELETE",
        "/api/locations",
        json={"path": str(first), "expected_revision": stale_revision},
    )
    assert stale.status_code == 409
    current = client.get("/api/locations").json()
    assert str(first.resolve()) in current["locations"]
    assert str(second.resolve()) in current["locations"]
