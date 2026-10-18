from pathlib import Path

from oh_goon.repository_tooling.architecture import observability_projection_contract as contract


def test_observability_projection_contract() -> None:
    assert contract.check_observability_projection_contract() == []


def _semantic_fixture(root: Path, *, handler_name: str = "anything_private") -> Path:
    package = root / "oh_goon"
    (package / "core/models").mkdir(parents=True)
    (package / "api/routes").mkdir(parents=True)
    (package / "scout").mkdir(parents=True)
    (package / "core/execution_observability.py").write_text("", encoding="utf-8")
    (package / "core/models/execution_observability.py").write_text("", encoding="utf-8")
    (package / "scout/evidence.py").write_text(
        "from dataclasses import dataclass\n"
        "from oh_goon.core.execution_observability import RepositoryRunObservation\n"
        "@dataclass\n"
        "class ScoutEvidenceSnapshot:\n"
        "    runs: tuple[RepositoryRunObservation, ...]\n",
        encoding="utf-8",
    )
    (package / "api/routes/fleet_runs.py").write_text(
        "@router.get('/observations', response_model=FleetExecutionTimeline, "
        "operation_id='fleet_run_execution_observations')\n"
        f"def {handler_name}(control_plane) -> FleetExecutionTimeline:\n"
        "    return control_plane.execution_timeline()\n",
        encoding="utf-8",
    )
    return package


def test_private_observability_handler_rename_does_not_change_architecture(
    monkeypatch, tmp_path: Path
) -> None:
    package = _semantic_fixture(tmp_path, handler_name="execution_timeline_route")
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    assert contract.check_observability_projection_contract() == []


def test_public_observability_route_mutation_fails_architecture(monkeypatch, tmp_path: Path) -> None:
    package = _semantic_fixture(tmp_path)
    route = package / "api/routes/fleet_runs.py"
    route.write_text(route.read_text(encoding="utf-8").replace("router.get", "router.post"), encoding="utf-8")
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    violations = contract.check_observability_projection_contract()
    assert any("GET /observations" in item.message for item in violations)


def test_arbitrarily_named_duplicate_scout_run_model_fails_ownership(
    monkeypatch, tmp_path: Path
) -> None:
    package = _semantic_fixture(tmp_path)
    duplicate = package / "scout/duplicate.py"
    duplicate.write_text(
        "class AnythingElse:\n"
        "    run_id: str\n"
        "    started_at: object\n"
        "    run_status: str\n"
        "    repository_id: str\n"
        "    repository_name: str\n"
        "    repository_status: str\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(contract, "BACKEND_PACKAGE_ROOT", package)
    violations = contract.check_observability_projection_contract()
    assert any("second Run identity/status representation" in item.message for item in violations)
