"""Native development fail-fast preserves full selected membership and cleanup."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from oh_goon.repository_tooling import test_batches

pytestmark = pytest.mark.process


def _run_subject(
    directory: Path,
    *,
    fail_fast: bool,
    workers: int = 1,
    batch_size: int = 2,
    launcher: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    report = directory / "report.json"
    entrypoint = (
        ["-c", launcher] if launcher is not None else ["-m", test_batches.__name__]
    )
    command = [
        sys.executable,
        *entrypoint,
        str(directory / "test_subject.py"),
        "--qualified",
        "--granularity=tests",
        f"--batch-size={batch_size}",
        f"--workers={workers}",
        "--batch-timeout=20",
        "--marker=",
        "--resume-state-root",
        str(directory / "state"),
        "--report-path",
        str(report),
    ]
    if fail_fast:
        command.append("--fail-fast")
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "OH_GOON_TEST_CPUS": str(workers)},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=35,
    )
    assert report.is_file(), result.stdout
    return result, json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fail_fast", "passing"),
    ((True, False), (True, True), (False, False)),
    ids=("development-failure", "development-success", "comprehensive-failure"),
)
def test_native_failure_policy_preserves_selection_and_actual_execution(
    tmp_path: Path, fail_fast: bool, passing: bool
) -> None:
    events = tmp_path / "executed.txt"
    finalized = tmp_path / "finalized.txt"
    subject = tmp_path / "test_subject.py"
    subject.write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        "@pytest.fixture(autouse=True)\n"
        "def cleanup(request):\n"
        "    yield\n"
        f"    with Path({str(finalized)!r}).open('a') as output:\n"
        "        output.write(request.node.name + '\\n')\n"
        "@pytest.mark.parametrize('case', range(4))\n"
        "def test_case(case):\n"
        f"    with Path({str(events)!r}).open('a') as output:\n"
        "        output.write(str(case) + '\\n')\n"
        f"    assert {passing!r} or case not in (0, 2), f'independent failure {{case}}'\n",
        encoding="utf-8",
    )
    result, payload = _run_subject(tmp_path, fail_fast=fail_fast)
    stopped = fail_fast and not passing
    executed = [0] if stopped else list(range(4))
    selected = [f"{subject}::test_case[{case}]" for case in range(4)]
    attempted = [selected[case] for case in executed]
    membership = payload["membership_receipt"]

    assert (result.returncode == 0) is passing, result.stdout
    assert events.read_text().splitlines() == [str(case) for case in executed]
    assert finalized.read_text().splitlines() == [
        f"test_case[{case}]" for case in executed
    ]
    assert membership["selected"] == selected
    assert membership["attempted"] == attempted
    assert membership["completed"] == attempted
    assert membership["failed"] == (
        [] if passing else [selected[0]] if stopped else [selected[0], selected[2]]
    )
    assert membership["missing"] == (selected[1:] if stopped else [])
    assert not membership["duplicates"] and not membership["extras"]
    assert membership["terminal_success"] is passing
    assert payload["status"] == ("PASS" if passing else "FAIL")
    assert payload["executed_member_count"] == len(executed)
    assert payload["fail_fast"] is fail_fast
    assert payload["fail_fast_triggered"] is stopped
    assert payload["stop_reason"] == ("first_failure" if stopped else None)
    assert payload["suite_timed_out"] is False
    assert "PYTEST SUITE TIMEOUT" not in result.stdout
    if not passing:
        assert "independent failure 0" in result.stdout
    if not fail_fast:
        assert "independent failure 2" in result.stdout


@pytest.mark.skipif(
    sys.platform != "linux", reason="native process-tree cleanup uses Linux /proc"
)
def test_fail_fast_cancels_active_worker_and_its_child_before_queued_work(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "ready.fifo"
    os.mkfifo(ready)
    pids = tmp_path / "active.json"
    queued = tmp_path / "queued-ran"
    subject = tmp_path / "test_subject.py"
    subject.write_text(
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "def test_first_failure():\n"
        f"    with Path({str(ready)!r}).open() as incoming:\n"
        "        assert incoming.readline() == 'active\\n'\n"
        "    assert False, 'decisive failure after sibling launch'\n"
        "def test_active_worker():\n"
        "    child = subprocess.Popen([sys.executable, '-c',\n"
        "        \"import signal; print('ready', flush=True); signal.pause()\"],\n"
        "        stdout=subprocess.PIPE, text=True)\n"
        "    assert child.stdout.readline() == 'ready\\n'\n"
        f"    Path({str(pids)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
        f"    with Path({str(ready)!r}).open('w') as outgoing:\n"
        "        outgoing.write('active\\n')\n"
        "    child.wait(timeout=15)\n"
        "def test_queued():\n"
        f"    Path({str(queued)!r}).touch()\n",
        encoding="utf-8",
    )
    native_proc = tmp_path / "native-proc"
    native_proc.mkdir()
    (native_proc / "meminfo").write_text("MemAvailable: 4194304 kB\n", encoding="utf-8")
    # Control only observed worker capacity so the two native workers also run
    # on constrained hosts. Collection, failure, cancellation and children are real.
    launcher = (
        "import os\nfrom functools import partial\nfrom pathlib import Path\n"
        "from oh_goon.repository_tooling import pytest_batch_orchestration as owner\n"
        "os.sched_getaffinity = lambda _pid: {0, 1}\n"
        "owner.resolve_pytest_resource_budget = partial(\n"
        "    owner.resolve_pytest_resource_budget,\n"
        f"    proc_root=Path({str(native_proc)!r}),\n"
        f"    cgroup_root=Path({str(tmp_path / 'native-cgroup')!r}),\n"
        ")\n"
        "from oh_goon.repository_tooling.test_batches import main\n"
        "raise SystemExit(main())\n"
    )
    try:
        result, payload = _run_subject(
            tmp_path, fail_fast=True, workers=2, batch_size=1, launcher=launcher
        )
        assert result.returncode != 0, result.stdout
        assert "decisive failure after sibling launch" in result.stdout
        assert not queued.exists()
        assert payload["fail_fast_triggered"] is True
        assert payload["stop_reason"] == "first_failure"
        assert payload["suite_timed_out"] is False
        assert payload["executed_member_count"] == 2
        assert payload["diagnostics"]["resource_budget"]["effective_workers"] == 2
        membership = payload["membership_receipt"]
        assert membership["failed"] == [f"{subject}::test_first_failure"]
        assert set(membership["missing"]) == {
            f"{subject}::test_active_worker",
            f"{subject}::test_queued",
        }
        assert membership["terminal_success"] is False
        assert any(batch["returncode"] == 130 for batch in payload["batches"])
        for pid in json.loads(pids.read_text()):
            status = Path(f"/proc/{pid}/stat")
            if status.exists():
                assert status.read_text().rsplit(") ", 1)[1].startswith("Z "), (
                    f"fail-fast left native process {pid} running"
                )
            else:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
    finally:
        # A broken cancellation implementation must not leak this test's children.
        if pids.exists():
            for pid in json.loads(pids.read_text()):
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
