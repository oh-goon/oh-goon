"""Real teaching workloads preserve their selected execution and artifact owners."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest
import yaml

from oh_goon.api import dependencies
from oh_goon.api.app import app as api_app
from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepExecution,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import repository_id
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.runtime.league_folders import LeagueFolderStore
from tests.integration.api.client import authenticated_api_client

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "template-certification" / "fixtures"
IRIS_PROJECT = ROOT / "templates/goons/ml/iris-train"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


def _native(argv: list[str], repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=repository, capture_output=True, text=True, timeout=120)


def _project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fixture: str | Path,
    group: str | None,
    *,
    register: bool = True,
) -> Path:
    repository = tmp_path / "repositories" / "subject"
    shutil.copytree(
        FIXTURES / fixture if isinstance(fixture, str) else fixture,
        repository,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", "ml-output"),
    )
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    for argv in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Teaching example",
            "-c",
            "user.email=example@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "real native fixture",
        ],
    ):
        result = _native(argv, repository)
        assert result.returncode == 0, result.stderr
    if (repository / "uv.lock").is_file():
        before = (repository / "uv.lock").read_bytes()
        prepared = _native(
            [
                "uv",
                "sync",
                "--frozen",
                "--no-default-groups",
                *(["--group", group] if group else []),
            ],
            repository,
        )
        assert prepared.returncode == 0, (prepared.stderr or prepared.stdout)[-8192:]
        assert (repository / "uv.lock").read_bytes() == before
    if register:
        LeagueFolderStore().replace((repository.parent,))
    return repository


def _cli(tmp_path: Path, *arguments: str, expected: int = 0) -> str:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    completed = subprocess.run(
        [str(Path(sys.executable).with_name("oh-goon")), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == expected, (
        arguments,
        completed.stdout[-8192:],
        completed.stderr[-8192:],
    )
    return completed.stdout


@pytest.fixture(scope="module")
def native_fixture_uv_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("teaching-project-uv-cache")


def _configuration(
    tmp_path: Path,
    repository: Path,
    key: str,
    kind: str,
    inputs: dict[str, str | int],
    capsys: pytest.CaptureFixture[str],
) -> Path:
    workflow = tmp_path / "workflow"
    assert main(["templates", "init", key, "--kind", kind, "--output", str(workflow)]) == 0
    path = workflow / "run.yml"
    selection = yaml.safe_load(path.read_text())
    selection["target"]["repository_id"] = repository_id(repository)
    selection["inputs"] = inputs
    path.write_text(yaml.safe_dump(selection), encoding="utf-8")
    assert main(["config", "validate", str(path), "--json"]) == 0
    assert main(["lock", str(path)]) == 0
    capsys.readouterr()
    return path


def _run(
    configuration: Path, capsys: pytest.CaptureFixture[str], *, expected: int
) -> FleetRunDetail:
    assert main(["run", str(configuration), "--json"]) == expected
    receipt = json.loads(capsys.readouterr().out)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        return runtime.runs.get(receipt["fleet_run_id"])
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.parametrize("threshold,expected", [(90, 0), (100, 1)])
def test_iris_project_command_trains_and_writes_actual_model_parameters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    threshold: int,
    expected: int,
) -> None:
    repository = _project(tmp_path, monkeypatch, IRIS_PROJECT, None)
    completed = _native(
        [
            "uv",
            "run",
            "--no-sync",
            "iris-train",
            "--random-seed",
            "42",
            "--test-size-percent",
            "20",
            "--minimum-accuracy-percent",
            str(threshold),
            "--max-iterations",
            "300",
        ],
        repository,
    )
    assert completed.returncode == expected, completed.stderr
    metrics = json.loads((repository / "ml-output/iris-training-metrics.json").read_text())
    model = json.loads((repository / "ml-output/iris-model-parameters.json").read_text())
    assert metrics["training_examples"] == 120
    assert metrics["test_examples"] == 30
    assert metrics["random_seed"] == 42
    assert metrics["test_size_percent"] == 20
    assert metrics["max_iterations"] == 300
    assert 0.9 <= metrics["accuracy"] < 1
    assert len(model["coefficients"]) == len(model["classes"]) == 3
    assert all(len(row) == 4 for row in model["coefficients"])
    assert len(model["scaler_mean"]) == len(model["scaler_scale"]) == 4
    assert metrics["scikit_learn_version"] == model["scikit_learn_version"]
    if expected:
        assert metrics["accuracy"] < metrics["minimum_accuracy"]


def test_iris_strict_plan_requires_deployment_before_training(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _project(tmp_path, monkeypatch, IRIS_PROJECT, None)
    configuration = _configuration(tmp_path, repository, "ml/iris-training", "plan", {}, capsys)
    (configuration.parent / "plan.yml").write_text(
        "execution: {purpose: cicd}\ngoons:\n- ml/iris-train\n"
    )
    selection = yaml.safe_load(configuration.read_text())
    selection["source"] = {"kind": "file-plan", "path": "plan.yml"}
    configuration.write_text(yaml.safe_dump(selection), encoding="utf-8")
    assert main(["lock", str(configuration)]) == 0
    capsys.readouterr()
    assert main(["run", str(configuration), "--json"]) == 2
    assert not (repository / "ml-output").exists()
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        assert not runtime.runs.history().items
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_iris_atomic_wrapper_accepts_retained_captured_project_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _project(tmp_path, monkeypatch, IRIS_PROJECT, None)
    workflow = tmp_path / "workflow"
    assert (
        main(["templates", "init", "ml/iris-train", "--kind", "goon", "--output", str(workflow)])
        == 0
    )
    configuration = workflow / "run.yml"
    selection = yaml.safe_load(configuration.read_text())
    selection["target"]["repository_id"] = repository_id(repository)
    configuration.write_text(yaml.safe_dump(selection), encoding="utf-8")
    assert main(["config", "validate", str(configuration), "--json"]) == 0
    assert not (repository / "ml-output").exists()
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        assert not runtime.runs.history().items
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_iris_native_development_training_export_repeat_and_failure_through_fresh_cli_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_fixture_uv_cache: Path,
) -> None:
    monkeypatch.setenv("UV_CACHE_DIR", str(native_fixture_uv_cache))
    repository = _project(tmp_path, monkeypatch, IRIS_PROJECT, "dev", register=False)
    native_lock = (repository / "uv.lock").read_bytes()
    tested = _native(
        [
            "uv",
            "run",
            "--frozen",
            "--no-sync",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / "native-test-output"),
            "tests",
        ],
        repository,
    )
    assert tested.returncode == 0, (tested.stderr or tested.stdout)[-8192:]
    assert (repository / "uv.lock").read_bytes() == native_lock
    assert not (repository / "ml-output").exists()
    target_parent_members = set(repository.parent.iterdir())
    _cli(tmp_path, "league", "add-folder", str(repository))
    workflow = tmp_path / "workflow"
    _cli(
        tmp_path,
        "templates",
        "init",
        "ml/iris-training",
        "--kind",
        "plan",
        "--output",
        str(workflow),
        "--repository",
        str(repository),
    )
    configuration = workflow / "run.yml"
    selection = yaml.safe_load(configuration.read_text())
    selection["inputs"] = {
        "random_seed": 42,
        "test_size_percent": 20,
        "minimum_accuracy_percent": 90,
        "max_iterations": 300,
    }
    configuration.write_text(yaml.safe_dump(selection), encoding="utf-8")
    _cli(tmp_path, "lock", str(configuration))
    first = json.loads(_cli(tmp_path, "run", str(configuration), "--json"))
    first_id = first["fleet_run_id"]
    assert first["status"] == "COMPLETED" and first["outcome"] == "SUCCESS"
    # The execution process exited and its package workspace is gone. Export
    # must use retained recorded bytes, rather than the target or attempt files.
    assert set(repository.parent.iterdir()) == target_parent_members
    assert not (repository / "ml-output").exists()
    first_tape = json.loads(_cli(tmp_path, "plan", "tape", first_id, "--json"))
    events = json.loads(_cli(tmp_path, "plan", "events", first_id, "--json"))
    admitted = next(event for event in events["events"] if event["event_type"] == "RUN_ADMITTED")
    assert admitted["target"]["plan"]["execution"]["purpose"] == "verification"
    assert admitted["execution"]["kind"] == first_tape["execution"]["kind"] == "direct"
    fingerprint = first_tape["repositories"][0]["fingerprint"]
    assert fingerprint["execution_input_id"] is None
    assert fingerprint["runtime_root_sha256"] is None
    assert fingerprint["isolation_backend_id"] is None
    first_step = first_tape["repositories"][0]["steps"][0]
    assert first_tape["status"] == first_step["status"] == "PASSED"
    assert first_step["exit_code"] == 0
    assert first_step["settlement"]["cleanup"] == "VERIFIED"
    assert first_step["project_source"]["content_sha256"]
    assert first_step["execution_authority"] is None

    def exported_training(
        run_id: str, label: str
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        output = json.loads(_cli(tmp_path, "plan", "outputs", run_id, "--json"))
        steps = output["repositories"][0]["steps"]
        assert len(steps) == 1
        artifacts = {artifact["path"]: artifact for artifact in steps[0]["artifacts"]}
        assert set(artifacts) == {
            "ml-output/iris-training-metrics.json",
            "ml-output/iris-model-parameters.json",
        }
        documents = []
        for path, artifact in artifacts.items():
            assert artifact["status"] == "PRESENT" and artifact["retained_object"] is not None
            destination = tmp_path / f"{label}-{Path(path).name}"
            _cli(
                tmp_path,
                "plan",
                "export-artifact",
                run_id,
                "--step",
                steps[0]["step_id"],
                "--path",
                path,
                "--output",
                str(destination),
            )
            payload = destination.read_bytes()
            assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
            assert len(payload) == artifact["byte_count"]
            documents.append((path, json.loads(payload)))
        by_path = dict(documents)
        return (
            by_path["ml-output/iris-training-metrics.json"],
            by_path["ml-output/iris-model-parameters.json"],
            output,
        )

    first_metrics, first_model, first_outputs = exported_training(first_id, "first")
    assert first_metrics["random_seed"] == 42
    assert first_metrics["test_size_percent"] == 20
    assert first_metrics["max_iterations"] == 300
    assert first_metrics["minimum_accuracy"] == 0.9
    assert 0.9 <= first_metrics["accuracy"] < 1
    assert len(first_model["coefficients"]) == len(first_model["classes"]) == 3
    locked = (workflow / "goon.lock").read_bytes()
    selection["inputs"]["random_seed"] = 7
    configuration.write_text(yaml.safe_dump(selection), encoding="utf-8")
    stale = json.loads(_cli(tmp_path, "run", str(configuration), "--json", expected=2))
    assert stale["status"] == "rejected"
    assert stale["execution"] == "not_started_by_diagnostic"
    assert (workflow / "goon.lock").read_bytes() == locked
    assert set(repository.parent.iterdir()) == target_parent_members
    _cli(tmp_path, "lock", str(configuration))
    assert (workflow / "goon.lock").read_bytes() != locked
    second = json.loads(_cli(tmp_path, "run", str(configuration), "--json"))
    second_id = second["fleet_run_id"]
    assert second_id != first_id
    assert second["status"] == "COMPLETED" and second["outcome"] == "SUCCESS"
    second_tape = json.loads(_cli(tmp_path, "plan", "tape", second_id, "--json"))
    assert second["plan_id"] != first["plan_id"]
    assert (
        second_tape["repositories"][0]["steps"][0]["project_source"] == first_step["project_source"]
    )
    assert set(repository.parent.iterdir()) == target_parent_members
    second_metrics, second_model, second_outputs = exported_training(second_id, "second")
    assert second_metrics["random_seed"] == 7
    assert second_metrics["test_size_percent"] == first_metrics["test_size_percent"]
    assert second_metrics["max_iterations"] == first_metrics["max_iterations"]
    assert second_metrics["accuracy"] >= second_metrics["minimum_accuracy"]
    assert second_model != first_model

    # A real accuracy failure stops the next imported Goon through the same
    # public lifecycle. Explicit bindings keep the two occurrences independent.
    failure_plan = {
        "goons": [
            {
                "import": "ml/iris-train",
                "as": name,
                "with": {
                    "random_seed": 42,
                    "test_size_percent": 20,
                    "minimum_accuracy_percent": threshold,
                    "max_iterations": 300,
                },
            }
            for name, threshold in (("training", 100), ("later-training", 90))
        ]
    }
    (workflow / "failure-plan.yml").write_text(yaml.safe_dump(failure_plan), encoding="utf-8")
    selection["source"] = {"kind": "file-plan", "path": "failure-plan.yml"}
    selection["inputs"] = {}
    configuration.write_text(yaml.safe_dump(selection), encoding="utf-8")
    _cli(tmp_path, "lock", str(configuration))
    failure = json.loads(_cli(tmp_path, "run", str(configuration), "--json", expected=1))
    assert failure["outcome"] == "ATTENTION_REQUIRED"
    failed_tape = json.loads(_cli(tmp_path, "plan", "tape", failure["fleet_run_id"], "--json"))
    failed_steps = failed_tape["repositories"][0]["steps"]
    assert len(failed_steps) == 2
    assert failed_steps[0]["status"] == "FAILED" and failed_steps[0]["exit_code"] == 1
    assert failed_steps[1]["status"] == "SKIPPED"
    assert failed_steps[1]["artifacts"] == failed_steps[1]["outputs"] == []
    assert set(repository.parent.iterdir()) == target_parent_members
    assert (repository / "uv.lock").read_bytes() == native_lock
    assert not (repository / "ml-output").exists()
    for run_id, previous in ((first_id, first_outputs), (second_id, second_outputs)):
        assert json.loads(_cli(tmp_path, "plan", "outputs", run_id, "--json")) == previous
    reopened_metrics, reopened_model, _ = exported_training(first_id, "reopened")
    assert reopened_metrics == first_metrics
    assert reopened_model == first_model

    # One original Run survives scratch disposal and reference release. Live
    # observations must not rewrite its collection-time evidence or outcome.
    recorded = first_outputs["repositories"][0]["steps"][0]
    artifacts = {artifact["path"]: artifact for artifact in recorded["artifacts"]}
    total_bytes = sum(artifact["byte_count"] for artifact in artifacts.values())
    observation_flags = (
        "--observe-artifacts",
        "--artifact-limit",
        "2",
        "--artifact-byte-budget",
        str(total_bytes),
        "--artifact-time-budget-seconds",
        "5",
    )
    observed_paths = set(artifacts)
    for view in ("outputs", "tape", "handoff"):
        default_view = json.loads(_cli(tmp_path, "plan", view, first_id, "--json"))
        assert default_view["artifact_observation"] is None
        observed_view = json.loads(
            _cli(tmp_path, "plan", view, first_id, *observation_flags, "--json")
        )
        batch = observed_view["artifact_observation"]
        assert batch["fleet_run_id"] == first_id
        assert batch["total_artifacts"] == 2 and batch["omitted_artifacts"] == 0
        assert batch["bytes_scheduled"] == total_bytes
        assert {item["path"] for item in batch["observations"]} == observed_paths
        for item in batch["observations"]:
            original_artifact = artifacts[item["path"]]
            assert item["status"] == "AVAILABLE" and item["reason"] == "VERIFIED"
            assert item["recorded_sha256"] == original_artifact["sha256"]
            assert item["recorded_byte_count"] == original_artifact["byte_count"]
            assert item["reference_id"] == original_artifact["retained_object"]["reference_id"]
            assert item["staging_cleanup"] == "VERIFIED"
    human = _cli(tmp_path, "plan", "outputs", first_id, *observation_flags)
    assert "AVAILABLE" in human
    assert "VERIFIED" in human
    assert all(path in human for path in observed_paths)
    selected_path = "ml-output/iris-training-metrics.json"
    selected_reference = artifacts[selected_path]["retained_object"]
    owner_id = first_outputs["repositories"][0]["repository"]["repository_id"]
    assert selected_reference["owner_repository_id"] == owner_id
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    api_runtime = ApplicationRuntime.build(standard_local=True)
    api_app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: api_runtime.runs
    client = authenticated_api_client(api_app)
    observation_parameters = {
        "observe_artifacts": "true",
        "artifact_limit": "2",
        "artifact_byte_budget": str(total_bytes),
        "artifact_time_budget_seconds": "5",
    }
    try:
        with monkeypatch.context() as unobserved:
            forbidden_read = Mock(side_effect=AssertionError("Default read probed retained bytes"))
            for name in (
                "get",
                "acquire_lease",
                "materialize_file",
                "preflight_reference_metadata",
            ):
                unobserved.setattr(FleetContentStore, name, forbidden_read)
            default_api = client.get(f"/api/fleet/runs/{first_id}")
            assert default_api.status_code == 200
            api_original = default_api.json()
            assert api_original["game_tape"]["artifact_observation"] is None
            forbidden_read.assert_not_called()
        available_api = client.get(f"/api/fleet/runs/{first_id}", params=observation_parameters)
        assert available_api.status_code == 200
        api_batch = available_api.json()["game_tape"]["artifact_observation"]
        assert api_batch["journal_revision"] == available_api.json()["journal_revision"]
        assert api_batch["total_artifacts"] == 2 and api_batch["omitted_artifacts"] == 0
        assert [
            {key: value for key, value in item.items() if key != "checked_at"}
            for item in api_batch["observations"]
        ] == [
            {key: value for key, value in item.items() if key != "checked_at"}
            for item in batch["observations"]
        ]
        store = FleetContentStore(
            ApplicationStatePathLayout.from_database(tmp_path / "runtime.sqlite").content_store
        )
        store.release_reference(selected_reference["reference_id"], owner_repository_id=owner_id)
        after_release = json.loads(
            _cli(tmp_path, "plan", "outputs", first_id, *observation_flags, "--json")
        )
        released_batch = after_release["artifact_observation"]
        released_observations = {item["path"]: item for item in released_batch["observations"]}
        assert released_observations[selected_path]["status"] == "UNAVAILABLE"
        assert released_observations[selected_path]["reason"] == "REFERENCE_UNAVAILABLE"
        assert (
            released_observations["ml-output/iris-model-parameters.json"]["status"] == "AVAILABLE"
        )
        released_api = client.get(f"/api/fleet/runs/{first_id}", params=observation_parameters)
        assert released_api.status_code == 200
        assert [
            {key: value for key, value in item.items() if key != "checked_at"}
            for item in released_api.json()["game_tape"]["artifact_observation"]["observations"]
        ] == [
            {key: value for key, value in item.items() if key != "checked_at"}
            for item in released_batch["observations"]
        ]
        unchanged_api = client.get(f"/api/fleet/runs/{first_id}")
        assert unchanged_api.status_code == 200
        assert unchanged_api.json() == api_original
        human_unavailable = _cli(tmp_path, "plan", "outputs", first_id, *observation_flags)
        assert "UNAVAILABLE" in human_unavailable and "REFERENCE_UNAVAILABLE" in human_unavailable
        assert selected_path in human_unavailable
        assert after_release["repositories"] == first_outputs["repositories"]
        assert json.loads(_cli(tmp_path, "plan", "outputs", first_id, "--json")) == first_outputs
        original_status = json.loads(_cli(tmp_path, "plan", "status", first_id, "--json"))
        assert original_status["status"] == "COMPLETED" and original_status["outcome"] == "SUCCESS"
        refused = json.loads(
            _cli(
                tmp_path,
                "plan",
                "export-artifact",
                first_id,
                "--step",
                recorded["step_id"],
                "--path",
                selected_path,
                "--output",
                str(tmp_path / "released-metrics.json"),
                "--json",
                expected=2,
            )
        )
        assert refused["status"] == "error"
        assert not (tmp_path / "released-metrics.json").exists()
    finally:
        client.close()
        api_runtime.shutdown(wait_for_runs=True)
        api_app.dependency_overrides.clear()


@pytest.mark.parametrize("tests_pass", [True, False])
def test_advanced_plan_executes_real_test_build_and_explicit_local_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tests_pass: bool,
) -> None:
    repository = _project(tmp_path, monkeypatch, "advanced/isolated-dataflow", "dev")
    if not tests_pass:
        (repository / "tests/test_intentional_failure.py").write_text(
            "def test_workload_failure():\n    assert False, 'intentional native failure'\n"
        )
    monkeypatch.setenv("RELEASE_TOKEN", "disposable-local-export-permission")
    configuration = _configuration(
        tmp_path,
        repository,
        "advanced/isolated-dataflow",
        "plan",
        {"tag": "trial", "test_timeout_seconds": 30},
        capsys,
    )
    completed = _run(configuration, capsys, expected=0 if tests_pass else 1)
    evidence = completed.operations[0].evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    assert (repository / ".fleet/release-tag.txt").read_text() == "trial\n"
    if tests_pass:
        assert completed.game_tape.status.value == "PASSED"
        assert all(step.status == "COMPLETED" for step in evidence.plan.steps)
        exported = repository / "published/trial-beta"
        assert (exported / "RELEASE_TAG").read_text() == "trial-beta\n"
        assert {item.name for item in exported.glob("*.whl")} == {
            "template_advanced_fixture-0.1.0-py3-none-any.whl"
        }
        for item in (repository / "dist").glob("*.whl"):
            assert (exported / item.name).read_bytes() == item.read_bytes()
        for item in (repository / "dist").glob("*.tar.gz"):
            assert (exported / item.name).read_bytes() == item.read_bytes()
    else:
        step = evidence.plan.steps[1]
        assert isinstance(step, FailedPlanStepExecution)
        assert step.returncode == 1
        assert all(item.status == "SKIPPED" for item in evidence.plan.steps[2:])
        assert not (repository / "dist").exists()
        assert not (repository / "published").exists()
