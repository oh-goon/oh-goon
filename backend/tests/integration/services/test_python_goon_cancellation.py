"""Package commands retain the original admitted native cancellation lifecycle."""

from __future__ import annotations

import json
import os
import select
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.services.run_configuration import load_run_configuration

pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


def test_installed_package_cancellation_reaps_native_process_and_removes_owned_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repositories/target"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    (repository / "subject.txt").write_text("target repository")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    LeagueFolderStore().replace((repository,))
    ready, hold = tmp_path / "ready", tmp_path / "hold"
    os.mkfifo(ready)
    os.mkfifo(hold)
    forbidden = tmp_path / "forbidden-marker"
    package = tmp_path / "workflow/goons/general/cancellable"
    implementation = package / "src/cancellable"
    implementation.mkdir(parents=True)
    (implementation / "__init__.py").write_text("""import argparse
import json
import os
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('completed', 'wait', 'forbidden'))
    parser.add_argument('ready', nargs='?')
    parser.add_argument('hold', nargs='?')
    args = parser.parse_args()
    if args.action == 'completed':
        Path('completed.txt').write_text('retained before cancellation')
        return 0
    if args.action == 'wait':
        message = json.dumps({'pid': os.getpid(), 'cwd': str(Path.cwd()),
                              'home': str(Path.home()),
                              'environment': os.environ['UV_PROJECT_ENVIRONMENT']}).encode()
        with Path(args.ready).open('wb', buffering=0) as stream:
            stream.write(message)
        with Path(args.hold).open('rb') as stream:
            stream.read(1)
    Path(args.ready).write_text('must not execute after cancellation')
    return 0
""")
    (package / "README.md").write_text("# general/cancellable\n\nCancel an installed command.\n")
    (package / "pyproject.toml").write_text("""[project]
name = "cancellable"
version = "0.1.0"
requires-python = ">=3.12"
[project.scripts]
cancellable = "cancellable:main"
[build-system]
requires = ["uv_build>=0.10.0,<0.11.0"]
build-backend = "uv_build"
""")
    locked = subprocess.run(
        ["uv", "lock", "--project", str(package), "--offline"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert locked.returncode == 0, locked.stderr
    developer = package / ".venv/keep"
    developer.parent.mkdir()
    developer.write_text("developer environment")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "steps": [
                    {
                        "id": "completed",
                        "run": "cancellable completed",
                        "artifacts": [{"path": "completed.txt", "retention": "evidence"}],
                    },
                    {
                        "id": "wait",
                        "run": f"cancellable wait {shlex.quote(str(ready))} {shlex.quote(str(hold))}",
                    },
                    {
                        "id": "forbidden",
                        "run": f"cancellable forbidden {shlex.quote(str(forbidden))}",
                    },
                ],
            }
        )
    )
    plan = package.parents[2] / "plan.yml"
    plan.write_text("goons:\n- import: general/cancellable\n  from: goons\n")
    selection = plan.with_name("run.yml")
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": repository_id(repository)},
            }
        )
    )
    assert main(["lock", str(selection)]) == 0
    captured = load_run_configuration(selection)
    runtime = ApplicationRuntime.build(standard_local=True)
    started = None
    try:
        runtime.locations.add(repository.parent)
        with os.fdopen(os.open(ready, os.O_RDWR | os.O_NONBLOCK), "rb", buffering=0) as channel:
            started = runtime.plans.run(
                plan,
                repository_ids={repository_id(repository)},
                lock_path=captured.lock_path,
                caller_input_file=captured.authority(),
                external_artifacts=captured.external_artifacts(),
            )
            assert select.select([channel], [], [], 30)[0], runtime.runs.get(started.fleet_run_id)
            running = json.loads(channel.read(4096))
            assert Path(running["cwd"]).is_dir()
            assert Path(running["environment"]).is_dir()
            assert Path(running["cwd"]) not in (package, repository)
            runtime.runs.cancel(started.fleet_run_id)
            cancelled = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=15)
        assert cancelled.run.status.value == "CANCELLED"
        assert cancelled.game_tape.status.value != "PASSED"
        assert not forbidden.exists()
        for name in ("cwd", "home", "environment"):
            assert not Path(running[name]).exists()
        with pytest.raises(ProcessLookupError):
            os.kill(running["pid"], 0)
        assert developer.read_text() == "developer environment"
        assert (repository / "subject.txt").read_text() == "target repository"
        assert not (repository / "completed.txt").exists()
        assert not (package / "completed.txt").exists()
        assert not (repository / "forbidden.txt").exists()
        assert not (package / "forbidden.txt").exists()
    finally:
        if started is not None and not runtime.runs.get(started.fleet_run_id).run.status.terminal:
            runtime.runs.cancel(started.fleet_run_id)
        runtime.shutdown(wait_for_runs=True)
