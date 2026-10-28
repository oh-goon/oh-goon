"""The documented CI helper runs native work and publishes portable Run results."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
APPLICATION = Path("templates/goons/general/orders-taskflow")
RECIPE = Path("examples/python-ci")
PLAN = RECIPE / "plan.yml"
SCRIPT = ROOT / RECIPE / "run.sh"
WHEEL = "airflow_oh_goon_orders-0.1.0-py3-none-any.whl"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None or shutil.which("git") is None,
    reason="native POSIX Git and uv required",
)


@pytest.fixture(scope="module")
def ci_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("ci-python-native-uv-cache")


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()


def _commit(repository: Path) -> str:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Native CI fixture",
        "-c",
        "user.email=ci@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "application and CI recipe",
    )
    return _git(repository, "rev-parse", "HEAD")


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "workload project"
    shutil.copytree(
        ROOT / APPLICATION,
        repository / APPLICATION,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".showcase", "dist", "junit.xml"
        ),
    )
    shutil.copytree(ROOT / RECIPE, repository / RECIPE)
    (repository / ".gitignore").write_text(
        "**/.venv/\n**/__pycache__/\n**/.pytest_cache/\n**/.ruff_cache/\n"
        "**/.showcase/\n**/dist/\n**/junit.xml\n"
    )
    _git(repository, "init", "-q", "-b", "main")
    _commit(repository)
    return repository


def _environment(ci_cache: Path) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment["UV_CACHE_DIR"] = str(ci_cache)
    return environment


def _native(
    argv: list[str],
    *,
    directory: Path,
    environment: dict[str, str],
    log: Path,
    expected: int = 0,
) -> str:
    with log.open("w+b") as output:
        completed = subprocess.run(
            argv,
            cwd=directory,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=180,
        )
        output.seek(0, os.SEEK_END)
        output.seek(max(0, output.tell() - 8192))
        tail = output.read(8192).decode("utf-8", errors="replace")
    assert completed.returncode == expected, (argv, tail)
    return tail


def _run(
    repository: Path,
    state: Path,
    environment: dict[str, str],
    *,
    expected: int = 0,
    plan: Path = PLAN,
) -> str:
    return _native(
        ["bash", str(SCRIPT), "run", str(ROOT), str(repository), str(plan), str(state)],
        directory=state.parent,
        environment=environment,
        log=state.with_suffix(".run.log"),
        expected=expected,
    )


def _collect(state: Path, results: Path, environment: dict[str, str], *, expected: int = 0) -> str:
    return _native(
        ["bash", str(SCRIPT), "collect", str(ROOT), str(state), str(results)],
        directory=results.parent,
        environment=environment,
        log=results.with_suffix(".collect.log"),
        expected=expected,
    )


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def _recorded_results(
    results: Path, commit: str, *, expected_steps: int = 3
) -> tuple[str, list[dict[str, Any]]]:
    receipt = _json(results / "run.json")
    run_id = str(receipt["fleet_run_id"])
    status = _json(results / "status.json")
    tape = _json(results / "tape.json")
    outputs = _json(results / "outputs.json")
    assert all(value["fleet_run_id"] == run_id for value in (status, tape, outputs))
    assert status["status"] == receipt["status"] == "COMPLETED"
    assert status["outcome"] == receipt["outcome"]
    locked = yaml.safe_load((results / "goon.lock").read_text())
    assert {source["commit"] for source in locked["execution_sources"]} == {commit}
    assert any(source["kind"] == "plan" for source in locked["execution_sources"])
    steps: list[dict[str, Any]] = tape["repositories"][0]["steps"]
    assert len(steps) == expected_steps
    assert all(step["project_source"]["application"]["commit"] == commit for step in steps)
    assert (results / "toolchain.json").is_file()
    return run_id, steps


def test_ci_helper_builds_native_wheels_and_keeps_commit_bound_portable_results(
    tmp_path: Path, ci_cache: Path
) -> None:
    repository = _repository(tmp_path)
    environment = _environment(ci_cache)
    original_lock = (repository / APPLICATION / "uv.lock").read_bytes()
    histories = []
    for generation in ("first", "second"):
        commit = _git(repository, "rev-parse", "HEAD")
        state, results = tmp_path / f"state-{generation}", tmp_path / f"results-{generation}"
        _run(repository, state, environment)
        _collect(state, results, environment)
        run_id, steps = _recorded_results(results, commit)
        assert [step["status"] for step in steps] == ["PASSED"] * 3
        assert all(step["exit_code"] == 0 for step in steps)
        report = next(item for item in steps[1]["artifacts"] if item["path"] == "junit.xml")
        assert report["status"] == "PRESENT" and report["byte_count"] > 0
        assert report["retained_object"] is not None
        wheel = next(item for item in steps[2]["artifacts"] if item["path"].endswith(WHEEL))
        exports = _json(results / "exports.json")
        assert {item["artifact_path"] for item in exports} == {
            "ruff.json",
            "junit.xml",
            "dist/" + WHEEL,
        }
        exported = next(item for item in exports if item["artifact_path"].endswith(WHEEL))
        wheel_path = Path(exported["destination"])
        assert wheel_path.is_relative_to(results)
        payload = wheel_path.read_bytes()
        assert wheel["status"] == "PRESENT"
        assert wheel["sha256"] == hashlib.sha256(payload).hexdigest()
        assert wheel["byte_count"] == len(payload)
        assert exported["fleet_run_id"] == run_id
        assert exported["sha256"] == wheel["sha256"]
        assert exported["reference_id"] == wheel["retained_object"]["reference_id"]
        ruff = next(item for item in exports if item["artifact_path"] == "ruff.json")
        assert _json(Path(ruff["destination"])) == []
        junit = next(item for item in exports if item["artifact_path"] == "junit.xml")
        document = ET.parse(junit["destination"])
        assert document.findall(".//testcase")
        assert not document.findall(".//failure") and not document.findall(".//error")
        assert (repository / APPLICATION / "uv.lock").read_bytes() == original_lock
        assert _git(repository, "status", "--porcelain") == ""
        assert not (repository / APPLICATION / ".venv").exists()
        assert not (repository / APPLICATION / "dist").exists()
        histories.append((state, results, commit, run_id))
        if generation == "first":
            implementation = repository / APPLICATION / "src/orders_pipeline/operations.py"
            implementation.write_text(
                implementation.read_text() + "\n# Second CI source revision.\n"
            )
            _commit(repository)
    assert histories[0][2] != histories[1][2]
    assert histories[0][3] != histories[1][3]
    shutil.rmtree(repository)
    historical = tmp_path / "historical-results"
    _collect(histories[0][0], historical, environment)
    assert _json(historical / "run.json") == _json(histories[0][1] / "run.json")
    historical_wheel = next(historical.rglob(WHEEL))
    assert historical_wheel.read_bytes() == next(histories[0][1].rglob(WHEEL)).read_bytes()

    installed = tmp_path / "installed-wheel"
    _native(
        ["uv", "venv", "--python", sys.executable, str(installed)],
        directory=tmp_path,
        environment=environment,
        log=tmp_path / "venv.log",
    )
    _native(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(installed / "bin/python"),
            "--no-deps",
            str(historical_wheel),
        ],
        directory=tmp_path,
        environment=environment,
        log=tmp_path / "install.log",
    )
    output = tmp_path / "native-wheel-run"
    output.mkdir()
    _native(
        [str(installed / "bin/orders-pipeline"), "run", "--fixture", "official"],
        directory=output,
        environment=environment,
        log=tmp_path / "installed-command.log",
    )
    assert _json(output / ".showcase/result.json") == {"total_order_value": "1236.70"}


@pytest.mark.parametrize("failure", ["lint", "test", "locked-metadata"])
def test_ci_helper_preserves_native_failure_and_skips_wheel(
    tmp_path: Path, ci_cache: Path, failure: str
) -> None:
    repository = _repository(tmp_path)
    application = repository / APPLICATION
    if failure == "lint":
        (application / "lint_failure.py").write_text("import pathlib\n")
    elif failure == "test":
        (application / "tests/test_ci_failure.py").write_text(
            "def test_real_ci_failure():\n    assert False, 'intentional native pytest failure'\n"
        )
    else:
        metadata = application / "pyproject.toml"
        metadata.write_text(
            metadata.read_text().replace('requires-python = ">=3.12"', 'requires-python = ">=3.13"')
        )
    commit = _commit(repository)
    original_lock = (application / "uv.lock").read_bytes()
    environment = _environment(ci_cache)
    state, results = tmp_path / "state", tmp_path / "results"
    _run(repository, state, environment, expected=1)
    _collect(state, results, environment)
    _, steps = _recorded_results(results, commit)
    assert [step["status"] for step in steps] == (
        ["PASSED", "FAILED", "SKIPPED"] if failure == "test" else ["FAILED", "SKIPPED", "SKIPPED"]
    )
    failed = steps[1] if failure == "test" else steps[0]
    assert failed["exit_code"] != 0
    if failure == "locked-metadata":
        assert failed["attempts"][0]["uv_preparation"] == {
            "lock_mode": "locked",
            "freshness": "not_established",
        }
    assert steps[2]["artifacts"] == []
    exports = _json(results / "exports.json")
    if failure == "locked-metadata":
        assert exports == []
    else:
        ruff = next(item for item in exports if item["artifact_path"] == "ruff.json")
        diagnostics = _json(Path(ruff["destination"]))
        if failure == "lint":
            assert [item["artifact_path"] for item in exports] == ["ruff.json"]
            assert any(item["code"] == "F401" for item in diagnostics)
        else:
            assert diagnostics == []
            junit = next(item for item in exports if item["artifact_path"] == "junit.xml")
            document = ET.parse(junit["destination"])
            assert document.findall(".//failure")
    assert not list(results.rglob(WHEEL))
    assert (application / "uv.lock").read_bytes() == original_lock
    assert _git(repository, "status", "--porcelain") == ""


def test_ci_collect_refuses_a_pre_run_source_error_without_inventing_history(
    tmp_path: Path, ci_cache: Path
) -> None:
    repository = _repository(tmp_path)
    environment = _environment(ci_cache)
    state, results = tmp_path / "state", tmp_path / "results"
    _run(repository, state, environment, expected=2, plan=RECIPE / "missing.yml")
    assert not (state / "run.json").exists()
    message = _collect(state, results, environment, expected=1)
    assert "No submitted Run" in message
    assert not (results / "tape.json").exists()
    assert not list(results.rglob(WHEEL))


def test_ci_collect_preserves_same_basename_artifacts_from_distinct_steps(
    tmp_path: Path, ci_cache: Path
) -> None:
    repository = _repository(tmp_path)
    plan_path = repository / PLAN
    plan = yaml.safe_load(plan_path.read_text())
    plan["goons"].append({"import": "python/wheel", "from": "./goons", "as": "second-wheel"})
    plan_path.write_text(yaml.safe_dump(plan, sort_keys=False))
    commit = _commit(repository)
    environment = _environment(ci_cache)
    state, results = tmp_path / "state", tmp_path / "results"
    _run(repository, state, environment)
    _collect(state, results, environment)
    run_id, steps = _recorded_results(results, commit, expected_steps=4)
    assert [step["status"] for step in steps] == ["PASSED"] * 4
    wheels = [
        item for item in _json(results / "exports.json") if item["artifact_path"].endswith(WHEEL)
    ]
    assert len(wheels) == 2
    assert len({item["destination"] for item in wheels}) == 2
    assert len({item["step_id"] for item in wheels}) == 2
    for exported in wheels:
        destination = Path(exported["destination"])
        assert destination.is_relative_to(results) and destination.name == WHEEL
        assert exported["fleet_run_id"] == run_id
        artifact = next(
            artifact
            for step in steps
            if step["step_id"] == exported["step_id"]
            for artifact in step["artifacts"]
            if artifact["path"] == exported["artifact_path"]
        )
        payload = destination.read_bytes()
        assert exported["sha256"] == artifact["sha256"] == hashlib.sha256(payload).hexdigest()
        assert exported["byte_count"] == len(payload)
        assert exported["reference_id"] == artifact["retained_object"]["reference_id"]
    assert _json(results / "outputs.json")["schema"] == "oh-goon.plan-run-outputs/v1"


def test_ci_collect_does_not_mistake_diagnostic_reports_for_a_distribution(
    tmp_path: Path, ci_cache: Path
) -> None:
    repository = _repository(tmp_path)
    goon = repository / RECIPE / "goons/python/wheel/goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    missing = "dist/not-produced-0.1.0-py3-none-any.whl"
    declaration["steps"][0]["artifacts"][0]["path"] = missing
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    commit = _commit(repository)
    environment = _environment(ci_cache)
    state, results = tmp_path / "state", tmp_path / "results"
    _run(repository, state, environment)
    failure = _collect(state, results, environment, expected=1)
    assert "no retained distribution" in failure
    _, steps = _recorded_results(results, commit)
    assert [step["status"] for step in steps] == ["PASSED"] * 3
    assert _json(results / "run.json")["outcome"] == "SUCCESS"
    assert steps[2]["artifacts"] == [{"path": missing, "status": "MISSING"}]
    exports = _json(results / "exports.json")
    assert {item["artifact_path"] for item in exports} == {"ruff.json", "junit.xml"}
    assert all(Path(item["destination"]).is_file() for item in exports)
    assert not list(results.rglob("*.whl"))
