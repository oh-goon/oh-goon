"""Native signals reach waited public CLI cancellation and durable Run readback."""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"),
    reason="native Linux process-identity handles required",
)


@pytest.mark.parametrize("termination,expected_exit", [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_waited_public_run_cancels_owned_process_and_reopens_terminal_evidence(
    tmp_path: Path, termination: signal.Signals, expected_exit: int
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    ready, forbidden = tmp_path / "ready.json", tmp_path / "forbidden.txt"
    (repository / "wait.py").write_text(
        "import json, os, sys, time\nfrom pathlib import Path\n"
        "ready = Path(sys.argv[1])\n"
        "temporary = ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid': os.getpid(), 'cwd': os.getcwd()}))\n"
        "temporary.replace(ready)\n"
        "while True:\n    time.sleep(0.1)\n"
    )
    (repository / "later.py").write_text(
        "import sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text('must not run')\n"
    )
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    executable = Path(sys.executable).with_name("oh-goon")

    def cli(*arguments: str) -> str:
        with TemporaryFile(mode="w+") as output, TemporaryFile(mode="w+") as errors:
            result = subprocess.run(
                [str(executable), *arguments],
                cwd=tmp_path,
                env=environment,
                stdout=output,
                stderr=errors,
                timeout=30,
            )
            output.seek(0)
            errors.seek(0)
            content = output.read(65537)
            diagnostic = errors.read(8192)
        assert result.returncode == 0, (content[-8192:], diagnostic)
        assert len(content) <= 65536, "Fixture readback exceeded its expected response bound"
        return content

    cli("league", "add-folder", str(repository))
    workflow = tmp_path / "workflow"
    cli(
        "init",
        "--template",
        "general/orders-taskflow",
        "--kind",
        "goon",
        "--output",
        str(workflow),
        "--repository",
        str(repository),
    )
    package = workflow / "goons/general/wait"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/wait\n\nWait for native cancellation.\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "application": {"path": str(repository)},
                "steps": [
                    {"id": "wait", "run": shlex.join((sys.executable, "wait.py", str(ready)))},
                    {
                        "id": "later",
                        "run": shlex.join((sys.executable, "later.py", str(forbidden))),
                    },
                ],
            },
            sort_keys=False,
        )
    )
    (workflow / "plan.yml").write_text("goons:\n- import: general/wait\n  from: goons\n")
    selection = workflow / "run.yml"
    selected = yaml.safe_load(selection.read_text())
    selected.pop("inputs", None)
    selection.write_text(yaml.safe_dump(selected))
    cli("workflow", "prepare", str(selection))
    child_handle: int | None = None
    with (tmp_path / "stdout").open("w+") as stdout, (tmp_path / "stderr").open("w+") as stderr:
        process = subprocess.Popen(
            [str(executable), "run", str(selection), "--json"],
            cwd=tmp_path,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 30
            while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert ready.is_file(), "Native Step did not reach readiness"
            running: dict[str, Any] = json.loads(ready.read_text())
            child_handle = os.pidfd_open(running["pid"])
            process.send_signal(termination)
            assert process.wait(timeout=30) == expected_exit
            stdout.seek(0)
            receipt_text = stdout.read(65537)
            assert len(receipt_text) <= 65536
            receipt = json.loads(receipt_text)
            assert len(receipt_text.strip().splitlines()) == 1
            assert receipt["status"] == "interrupted"
            assert receipt["cancellation_requested"] is True
            assert receipt["cleanup"] == "not-established-by-interruption"
            run_id = receipt["fleet_run_id"]
            status = json.loads(cli("runs", "show", run_id, "--json"))
            steps = json.loads(cli("runs", "steps", run_id, "--json"))
            handoff = json.loads(cli("runs", "handoff", run_id, "--json"))
            assert status["status"] == "CANCELLED" and status["outcome"] == "CANCELLED"
            assert steps["repositories"][0]["steps"][0]["status"] == "CANCELLED"
            assert steps["repositories"][0]["steps"][1]["status"] == "NOT_RUN"
            assert handoff["status"] == "CANCELLED"
            assert not forbidden.exists()
            assert not Path(running["cwd"]).exists()
            with pytest.raises(ProcessLookupError):
                signal.pidfd_send_signal(child_handle, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if child_handle is not None:
                try:
                    signal.pidfd_send_signal(child_handle, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.close(child_handle)
