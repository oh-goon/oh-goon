"""Safe catalog workloads use real native project commands and durable Fleet evidence."""

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
from oh_goon.core.models.fleet_observation import DependencyObservationSnapshot
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.product_identity import repository_id
from oh_goon.integrations.hashmarks.dependencies import assess_locked_dependencies
from oh_goon.runtime.league_folders import LeagueFolderStore

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)

_PROJECT_COMMAND = """from __future__ import annotations
import argparse
import subprocess
import tomllib
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    observed = subprocess.run(
        ["git", "rev-parse", "--verify", args.revision + "^{commit}"],
        capture_output=True, text=True,
    )
    if observed.returncode:
        return observed.returncode
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output / "PROJECT_VERSION").write_text(version + "\\n")
    (output / "RELEASE_VERSION").write_text(
        version + "-" + args.channel + "." + observed.stdout.strip()[:7] + "\\n"
    )
    return 0
"""


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    repository = tmp_path / "repositories" / "metadata"
    package = repository / "src" / "sample_metadata"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(_PROJECT_COMMAND)
    (repository / "pyproject.toml").write_text("""[project]
name = "sample-metadata"
version = "0.7.0"
requires-python = ">=3.12"
dependencies = []
[project.scripts]
release-metadata = "sample_metadata:main"
[dependency-groups]
dev = []
[build-system]
requires = ["uv_build>=0.10.0,<0.11.0"]
build-backend = "uv_build"
""")
    locked = subprocess.run(
        ["uv", "lock", "--project", str(repository), "--offline"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert locked.returncode == 0, "native uv could not author the disposable project lock"
    for command in (
        ["git", "init", "-q", str(repository)],
        ["git", "-C", str(repository), "add", "."],
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Sample",
            "-c",
            "user.email=sample@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "native sample",
        ],
    ):
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    LeagueFolderStore().replace((repository.parent,))
    return repository


def _run_selection(
    tmp_path: Path,
    repository: Path,
    template: str,
    inputs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    *,
    expected: int = 0,
) -> FleetRunDetail:
    selection = tmp_path / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "catalog-plan", "name": template},
                "target": {"repository_id": repository_id(repository)},
                "inputs": inputs,
            }
        )
    )
    assert main(["config", "validate", str(selection), "--json"]) == 0
    assert main(["lock", str(selection)]) == 0
    capsys.readouterr()
    assert main(["run", str(selection), "--json"]) == expected
    receipt = json.loads(capsys.readouterr().out)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        return runtime.runs.get(receipt["fleet_run_id"])
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_release_metadata_retains_real_project_outputs_and_native_git_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _project(tmp_path, monkeypatch)
    prepared = subprocess.run(
        ["uv", "sync", "--project", str(repository), "--locked", "--group", "dev"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert prepared.returncode == 0, "native uv could not materialize the selected project group"
    revision = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.strip()
    succeeded = _run_selection(
        tmp_path,
        repository,
        "python/release-metadata",
        {"release_channel": "production", "revision": revision},
        capsys,
    )
    assert succeeded.game_tape.status.value == "PASSED"
    evidence = succeeded.operations[0].evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    assert {item.name for item in evidence.plan.steps[0].outputs} == {
        "PROJECT_VERSION",
        "RELEASE_VERSION",
    }
    assert (repository / ".fleet/release/PROJECT_VERSION").read_text() == "0.7.0\n"
    assert (
        (repository / ".fleet/release/RELEASE_VERSION").read_text().startswith("0.7.0-production.")
    )
    failed = _run_selection(
        tmp_path,
        repository,
        "python/release-metadata",
        {"release_channel": "production", "revision": "0" * 40},
        capsys,
        expected=1,
    )
    evidence = failed.operations[0].evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    assert failed.game_tape.status.value == "ATTENTION_REQUIRED"
    assert failed.run.progress.failed == 1
    step = evidence.plan.steps[0]
    assert isinstance(step, FailedPlanStepExecution)
    assert step.returncode == 128
    assert step.outputs == ()


def test_dependency_inventory_records_real_hashmarks_lock_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = _project(tmp_path, monkeypatch)
    before = (repository / "uv.lock").read_bytes()
    completed = _run_selection(tmp_path, repository, "python/dependency-inventory", {}, capsys)
    assert completed.game_tape.status.value == "PASSED"
    evidence = completed.operations[0].evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    observed = evidence.plan.steps[0].observations[0]
    assert isinstance(observed, DependencyObservationSnapshot)
    assert observed.evidence.availability == "available"
    assert observed.evidence.completeness == "complete"
    assert observed.evidence.freshness == "current"
    assert {source.path for source in observed.sources} == {"uv.lock"}
    assert observed.producer_implementation_identity
    assert (repository / "uv.lock").read_bytes() == before
    assert not (repository / ".venv").exists()
    (repository / "uv.lock").write_bytes(before + b"\n")
    assert assess_locked_dependencies(repository, observed).evidence.freshness == "stale"
