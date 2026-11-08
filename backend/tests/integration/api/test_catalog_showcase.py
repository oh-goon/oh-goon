"""The guided API uses native evidence, the original worker and retained bytes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id, repository_subject_instance_id
from tests.integration.api.client import authenticated_api_client

ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="native POSIX worker proof")


@dataclass
class CatalogCase:
    client: TestClient
    runtime: ApplicationRuntime
    repository: Path

    @property
    def route(self) -> str:
        return f"/api/repositories/{repository_id(self.repository)}/catalog"

    def explore(
        self, query: str = "submit_order", path: str | None = "src/service.py"
    ) -> dict[str, Any]:
        response = self.client.post(
            self.route + "/explore",
            json={
                "expected_repository_instance_id": repository_subject_instance_id(self.repository),
                "query": query,
                "path": path,
            },
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert isinstance(value, dict)
        return value


def _commit_synthetic(repository: Path, name: str, email: str, message: str) -> None:
    environment = dict(os.environ)
    environment.update(
        GIT_AUTHOR_NAME=name,
        GIT_COMMITTER_NAME=name,
        GIT_AUTHOR_EMAIL=email,
        GIT_COMMITTER_EMAIL=email,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            message,
        ],
        env=environment,
        check=True,
        timeout=10,
    )


@pytest.fixture
def catalog_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CatalogCase]:
    repository = tmp_path / "sample-repo"
    shutil.copytree(ROOT / "examples/private-code-catalog/sample-repo", repository)
    subprocess.run(["git", "init", "-q", "--template=", str(repository)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True, timeout=10)
    _commit_synthetic(
        repository,
        "Synthetic Contributor",
        "private-email-sentinel@example.invalid",
        "Seed fixture",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository)
        app = create_app(local_access_token="catalog-native-api-test-local-access")
        app.dependency_overrides[dependencies.catalog_showcase_service] = (
            lambda: runtime.catalog_showcase
        )
        app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: runtime.runs
        with closing(authenticated_api_client(app)) as client:
            yield CatalogCase(client, runtime, repository)
    finally:
        runtime.shutdown(wait_for_runs=True)


def _candidate() -> dict[str, Any]:
    return {
        "name": "order-gateway",
        "description": "A synthetic guided catalog example.",
        "owner": "group:default/platform-team",
        "type": "service",
        "lifecycle": "experimental",
        "languages": ["python", "typescript"],
    }


def _preview(case: CatalogCase, evidence: dict[str, Any]) -> dict[str, Any]:
    response = case.client.post(
        case.route + "/previews",
        json={"source_authority": evidence["source_authority"], "candidate": _candidate()},
    )
    assert response.status_code == 200, response.text
    value = response.json()
    assert isinstance(value, dict)
    return value


def _run_request(preview: dict[str, Any], identifier: str) -> dict[str, Any]:
    return {
        "source_authority": preview["source_authority"],
        "candidate": preview["candidate"],
        "expected_plan_id": preview["plan_id"],
        "expected_plan_sha256": preview["plan_sha256"],
        "expected_execution_digest": preview["execution_digest"],
        "fleet_run_id": identifier,
    }


def test_guided_native_evidence_review_run_and_retained_export(
    catalog_case: CatalogCase, tmp_path: Path
) -> None:
    case = catalog_case
    evidence = case.explore()
    assert {row["name"] for row in evidence["languages"]} >= {"python", "typescript"}
    assert any(
        hit["path"] == "src/service.py" and hit["first_line"] is not None and hit["first_line"] > 0
        for hit in evidence["hits"]
    )
    assert any("src/order_status.py" in row["resolved_paths"] for row in evidence["relations"])
    assert evidence["qualification"]["freshness"] == "unknown"
    assert evidence["qualification"]["repository_wide_completeness"] == "not-claimed"
    assert evidence["git_author"]["author"] == "Synthetic Contributor"
    assert "private-email-sentinel" not in json.dumps(evidence)
    assert evidence["ownership_status"] == "manual-review-required"
    preview = _preview(case, evidence)
    assert not case.runtime.runs.history().items
    assert preview["source_authority"] == evidence["source_authority"]
    assert preview["provider_mode"] == "none"
    accepted = case.client.post(
        case.route + "/runs", json=_run_request(preview, "guided-native-run")
    )
    assert accepted.status_code == 202, accepted.text
    completed = case.runtime.runs.wait_for_terminal("guided-native-run", timeout=45)
    assert completed.game_tape.status.value == "PASSED", completed.operations
    assert isinstance(completed.run.target, FleetPlanTarget)
    assert (
        completed.run.target.reviewed_repository_source
        == RepositorySourceAuthority.model_validate(evidence["source_authority"])
    )
    readback = case.client.get("/api/fleet/runs/guided-native-run")
    assert readback.status_code == 200 and readback.json()["game_tape"]["status"] == "PASSED"
    plan_evidence = completed.operations[0].evidence
    assert isinstance(plan_evidence, FleetPlanOperationEvidence)
    step = plan_evidence.plan.steps[0]
    destination = tmp_path / "catalog-info.yaml"
    request = {
        "repository_id": repository_id(case.repository),
        "step_id": step.step_id,
        "path": "catalog-info.yaml",
        "destination": str(destination),
    }
    exported = case.client.post("/api/fleet/runs/guided-native-run/artifact-exports", json=request)
    assert exported.status_code == 200, exported.text
    assert exported.json()["status"] == "exported"
    assert exported.json()["cleanup_status"] == "verified"
    assert destination.read_text() == preview["yaml"]
    conflict = case.client.post("/api/fleet/runs/guided-native-run/artifact-exports", json=request)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "ARTIFACT_EXPORT_FAILED"
    assert destination.read_text() == preview["yaml"]
    foreign = {
        **request,
        "repository_id": "another-repository",
        "destination": str(tmp_path / "foreign.yaml"),
    }
    assert (
        case.client.post(
            "/api/fleet/runs/guided-native-run/artifact-exports", json=foreign
        ).status_code
        == 409
    )
    assert not (tmp_path / "foreign.yaml").exists()
    assert not (case.repository / "catalog-info.yaml").exists()
    assert not (case.repository / ".hashmarks").exists()


def test_guided_generation_refuses_changed_candidate_and_source_before_run(
    catalog_case: CatalogCase,
) -> None:
    case = catalog_case
    preview = _preview(case, case.explore())
    changed = _run_request(preview, "changed-candidate")
    changed["candidate"] = {**changed["candidate"], "description": "A different reviewed value."}
    assert case.client.post(case.route + "/runs", json=changed).status_code == 400
    assert not case.runtime.runs.history().items
    source = case.repository / "src/service.py"
    source.write_text(source.read_text() + "\n# changed after review\n")
    stale = case.client.post(case.route + "/runs", json=_run_request(preview, "stale-source"))
    assert stale.status_code == 409, stale.text
    assert stale.json()["code"] == "REVIEWED_SOURCE_STALE"
    assert not case.runtime.runs.history().items


def test_native_relationship_limits_remain_distinct_from_search_limits(
    catalog_case: CatalogCase,
) -> None:
    baseline = catalog_case.explore()
    assert baseline["relationship_qualification"]["unresolved_reasons"] == []
    source = catalog_case.repository / "src/service.py"
    source.write_text(
        "from .order_status import accepted_status\n\ndef submit_order(order_id: str):\n"
        + "    accepted_status()\n" * 16
        + "    return {'id': order_id}\n"
    )
    observed = catalog_case.explore()
    native = observed["technical_evidence"]["relationships"]
    qualifier = observed["relationship_qualification"]
    assert len(observed["relations"]) == qualifier["call_limit_per_symbol"] == 8
    assert observed["qualification"]["truncated"] is False
    assert qualifier["max_depth"] == native["bounds"]["max_depth"] == 1
    assert "call-limit-reached" in qualifier["unresolved_reasons"]
    assert (
        qualifier["lifecycle_completeness"]
        == native["resource_lifecycle"]["coverage"]["completeness"]
    )
    assert qualifier["lifecycle_truncated"] == native["resource_lifecycle"]["coverage"]["truncated"]


def test_guided_empty_denied_and_private_validation_states(catalog_case: CatalogCase) -> None:
    case = catalog_case
    empty = case.explore(query="catalog_definitely_absent_needle", path=None)
    assert empty["hits"] == []
    assert empty["qualification"]["negative_evidence"] == "not-admissible"
    assert empty["git_author"]["status"] == "select-file"
    invalid = case.client.post(
        case.route + "/previews",
        json={
            "source_authority": empty["source_authority"],
            "candidate": {**_candidate(), "secret": "DENIED_INPUT_SENTINEL"},
        },
    )
    assert invalid.status_code == 422
    assert "DENIED_INPUT_SENTINEL" not in invalid.text
    assert not invalid.json()["details"]
    relative_export = case.client.post(
        "/api/fleet/runs/unread-run/artifact-exports",
        json={
            "repository_id": repository_id(case.repository),
            "step_id": "catalog",
            "path": "catalog-info.yaml",
            "destination": "relative-private-sentinel.yaml",
        },
    )
    assert relative_export.status_code == 422
    assert "relative-private-sentinel" not in relative_export.text
    assert not relative_export.json()["details"]
    escaped = case.client.post(
        case.route + "/explore",
        json={
            "expected_repository_instance_id": repository_subject_instance_id(case.repository),
            "query": "submit_order",
            "path": "../outside-source-sentinel.py",
        },
    )
    assert escaped.status_code == 400
    assert "outside-source-sentinel" not in escaped.text
    outside = case.repository.parent / "denied-external-source.py"
    outside.write_text("UNAUTHORIZED_SOURCE_SENTINEL = True\n")
    link = case.repository / "outside-link.py"
    link.symlink_to(outside)
    refused = case.client.post(
        case.route + "/explore",
        json={
            "expected_repository_instance_id": repository_subject_instance_id(case.repository),
            "query": "UNAUTHORIZED_SOURCE_SENTINEL",
            "path": "outside-link.py",
        },
    )
    assert refused.status_code == 400
    assert "UNAUTHORIZED_SOURCE_SENTINEL" not in refused.text
    case.runtime.repositories.exclude(
        str(case.repository), "excluded", repository_subject_instance_id(case.repository)
    )
    denied = case.client.post(
        case.route + "/explore",
        json={
            "expected_repository_instance_id": repository_subject_instance_id(case.repository),
            "query": "submit_order",
        },
    )
    assert denied.status_code == 404
    assert not case.runtime.runs.history().items


@pytest.mark.parametrize("git_parent", [False, True])
def test_filesystem_subject_never_inherits_parent_git_author(
    catalog_case: CatalogCase, tmp_path: Path, git_parent: bool
) -> None:
    parent = tmp_path / "parent"
    selected = parent / "selected-subject"
    selected.mkdir(parents=True)
    (selected / "service.py").write_text("def selected_work():\n    return 1\n")
    if git_parent:
        subprocess.run(["git", "init", "-q", "--template=", str(parent)], check=True, timeout=10)
        subprocess.run(["git", "-C", str(parent), "add", "."], check=True, timeout=10)
        _commit_synthetic(
            parent, "FOREIGN_PARENT_AUTHOR", "foreign@example.invalid", "Parent-only history"
        )
    catalog_case.runtime.locations.add(selected)
    response = catalog_case.client.post(
        f"/api/repositories/{repository_id(selected)}/catalog/explore",
        json={
            "expected_repository_instance_id": repository_subject_instance_id(selected),
            "query": "selected_work",
            "path": "service.py",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["hits"]
    assert response.json()["git_author"]["status"] == "unavailable"
    assert "FOREIGN_PARENT_AUTHOR" not in response.text
    if not git_parent:
        plain = CatalogCase(catalog_case.client, catalog_case.runtime, selected)
        foreign_source = catalog_case.explore()["source_authority"]
        wrong_scope = plain.client.post(
            plain.route + "/previews",
            json={"source_authority": foreign_source, "candidate": _candidate()},
        )
        assert wrong_scope.status_code == 409
        assert wrong_scope.json()["code"] == "REVIEWED_SOURCE_STALE"
        preview = _preview(plain, response.json())
        accepted = plain.client.post(
            plain.route + "/runs", json=_run_request(preview, "filesystem-guided-run")
        )
        assert accepted.status_code == 202, accepted.text
        completed = plain.runtime.runs.wait_for_terminal("filesystem-guided-run", timeout=45)
        assert completed.game_tape.status.value == "PASSED"
        assert completed.repository_admissions[0].repository_id == repository_id(selected)
        assert not (selected / "catalog-info.yaml").exists()
