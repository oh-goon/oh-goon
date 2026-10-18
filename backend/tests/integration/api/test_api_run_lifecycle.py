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


class ApiReadyRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: object = None,
    ) -> OperationExecution:
        report = make_operation_report(
            repository,
            action,
            fleet_run_id=fleet_run_id,
            verdict=Verdict.READY,
            reason_code=ReasonCode.READY,
        )
        return OperationExecution(stdout="", stderr="", report=report)


class ApiCancellableRunner:
    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        fleet_run_id: str | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> OperationExecution:
        assert cancel_requested is not None
        deadline = time.monotonic() + 15
        while not cancel_requested() and time.monotonic() < deadline:
            time.sleep(0.01)
        raise OperationCancelledError("cancelled")


def test_async_run_api_returns_run_detail_from_session_journal(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")

    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, ApiReadyRunner())
    api_app.dependency_overrides[dependencies.fleet_service] = lambda: fleet
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager

    client = authenticated_api_client(api_app)
    try:
        started = client.post(
            "/api/fleet/runs", json={"fleet_run_id": "fleet-api-run", "action": "init"}
        )
        assert started.status_code == 202
        run_id = started.json()["fleet_run_id"]

        manager.wait_for_terminal(run_id, timeout=2)
        detail = client.get(f"/api/fleet/runs/{run_id}").json()

        assert detail["run"]["status"] == "COMPLETED"
        assert detail["run"]["targets"][0]["name"] == "api"
        assert len(detail["operations"]) == 1
        operation_id = detail["operations"][0]["provenance"]["operation_id"]
        events = client.get(f"/api/fleet/runs/{run_id}/events").json()
        assert events["run"]["progress"]["completed"] == 1
        completed = next(
            event for event in events["events"] if event["event_type"] == "REPOSITORY_COMPLETED"
        )
        assert completed["operation_id"] == operation_id
        assert "reason_code" not in completed
        snapshot = client.get("/api/fleet").json()
        assert snapshot["summary"]["ready_for_update"] == 1
        latest_init = next(
            detail
            for detail in snapshot["latest_fleet_runs"]
            if detail["run"]["capability"]["action"] == "init"
        )
        assert latest_init["run"]["fleet_run_id"] == run_id
        assert latest_init["operations"][0]["provenance"]["operation_id"] == operation_id
        assert latest_init["outcome"] == "SUCCESS"
        assert snapshot["provenance"]["journal_revision"] >= events["journal_revision"] > 0
        assert (
            snapshot["provenance"]["durable_revision"]
            >= snapshot["provenance"]["journal_revision"]
            > 0
        )
    finally:
        client.close()
        manager.shutdown()
        api_app.dependency_overrides.clear()


