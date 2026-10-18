"""Native standard-local qualification using the installed, locked Hashmarks.

No identity monkeypatch, fake worker or subprocess interception is permitted here.
Ordinary test runs skip when native runtime is unavailable; the dedicated host
qualification command refuses an unavailable or mismatched producer as failure.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.product_identity import repository_id

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "standard-atomic"
EXPECTED_HASHMARKS = "0.25.1"  # backend/pyproject.toml and uv.lock, not a substitute


def _require_native_hashmarks() -> None:
    if os.name != "posix":
        pytest.skip("Linux/WSL native process required")
    if importlib.util.find_spec("hashmarks") is None:
        if os.environ.get("OH_GOON_REQUIRE_NATIVE_HASHMARKS") == "1":
            pytest.fail(
                "Native qualification BLOCKED: install the pinned Hashmarks wheel using uv sync --locked before running this suite",
                pytrace=False,
            )
        pytest.skip("Native Hashmarks not installed; not a host-qualification PASS")
    from hashmarks import __version__

    from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime

    assert __version__ == EXPECTED_HASHMARKS, "Installed Hashmarks differs from the project pin"
    assert require_loaded_hashmarks_runtime(), (
        "Installed Hashmarks producer has no authoritative identity"
    )


@pytest.fixture(autouse=True)
def _native_contract():
    _require_native_hashmarks()


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str | None = None) -> Path:
    repo = tmp_path / "repos" / "native"
    repo.parent.mkdir()
    shutil.copytree(EXAMPLE, repo)
    if script is not None:
        (repo / "scripts" / "hello.sh").write_text(script, encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Native Qualification")
    _git(repo, "config", "user.email", "native-test@example.invalid")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "native qualification")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runs.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return repo


def _register(repo: Path) -> None:
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent)
    finally:
        runtime.shutdown(wait_for_runs=True)


def _cli(args: list[str], capsys: pytest.CaptureFixture[str]) -> dict:
    from oh_goon.cli import main

    rc = main(args)
    output = capsys.readouterr().out.strip()
    assert output, f"CLI emitted no JSON (exit={rc})"
    payload = json.loads(output)
    assert isinstance(payload, dict)
    return {"exit": rc, **payload}


def _reopened_tape(run_id: str):
    # Fresh application instance: observations must survive process ownership turnover.
    reopened = ApplicationRuntime.build(standard_local=True)
    try:
        detail = reopened.runs.get(run_id)
        return detail.game_tape.status.value, detail.run.status.value
    finally:
        reopened.shutdown(wait_for_runs=True)


def test_native_cli_success_journal_and_game_tape_survive_restart(tmp_path, monkeypatch, capsys):
    from oh_goon.cli import main

    repo = _repo(tmp_path, monkeypatch)
    # Ordinary authoring needs neither an execution policy nor local/wait flags.
    source = repo / "goon.yml"
    source.write_text("version: 1\nsteps:\n- id: hello\n  run: sh scripts/hello.sh\n")
    assert main(["lock", str(source)]) == 0
    capsys.readouterr()
    _register(repo)
    data = _cli(
        [
            "plan",
            "run",
            str(repo / "goon.yml"),
            "--repository-id",
            repository_id(repo),
            "--json",
        ],
        capsys,
    )
    assert data["exit"] == 0
    assert data["status"] == "COMPLETED"
    assert data["outcome"] == "SUCCESS"
    run_id = data["fleet_run_id"]
    assert _reopened_tape(run_id) == ("PASSED", "COMPLETED")
    tape = _cli(["plan", "tape", run_id, "--json"], capsys)
    assert tape["exit"] == 0
    events = _cli(["plan", "events", run_id, "--json"], capsys)
    assert events["exit"] == 0


def test_native_real_nonzero_exit_durable_failure(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, "#!/bin/sh\necho expected-failure\nexit 17\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent)
        target = runtime.plans.prepare_execution_target(
            repo / "goon.yml", lock_path=repo / "goon.lock"
        )
        admitted = runtime.runs.start_prepared_plan(
            target,
            repository_ids={repository_id(repo)},
            fleet_run_id="native-nonzero",
        )
        terminal = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=30)
        assert terminal.outcome.value != "SUCCESS"
        assert terminal.operations[0].evidence.plan.steps[0].returncode == 17
    finally:
        runtime.shutdown(wait_for_runs=True)
    status, _ = _reopened_tape("native-nonzero")
    assert status != "PASSED"


def test_native_owned_process_cancellation_durable(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, "#!/bin/sh\nsleep 30\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repo.parent)
        target = runtime.plans.prepare_execution_target(
            repo / "goon.yml", lock_path=repo / "goon.lock"
        )
        admitted = runtime.runs.start_prepared_plan(
            target,
            repository_ids={repository_id(repo)},
            fleet_run_id="native-cancel",
        )
        end = time.monotonic() + 15
        while time.monotonic() < end:
            observed = runtime.runs.get(admitted.fleet_run_id).run.status.value
            if observed == "RUNNING":
                break
            time.sleep(0.05)
        assert observed == "RUNNING", "Work never reached the running state"
        runtime.runs.cancel(admitted.fleet_run_id)
        final = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=30)
        assert final.outcome.value != "SUCCESS"
    finally:
        runtime.shutdown(wait_for_runs=True)
    status, _ = _reopened_tape("native-cancel")
    assert status != "PASSED"
