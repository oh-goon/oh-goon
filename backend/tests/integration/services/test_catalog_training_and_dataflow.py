"""Real teaching workloads preserve their selected execution and artifact owners."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepExecution,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.league_folders import LeagueFolderStore

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "template-certification" / "fixtures"
IRIS_PROJECT = ROOT / "templates/goons/ml/iris-train"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


def _native(argv: list[str], repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=repository, capture_output=True, text=True, timeout=120)


def _project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: str | Path, group: str | None
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
    if (repository / "uv.lock").is_file():
        before = (repository / "uv.lock").read_bytes()
        prepared = _native(
            [
                "uv",
                "sync",
                "--locked",
                "--no-default-groups",
                *(["--group", group] if group else []),
            ],
            repository,
        )
        assert prepared.returncode == 0, "native uv could not prepare the selected locked fixture"
        assert (repository / "uv.lock").read_bytes() == before
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
    LeagueFolderStore().replace((repository.parent,))
    return repository


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
    repository = _project(tmp_path, monkeypatch, "repository/empty", None)
    configuration = _configuration(tmp_path, repository, "ml/iris-training", "plan", {}, capsys)
    assert main(["run", str(configuration), "--json"]) == 2
    assert not (repository / "ml-output").exists()
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        assert not runtime.runs.history().items
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_iris_atomic_wrapper_refuses_retention_before_training_or_run_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _project(tmp_path, monkeypatch, "repository/empty", None)
    workflow = tmp_path / "workflow"
    assert (
        main(["templates", "init", "ml/iris-train", "--kind", "goon", "--output", str(workflow)])
        == 0
    )
    configuration = workflow / "run.yml"
    selection = yaml.safe_load(configuration.read_text())
    selection["target"]["repository_id"] = repository_id(repository)
    configuration.write_text(yaml.safe_dump(selection), encoding="utf-8")
    assert main(["config", "validate", str(configuration), "--json"]) == 2
    capsys.readouterr()
    assert main(["run", str(configuration), "--json"]) == 2
    assert not (repository / "ml-output").exists()
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        assert not runtime.runs.history().items
    finally:
        runtime.shutdown(wait_for_runs=True)


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
