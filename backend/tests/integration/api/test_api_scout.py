from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.integration.api.client import authenticated_api_client

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.scout.policy import SCOUT_MEASUREMENT_SCHEMA_VERSION
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import scout_analytical_database_path
from oh_goon.scout.service import ScoutService


class _EmptyJournal:
    def run_details_changed_after(self, revision: int):
        return 41, ()


def _client(tmp_path: Path, *, enabled: bool = True) -> TestClient:
    service = ScoutService(
        _EmptyJournal(),
        database=LocalDatabase(tmp_path / "state.sqlite3"),
        enabled=enabled,
    )  # type: ignore[arg-type]
    api_app.dependency_overrides[dependencies.scout_service] = lambda: service
    return authenticated_api_client(api_app)


def test_scout_read_api_exposes_empty_truthful_overview_and_metric_dictionary(
    tmp_path: Path,
) -> None:
    with _client(tmp_path) as client:
        overview = client.get("/api/scout/overview", params={"hours": 168, "limit": 20})
        metrics = client.get("/api/scout/metrics")
        health = client.get("/api/scout/health")

    assert overview.status_code == 200
    payload = overview.json()
    assert payload["enabled"] is True
    assert payload["journal_revision"] == 41
    assert payload["window"]["hours"] == 168
    assert payload["executions"] == 0
    assert payload["active_repositories"] == 0
    assert payload["totals"]["memory_time_available"] is False
    assert payload["totals"]["memory_time_gib_seconds"] is None

    assert metrics.status_code == 200
    metric_payload = metrics.json()
    assert metric_payload["measurement_schema_version"] == SCOUT_MEASUREMENT_SCHEMA_VERSION
    definitions = {item["metric"]: item for item in metric_payload["definitions"]}
    assert "total_cpu" in definitions
    assert "cpu_per_run" in definitions
    assert "run_frequency" in definitions
    assert "failure_cpu" in definitions
    assert definitions["average_cpu"]["unit"] == "cores"
    assert definitions["peak_cpu"]["aggregation"] == "Unavailable."
    assert definitions["memory_time"]["aggregation"] == "Unavailable."

    assert health.status_code == 200
    health_payload = health.json()
    assert health_payload["journal_revision"] == 41
    assert health_payload["processed_journal_revision"] == 41
    assert health_payload["lag_revisions"] == 0
    assert health_payload["snapshot_cache_entries"] == 1
    assert health_payload["snapshot_cache_hits"] == 0
    assert health_payload["snapshot_cache_misses"] == 1
    assert health_payload["last_snapshot_load_ms"] is not None
    assert health_payload["last_snapshot_load_ms"] >= 0
    assert health_payload["analytical_storage_bytes"] > 0
    assert health_payload["detail_retention_days"] == 90
    assert health_payload["run_retention_days"] == 365


def test_scout_findings_are_paginated_and_request_bounds_are_validated(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        findings = client.get(
            "/api/scout/findings",
            params={"hours": 24, "category": "CPU_OUTLIER", "offset": 0, "limit": 20},
        )
        invalid_window = client.get("/api/scout/overview", params={"hours": 2161})
        invalid_limit = client.get("/api/scout/findings", params={"limit": 101})

    assert findings.status_code == 200
    payload = findings.json()
    assert payload["category"] == "CPU_OUTLIER"
    assert payload["total"] == 0
    assert payload["offset"] == 0
    assert payload["limit"] == 20
    assert payload["items"] == []
    assert invalid_window.status_code == 422
    assert invalid_limit.status_code == 422


def test_disabled_scout_is_observational_and_does_not_turn_into_execution_failure(
    tmp_path: Path,
) -> None:
    with _client(tmp_path, enabled=False) as client:
        overview = client.get("/api/scout/overview")
        repository = client.get("/api/scout/repositories/repo-does-not-matter")

    assert overview.status_code == 200
    assert overview.json()["enabled"] is False
    assert repository.status_code == 400
    error = repository.json()
    assert error["code"] == "INVALID_REQUEST"
    assert "Scout is disabled" in error["message"]


def test_scout_search_is_read_only_bounded_and_url_query_driven(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        search = client.get(
            "/api/scout/search", params={"q": "repo", "hours": 24, "offset": 20, "limit": 20}
        )
        blank = client.get("/api/scout/search", params={"q": ""})
        oversized = client.get("/api/scout/search", params={"q": "x" * 121})

    assert search.status_code == 200
    payload = search.json()
    assert payload["enabled"] is True
    assert payload["query"] == "repo"
    assert payload["total"] == 0
    assert payload["offset"] == 20
    assert payload["limit"] == 20
    assert payload["items"] == []
    assert blank.status_code == 422
    assert oversized.status_code == 422


def test_scout_plan_route_is_read_only_and_reports_missing_observation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/scout/plans/plan-missing", params={"hours": 24})

    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "OPERATION_NOT_FOUND"
    assert "Plan 'plan-missing'" in payload["message"]


def test_scout_goon_leaders_are_read_only_and_paginated(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        leaders = client.get(
            "/api/scout/goon-leaders", params={"hours": 24, "offset": 20, "limit": 20}
        )

    assert leaders.status_code == 200
    payload = leaders.json()
    assert payload["enabled"] is True
    assert payload["total"] == 0
    assert payload["offset"] == 20
    assert payload["limit"] == 20
    assert payload["items"] == []


def test_scout_api_recovers_disposable_corrupt_sidecar_without_changing_canonical_state(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.sqlite3"
    database = LocalDatabase(database_path)
    canonical_revision = database.durable_revision()
    sidecar = scout_analytical_database_path(database.path)
    sidecar.write_bytes(b"not-a-sqlite-database")
    service = ScoutService(_EmptyJournal(), database=database, enabled=True)  # type: ignore[arg-type]
    api_app.dependency_overrides[dependencies.scout_service] = lambda: service

    with authenticated_api_client(api_app) as client:
        response = client.get("/api/scout/overview")

    assert response.status_code == 200
    assert response.json()["journal_revision"] == 41
    assert LocalDatabase(database_path).durable_revision() == canonical_revision


def test_scout_detail_pagination_rejects_irrelevant_or_unscoped_offsets(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        wrong_section = client.get(
            "/api/scout/plans/plan-missing",
            params={"section": "versions", "offset": 100},
        )
        unscoped_offset = client.get(
            "/api/scout/plans/plan-missing",
            params={"offset": 100},
        )

    assert wrong_section.status_code == 400
    assert "Plan detail section 'versions' is not valid" in wrong_section.json()["message"]
    assert unscoped_offset.status_code == 400
    assert "offset requires an explicit section" in unscoped_offset.json()["message"]