def test_rerun_api_derives_authority_from_existing_run_identity(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")

    database = LocalDatabase(tmp_path / "rerun-api.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, ApiReadyRunner())
    api_app.dependency_overrides[dependencies.fleet_service] = lambda: fleet
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager

    client = authenticated_api_client(api_app)
    try:
        started = client.post(
            "/api/fleet/runs", json={"fleet_run_id": "fleet-api-rerun-source", "action": "init"}
        )
        assert started.status_code == 202
        manager.wait_for_terminal("fleet-api-rerun-source", timeout=2)
        original = client.get("/api/fleet/runs/fleet-api-rerun-source").json()["run"]

        rerun = client.post(
            "/api/fleet/runs/fleet-api-rerun-source/rerun",
            json={"new_fleet_run_id": "fleet-api-rerun-child"},
        )
        assert rerun.status_code == 202
        assert rerun.json()["fleet_run_id"] == "fleet-api-rerun-child"
        manager.wait_for_terminal("fleet-api-rerun-child", timeout=2)
        child = client.get("/api/fleet/runs/fleet-api-rerun-child").json()["run"]

        assert child["capability"] == original["capability"]
        assert child["target"] == original["target"]
        assert child["repository_scope"] == original["repository_scope"]
        assert child["source"] == original["source"]
        assert child["targets"] == original["targets"]

        existing = client.post(
            "/api/fleet/runs",
            json={"fleet_run_id": "fleet-api-rerun-existing", "action": "verify"},
        )
        assert existing.status_code == 202
        manager.wait_for_terminal("fleet-api-rerun-existing", timeout=2)
        collision = client.post(
            "/api/fleet/runs/fleet-api-rerun-source/rerun",
            json={"new_fleet_run_id": "fleet-api-rerun-existing"},
        )
        assert collision.status_code == 409
        assert collision.json()["code"] == "STATE_CONFLICT"
    finally:
        client.close()
        manager.shutdown()
        api_app.dependency_overrides.clear()


def test_cancel_api_moves_run_to_cancelled_terminal_state(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")

    database = LocalDatabase(tmp_path / "cancel.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, ApiCancellableRunner())
    api_app.dependency_overrides[dependencies.fleet_service] = lambda: fleet
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager

    client = authenticated_api_client(api_app)
    try:
        started = client.post(
            "/api/fleet/runs", json={"fleet_run_id": "fleet-api-cancel", "action": "init"}
        )
        assert started.status_code == 202
        run_id = started.json()["fleet_run_id"]

        cancelled = client.post(f"/api/fleet/runs/{run_id}/cancel")
        assert cancelled.status_code == 202
        assert cancelled.json()["status"] in {"CANCELLING", "CANCELLED"}

        manager.wait_for_terminal(run_id, timeout=2)
        detail = client.get(f"/api/fleet/runs/{run_id}").json()

        assert detail["run"]["status"] == "CANCELLED"
        assert detail["outcome"] == "CANCELLED"
        repeated = client.post(f"/api/fleet/runs/{run_id}/cancel")
        assert repeated.status_code == 202
        assert repeated.json()["status"] == "CANCELLED"
        event_batch = client.get(f"/api/fleet/runs/{run_id}/events").json()
        event_types = [event["event_type"] for event in event_batch["events"]]
        assert "RUN_CANCEL_REQUESTED" in event_types
        assert event_types[-1] == "RUN_CANCELLED"

        snapshot = client.get("/api/fleet").json()
        assert snapshot["provenance"]["journal_revision"] >= event_batch["journal_revision"] > 0
        assert snapshot["active_fleet_run"] is None
    finally:
        client.close()
        manager.shutdown()
        api_app.dependency_overrides.clear()


def test_run_history_api_lists_compact_durable_runs_and_supports_paging(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")

    database = LocalDatabase(tmp_path / "history-api.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, ApiReadyRunner())
    api_app.dependency_overrides[dependencies.fleet_service] = lambda: fleet
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager

    client = authenticated_api_client(api_app)
    try:
        run_ids: list[str] = []
        for index in range(2):
            started = client.post(
                "/api/fleet/runs",
                json={"fleet_run_id": f"fleet-api-history-{index + 1}", "action": "init"},
            )
            assert started.status_code == 202
            run_id = started.json()["fleet_run_id"]
            run_ids.append(run_id)
            manager.wait_for_terminal(run_id, timeout=2)
            detail = client.get(f"/api/fleet/runs/{run_id}").json()
            assert detail["run"]["status"] == "COMPLETED"

        first = client.get("/api/fleet/runs?limit=1")
        assert first.status_code == 200
        payload = first.json()
        assert payload["items"][0]["run"]["fleet_run_id"] == run_ids[-1]
        assert payload["items"][0]["outcome"] == "SUCCESS"
        assert "operations" not in payload["items"][0]
        cursor = payload["next_before_revision"]
        assert cursor is not None

        second = client.get(f"/api/fleet/runs?limit=1&before_revision={cursor}").json()
        assert second["items"][0]["run"]["fleet_run_id"] == run_ids[0]
        assert second["next_before_revision"] is None
    finally:
        client.close()
        manager.shutdown()
        api_app.dependency_overrides.clear()


def test_execution_observations_api_projects_existing_run_game_tape_facts(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text("[project]\nname='api'\nversion='0.1.0'\n")

    database = LocalDatabase(tmp_path / "observations-api.db")
    state = local_state(database)
    state.add_location(root)
    fleet, manager, _, _ = fleet_stack(state, ApiReadyRunner())
    api_app.dependency_overrides[dependencies.fleet_service] = lambda: fleet
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: manager

    client = authenticated_api_client(api_app)
    try:
        started = client.post(
            "/api/fleet/runs",
            json={"fleet_run_id": "fleet-api-observations", "action": "init"},
        )
        assert started.status_code == 202
        run_id = started.json()["fleet_run_id"]
        manager.wait_for_terminal(run_id, timeout=2)
        detail = client.get(f"/api/fleet/runs/{run_id}").json()
        started_at = detail["run"]["started_at"]

        response = client.get(
            "/api/fleet/runs/observations",
            params={
                "from_at": "2000-01-01T00:00:00Z",
                "to_at": "2099-01-01T00:00:00Z",
                "bucket": "month",
                "repository_query": "api",
            },
        )
        assert response.status_code == 400  # bounded historical query is enforced

        from datetime import datetime, timedelta

        when = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        response = client.get(
            "/api/fleet/runs/observations",
            params={
                "from_at": (when - timedelta(days=1)).isoformat(),
                "to_at": (when + timedelta(days=1)).isoformat(),
                "bucket": "day",
                "repository_query": "api",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_repositories"] == 1
        assert payload["items"][0]["repository_name"] == "api"
        assert payload["items"][0]["buckets"][0]["run_count"] == 1
        statuses = payload["items"][0]["buckets"][0]["statuses"]
        assert statuses == [{"status": "PASSED", "count": 1}]
    finally:
        client.close()
        manager.shutdown()
        api_app.dependency_overrides.clear()
