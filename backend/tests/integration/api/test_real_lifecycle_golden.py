from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.vocabulary import OperationAction, Verdict
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.runtime.database import LocalDatabase
from tests.factories.local_state import local_state
from tests.factories.operation_reports import make_operation_report
from tests.factories.runtime_stack import fleet_stack
from tests.integration.api.client import authenticated_api_client


class _ReadyRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
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


class _FailRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        raise RuntimeError(f"deliberate lifecycle failure for {repository.name}")


class _CancellableRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> OperationExecution:
        assert cancel_requested is not None
        deadline = time.monotonic() + 5
        while not cancel_requested() and time.monotonic() < deadline:
            time.sleep(0.005)
        raise OperationCancelledError("cancelled by golden lifecycle")


def _seed_repository(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        f"[project]\nname='{name}'\nversion='0.1.0'\n", encoding="utf-8"
    )
    return repository


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


def test_completed_run_survives_full_runtime_graph_reconstruction(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _ReadyRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    started = client.post(
        "/api/fleet/runs", json={"fleet_run_id": "golden-success", "action": "init"}
    )
    assert started.status_code == 202
    manager.wait_for_terminal("golden-success", timeout=2)
    before = client.get("/api/fleet/runs/golden-success").json()
    events_before = client.get("/api/fleet/runs/golden-success/events").json()
    client.close()
    _clear(manager)

    # Destroy every runtime owner and reconstruct the normal graph over the same durable DB.
    _, fleet2, manager2 = _stack(database_path, _ReadyRunner())
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        after = client.get("/api/fleet/runs/golden-success")
        events_after = client.get("/api/fleet/runs/golden-success/events")
        overview = client.get("/api/fleet")
        client.close()
        assert after.status_code == 200
        assert after.json()["run"]["status"] == "COMPLETED"
        assert after.json()["outcome"] == "SUCCESS"
        assert after.json()["operations"] == before["operations"]
        assert events_after.json()["events"] == events_before["events"]
        assert overview.json()["active_fleet_run"] is None
        assert any(
            row["run"]["fleet_run_id"] == "golden-success"
            for row in overview.json()["latest_fleet_runs"]
        )
    finally:
        _clear(manager2)


def test_failed_run_then_rerun_uses_new_runtime_authority_and_preserves_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _FailRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    assert (
        client.post(
            "/api/fleet/runs", json={"fleet_run_id": "golden-failed", "action": "init"}
        ).status_code
        == 202
    )
    manager.wait_for_terminal("golden-failed", timeout=2)
    failed = client.get("/api/fleet/runs/golden-failed").json()
    assert failed["run"]["status"] == "FAILED"
    assert failed["outcome"] == "FAILED"
    client.close()
    _clear(manager)

    # A fresh runtime performs the retry; no in-memory authority from the failed owner survives.
    _, fleet2, manager2 = _stack(database_path, _ReadyRunner())
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        rerun = client.post(
            "/api/fleet/runs/golden-failed/rerun",
            json={"new_fleet_run_id": "golden-retry"},
        )
        assert rerun.status_code == 202
        manager2.wait_for_terminal("golden-retry", timeout=2)
        source_after = client.get("/api/fleet/runs/golden-failed").json()
        retry = client.get("/api/fleet/runs/golden-retry").json()
        retry_events = client.get("/api/fleet/runs/golden-retry/events").json()["events"]
        client.close()
        assert source_after["run"]["status"] == "FAILED"
        assert retry["run"]["status"] == "COMPLETED"
        assert retry["outcome"] == "SUCCESS"
        assert retry["run"]["capability"] == source_after["run"]["capability"]
        assert retry["run"]["repository_scope"] == {
            "kind": "EXPLICIT",
            "repository_ids": [source_after["run"]["targets"][0]["repository_id"]],
        }
        assert retry_events[-1]["event_type"] == "RUN_COMPLETED"
    finally:
        _clear(manager2)


def test_cancelled_run_remains_terminal_after_runtime_reconstruction(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _CancellableRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    assert (
        client.post(
            "/api/fleet/runs", json={"fleet_run_id": "golden-cancel", "action": "init"}
        ).status_code
        == 202
    )
    cancelled = client.post("/api/fleet/runs/golden-cancel/cancel")
    assert cancelled.status_code == 202
    manager.wait_for_terminal("golden-cancel", timeout=2)
    before = client.get("/api/fleet/runs/golden-cancel").json()
    assert before["run"]["status"] == "CANCELLED"
    client.close()
    _clear(manager)

    _, fleet2, manager2 = _stack(database_path, _ReadyRunner())
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        after = client.get("/api/fleet/runs/golden-cancel").json()
        repeated = client.post("/api/fleet/runs/golden-cancel/cancel")
        overview = client.get("/api/fleet").json()
        client.close()
        assert after["run"]["status"] == "CANCELLED"
        assert after["outcome"] == "CANCELLED"
        assert repeated.status_code == 202
        assert repeated.json()["status"] == "CANCELLED"
        assert overview["active_fleet_run"] is None
    finally:
        _clear(manager2)


def test_multi_repository_run_preserves_distinct_operation_identity_after_reload(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    alpha = _seed_repository(root, "alpha")
    beta = _seed_repository(root, "beta")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _ReadyRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    assert (
        client.post(
            "/api/fleet/runs", json={"fleet_run_id": "golden-multi", "action": "init"}
        ).status_code
        == 202
    )
    manager.wait_for_terminal("golden-multi", timeout=2)
    before = client.get("/api/fleet/runs/golden-multi").json()
    client.close()
    _clear(manager)

    assert before["run"]["status"] == "COMPLETED"
    assert {row["name"] for row in before["run"]["targets"]} == {"alpha", "beta"}
    assert len(before["operations"]) == 2
    operation_ids = {row["provenance"]["operation_id"] for row in before["operations"]}
    assert len(operation_ids) == 2
    assert {Path(row["repository"]["path"]) for row in before["operations"]} == {alpha, beta}

    _, fleet2, manager2 = _stack(database_path, _ReadyRunner())
    _bind(fleet2, manager2)
    try:
        client = authenticated_api_client(api_app)
        after = client.get("/api/fleet/runs/golden-multi").json()
        client.close()
        assert {row["provenance"]["operation_id"] for row in after["operations"]} == operation_ids
        assert {Path(row["repository"]["path"]) for row in after["operations"]} == {alpha, beta}
    finally:
        _clear(manager2)


def test_explicit_repository_instance_scope_is_enforced_through_public_run_api(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repos"
    _seed_repository(root, "alpha")
    _seed_repository(root, "beta")
    database_path = tmp_path / "state.db"

    state, fleet, manager = _stack(database_path, _ReadyRunner())
    state.add_location(root)
    _bind(fleet, manager)
    client = authenticated_api_client(api_app)
    try:
        repositories = client.get("/api/fleet").json()["repositories"]
        alpha = next(row for row in repositories if row["repository"]["name"] == "alpha")
        repository_id = alpha["repository"]["repository_id"]
        instance_id = alpha["repository_instance_id"]

        admitted = client.post(
            "/api/fleet/runs",
            json={
                "fleet_run_id": "golden-explicit-alpha",
                "action": "init",
                "repository_ids": [repository_id],
                "repository_instance_ids": {repository_id: instance_id},
            },
        )
        assert admitted.status_code == 202
        assert [row["name"] for row in admitted.json()["targets"]] == ["alpha"]
        manager.wait_for_terminal("golden-explicit-alpha", timeout=2)

        stale = client.post(
            "/api/fleet/runs",
            json={
                "fleet_run_id": "golden-stale-instance",
                "action": "init",
                "repository_ids": [repository_id],
                "repository_instance_ids": {repository_id: "repo-instance-" + ("0" * 64)},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "TARGET_SELECTION_ERROR"
        assert "instance changed" in stale.json()["message"]
    finally:
        client.close()
        _clear(manager)
