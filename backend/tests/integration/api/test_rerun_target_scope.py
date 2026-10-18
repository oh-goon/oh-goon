from __future__ import annotations

from pathlib import Path

from tests.integration.api.client import authenticated_api_client

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.vocabulary import OperationAction, Verdict
from oh_goon.runtime.database import LocalDatabase
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack


class _PartialRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        if repository.name == "beta":
            raise RuntimeError("beta deliberate failure")
        return _ready_execution(repository, action, fleet_run_id=fleet_run_id)


class _ReportedPartialRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        if repository.name == "beta":
            return OperationExecution(
                stdout="failed",
                stderr="",
                report=make_operation_report(
                    repository,
                    action,
                    fleet_run_id=fleet_run_id,
                    verdict=Verdict.FAILED,
                    reason_code=ReasonCode.OPERATION_FAILED,
                    returncode=23,
                ),
            )
        return _ready_execution(repository, action, fleet_run_id=fleet_run_id)


class _RecordingReadyRunner:
    def __init__(self) -> None:
        self.repositories: list[str] = []

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        self.repositories.append(repository.name)
        return _ready_execution(repository, action, fleet_run_id=fleet_run_id)


def _ready_execution(
    repository: Path, action: OperationAction, *, fleet_run_id: str | None
) -> OperationExecution:
    return OperationExecution(
        stdout="ready",
        stderr="",
        report=make_operation_report(
            repository,
            action,
            fleet_run_id=fleet_run_id,
            verdict=Verdict.READY,
            reason_code=ReasonCode.READY,
        ),
    )


def _seed_repository(root: Path, name: str) -> None:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        f"[project]\nname='{name}'\nversion='0.1.0'\n", encoding="utf-8"
    )


def _stack(database_path: Path, runner: object):
    database = LocalDatabase(database_path)
    state = local_state(database)
    fleet, manager, _, _ = fleet_stack(state, runner)
    return state, fleet, manager


def _bind(fleet: object, manager: object) -> None:
    api_app.dependency_overrides[dependencies.fleet_service] = lambda: fleet
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager


def _clear(manager: object) -> None:
    manager.shutdown()
    api_app.dependency_overrides.clear()


def test_partial_failure_rerun_excludes_durably_successful_sibling(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    _seed_repository(root, "beta")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _PartialRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    try:
        assert client.post(
            "/api/fleet/runs", json={"fleet_run_id": "partial-parent", "action": "init"}
        ).status_code == 202
        manager.wait_for_terminal("partial-parent", timeout=2)
        parent = client.get("/api/fleet/runs/partial-parent").json()
        assert parent["run"]["status"] == "FAILED"
        assert [(row["repository"]["name"], row["outcome"]["state"]) for row in parent["operations"]] == [
            ("alpha", "successful")
        ]
    finally:
        client.close()
        _clear(manager)

    rerun_runner = _RecordingReadyRunner()
    _, fleet2, manager2 = _stack(database_path, rerun_runner)
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        rerun = client.post(
            "/api/fleet/runs/partial-parent/rerun",
            json={"new_fleet_run_id": "partial-child"},
        )
        assert rerun.status_code == 202
        assert [row["name"] for row in rerun.json()["targets"]] == ["beta"]
        manager2.wait_for_terminal("partial-child", timeout=2)
        child = client.get("/api/fleet/runs/partial-child").json()
        assert child["execution"]["rerun_from_run_id"] == "partial-parent"
        assert child["run"]["repository_scope"] == {
            "kind": "EXPLICIT",
            "repository_ids": [parent["run"]["targets"][1]["repository_id"]],
        }
        assert rerun_runner.repositories == ["beta"]
        assert [row["repository"]["name"] for row in child["operations"]] == ["beta"]
    finally:
        client.close()
        _clear(manager2)


def test_committed_failed_operation_rerun_replays_only_failed_repository(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    _seed_repository(root, "beta")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _ReportedPartialRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    try:
        assert client.post(
            "/api/fleet/runs", json={"fleet_run_id": "reported-parent", "action": "init"}
        ).status_code == 202
        manager.wait_for_terminal("reported-parent", timeout=2)
        parent = client.get("/api/fleet/runs/reported-parent").json()
        assert {
            (row["repository"]["name"], row["outcome"]["state"])
            for row in parent["operations"]
        } == {
            ("alpha", "successful"),
            ("beta", "failed"),
        }
    finally:
        client.close()
        _clear(manager)

    rerun_runner = _RecordingReadyRunner()
    _, fleet2, manager2 = _stack(database_path, rerun_runner)
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        rerun = client.post(
            "/api/fleet/runs/reported-parent/rerun",
            json={"new_fleet_run_id": "reported-child"},
        )
        assert rerun.status_code == 202
        assert [row["name"] for row in rerun.json()["targets"]] == ["beta"]
        manager2.wait_for_terminal("reported-child", timeout=2)
        assert rerun_runner.repositories == ["beta"]
    finally:
        client.close()
        _clear(manager2)


def test_successful_multi_repository_rerun_preserves_full_target_set(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    _seed_repository(root, "beta")
    database_path = tmp_path / "state.db"

    parent_runner = _RecordingReadyRunner()
    state, fleet, manager = _stack(database_path, parent_runner)
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    try:
        assert client.post(
            "/api/fleet/runs", json={"fleet_run_id": "successful-parent", "action": "init"}
        ).status_code == 202
        manager.wait_for_terminal("successful-parent", timeout=2)
        assert sorted(parent_runner.repositories) == ["alpha", "beta"]
    finally:
        client.close()
        _clear(manager)

    rerun_runner = _RecordingReadyRunner()
    _, fleet2, manager2 = _stack(database_path, rerun_runner)
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        rerun = client.post(
            "/api/fleet/runs/successful-parent/rerun",
            json={"new_fleet_run_id": "successful-child"},
        )
        assert rerun.status_code == 202
        assert sorted(row["name"] for row in rerun.json()["targets"]) == ["alpha", "beta"]
        manager2.wait_for_terminal("successful-child", timeout=2)
        assert sorted(rerun_runner.repositories) == ["alpha", "beta"]
    finally:
        client.close()
        _clear(manager2)
