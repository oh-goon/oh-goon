from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.services.execution_lock_persistence import parse_execution_lock
from tests.factories.run_configuration import write_run_configuration

_FRESH_CLI = """
import importlib.abc
import sys

if sys.argv[1] == 'without-web-server':
    class WebServerUnavailable(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == 'fastapi' or fullname.startswith('fastapi.'):
                raise ModuleNotFoundError('Web server implementation unavailable', name=fullname)

    sys.meta_path.insert(0, WebServerUnavailable())

if sys.argv[1] == 'without-run-execution':
    class RunExecutionUnavailable(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in {
                'oh_goon.services.fleet_plans',
                'oh_goon.integrations.fleet_plan_native_steps',
            }:
                raise ModuleNotFoundError('Run execution implementation unavailable', name=fullname)

    sys.meta_path.insert(0, RunExecutionUnavailable())

from oh_goon.cli import main
raise SystemExit(main(sys.argv[2:]))
"""


@pytest.mark.process
@pytest.mark.parametrize("command", ["list", "show", "init"])
def test_template_cli_runs_without_unselected_run_execution(tmp_path: Path, command: str) -> None:
    control = tmp_path / "control"
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(control / "application.sqlite"),
        OH_GOON_LEAGUE_FILE=str(control / "league.yml"),
        XDG_CONFIG_HOME=str(control / "config"),
        XDG_DATA_HOME=str(control / "data"),
        XDG_STATE_HOME=str(control / "state"),
    )
    outputs = []
    for mode in ("normal", "without-run-execution"):
        destination = tmp_path / mode
        arguments = (
            ["templates", "list", "--json"]
            if command == "list"
            else ["templates", command, "python/quality", "--kind", "plan"]
        )
        if command == "init":
            arguments.extend(["--output", str(destination)])
        with (
            (tmp_path / f"{mode}.stdout").open("w+b") as output,
            (tmp_path / f"{mode}.stderr").open("w+b") as errors,
        ):
            completed = subprocess.run(
                [sys.executable, "-I", "-c", _FRESH_CLI, mode, *arguments],
                cwd=tmp_path,
                env=environment,
                stdout=output,
                stderr=errors,
                timeout=30,
                check=False,
            )
            output.seek(0)
            stdout = output.read(1_048_577)
            errors.seek(0)
            stderr = errors.read(65_537)
        assert len(stdout) <= 1_048_576 and len(stderr) <= 65_536
        assert completed.returncode == 0, stderr.decode("utf-8", errors="replace")
        if command == "init":
            assert (destination / "run.yml").is_file()
            outputs.append(
                {
                    path.relative_to(destination): path.read_bytes()
                    for path in destination.rglob("*")
                    if path.is_file()
                }
            )
        else:
            outputs.append(json.loads(stdout) if command == "list" else stdout)
        assert not control.exists()
    assert outputs[0] == outputs[1]


@pytest.mark.process
@pytest.mark.parametrize("command", ["inspect", "lock"])
def test_stateless_cli_runs_without_unselected_web_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    workflow = tmp_path / "workflow"
    selected = write_run_configuration(
        workflow, monkeypatch, goon_content="steps:\n- run: echo startup-contract\n"
    )
    working_directory = tmp_path / "unrelated-working-directory"
    working_directory.mkdir()
    control = tmp_path / "control"
    database = control / "application.sqlite"
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(database),
        OH_GOON_LEAGUE_FILE=str(workflow / "league.yml"),
        XDG_CONFIG_HOME=str(control / "config"),
        XDG_DATA_HOME=str(control / "data"),
        XDG_STATE_HOME=str(control / "state"),
    )
    arguments = (
        ("config", "inspect", str(selected), "--json")
        if command == "inspect"
        else ("lock", str(selected))
    )

    def invoke(mode: str) -> bytes:
        with (
            (tmp_path / f"{mode}.stdout").open("w+b") as output,
            (tmp_path / f"{mode}.stderr").open("w+b") as errors,
        ):
            result = subprocess.run(
                [sys.executable, "-I", "-c", _FRESH_CLI, mode, *arguments],
                cwd=working_directory,
                env=environment,
                stdout=output,
                stderr=errors,
                timeout=30,
                check=False,
            )
            output.seek(0)
            stdout = output.read(1_048_577)
            errors.seek(0)
            stderr = errors.read(65_537)
        assert len(stdout) <= 1_048_576 and len(stderr) <= 65_536
        assert result.returncode == 0, stderr.decode("utf-8", errors="replace")
        return stdout

    baseline = invoke("normal")
    lock_path = workflow / "goon.lock"
    if command == "inspect":
        report = json.loads(baseline)
        assert report["status"] == "inputs-valid"
        assert report["proof"]["admission"] == "not-started"
        assert not lock_path.exists()
    else:
        original_lock = lock_path.read_bytes()
        original = parse_execution_lock(original_lock.decode())
        assert f"Execution digest: {original.digest}" in baseline.decode()
    assert not database.exists() and not control.exists()

    without_web_server = invoke("without-web-server")
    if command == "inspect":
        assert json.loads(without_web_server) == report
        assert not lock_path.exists()
    else:
        assert without_web_server == baseline
        assert lock_path.read_bytes() == original_lock
        assert parse_execution_lock(lock_path.read_text()) == original
    assert not database.exists() and not control.exists()
