from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from oh_goon.repository_tooling.test_batches import _run_command

pytestmark = pytest.mark.process


def test_batch_command_returns_output_and_status() -> None:
    rc, output, elapsed = _run_command(
        [sys.executable, "-c", "print('batch-ok')"], timeout=5
    )
    assert rc == 0
    assert output.rstrip().endswith("batch-ok")
    assert elapsed < 5


def test_batch_runner_accepts_pytest_skip_as_terminal_non_failure(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    root = Path(__file__).resolve().parents[2]
    test_file = tmp_path / "test_optional.py"
    report = tmp_path / "batch-report.json"
    test_file.write_text(
        "import pytest\n"
        "def test_ok(): assert True\n"
        "def test_optional(): pytest.skip('platform capability unavailable')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "backend/src/oh_goon/repository_tooling/test_batches.py",
            str(test_file),
            "--granularity",
            "files",
            "--batch-size",
            "1",
            "--workers",
            "1",
            "--batch-timeout",
            "30",
            "--suite-timeout",
            "60",
            "--marker",
            "",
            "--report-path",
            str(report),
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout
    assert "PYTEST MEMBERSHIP FAILURE" not in result.stdout
    assert "PYTEST BATCH RESULT: PASS" in result.stdout
    assert "non-passed=1" in result.stdout
    membership = json.loads(report.read_text(encoding="utf-8"))["membership_receipt"]
    assert membership["terminal_success"] is True
    assert membership["non_passed"] == [str(test_file)]
    assert membership["passed"] == []


@pytest.mark.skipif(
    os.name != "posix", reason="process-group timeout ownership is POSIX-specific"
)
def test_batch_timeout_kills_descendants_holding_output_pipe() -> None:
    script = r"""
import os
import signal
import subprocess
import sys
import time

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
])
print(f"CHILD={child.pid}", flush=True)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
    started = time.monotonic()
    rc, output, elapsed = _run_command([sys.executable, "-c", script], timeout=3)
    wall = time.monotonic() - started

    assert rc == 124
    assert "BATCH TIMEOUT after 3s" in output
    assert "CHILD=" in output
    # One-second timeout + two-second graceful group-termination budget, with margin.
    assert elapsed < 6
    assert wall < 6

    child_pid = int(output.split("CHILD=", 1)[1].splitlines()[0])
    proc_stat = Path(f"/proc/{child_pid}/stat")
    deadline = time.monotonic() + 2
    while proc_stat.exists() and time.monotonic() < deadline:
        try:
            state = proc_stat.read_text().split()[2]
        except (FileNotFoundError, IndexError):
            break
        if state == "Z":
            break
        time.sleep(0.05)
    if proc_stat.exists():
        state = proc_stat.read_text().split()[2]
        assert state == "Z", "timed-out pytest descendant is still running"


@pytest.mark.skipif(
    os.name != "posix", reason="process-group completion ownership is POSIX-specific"
)
def test_batch_completion_cleans_descendant_after_direct_parent_already_exited() -> (
    None
):
    script = r"""
import subprocess
import sys

child = subprocess.Popen([
    sys.executable,
    "-c",
    "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
])
print(f"ORPHAN_CHILD={child.pid}", flush=True)
# Exit immediately. The descendant inherits stdout and keeps the pipe open.
"""
    started = time.monotonic()
    rc, output, elapsed = _run_command([sys.executable, "-c", script], timeout=5)
    wall = time.monotonic() - started

    assert rc == 0
    assert "BATCH TIMEOUT" not in output
    assert "ORPHAN_CHILD=" in output
    assert elapsed < 4
    assert wall < 4

    child_pid = int(output.split("ORPHAN_CHILD=", 1)[1].splitlines()[0])
    proc_stat = Path(f"/proc/{child_pid}/stat")
    deadline = time.monotonic() + 2
    while proc_stat.exists() and time.monotonic() < deadline:
        try:
            state = proc_stat.read_text().split()[2]
        except (FileNotFoundError, IndexError):
            break
        if state == "Z":
            break
        time.sleep(0.05)
    if proc_stat.exists():
        assert proc_stat.read_text().split()[2] == "Z"


@pytest.mark.skipif(
    os.name != "posix", reason="suite wall-clock timeout is POSIX-tested"
)
def test_suite_timeout_bounds_complete_batch_run(tmp_path: Path) -> None:
    import subprocess

    test_file = tmp_path / "test_suite_budget.py"
    test_file.write_text(
        "import time\ndef test_one(): time.sleep(1.5)\ndef test_two(): time.sleep(30)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "backend/src/oh_goon/repository_tooling/test_batches.py",
            str(test_file),
            "--granularity",
            "tests",
            "--batch-size",
            "1",
            "--workers",
            "1",
            "--batch-timeout",
            "2",
            "--suite-timeout",
            "3",
            "--marker",
            "",
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=10,
    )
    wall = time.monotonic() - started
    assert result.returncode != 0
    assert "PYTEST SUITE TIMEOUT" in result.stdout
    assert wall < 8


def test_batch_child_does_not_inherit_progress_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setenv("OH_GOON_PROGRESS_FD", str(write_fd))
        rc, output, _elapsed = _run_command(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('OH_GOON_PROGRESS_FD', 'missing'))",
            ],
            timeout=5,
        )
        assert rc == 0
        assert output.rstrip().endswith("missing")
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_batch_execution_report_is_atomic_and_records_timeout_refinement(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    test_file = tmp_path / "test_report.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    report = tmp_path / "reports" / "batch.json"
    result = subprocess.run(
        [
            sys.executable,
            "backend/src/oh_goon/repository_tooling/test_batches.py",
            str(test_file),
            "--batch-size",
            "1",
            "--workers",
            "1",
            "--batch-timeout",
            "10",
            "--marker",
            "",
            "--report-path",
            str(report),
            "--resume-state-root",
            str(tmp_path / "state"),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["schema"] == "oh-goon.pytest-batch-execution/v1"
    assert payload["status"] == "PASS"
    assert payload["batches"][0]["members"] == [str(test_file)]
    assert payload["batches"][0]["environment_retry_count"] == 0
    assert payload["batches"][0]["recovery_events"] == []
    membership = payload["membership_receipt"]
    assert membership["schema"] == "oh-goon.pytest-executed-membership-receipt/v1"
    assert membership["selected"] == [str(test_file)]
    assert membership["attempted"] == [str(test_file)]
    assert membership["completed"] == [str(test_file)]
    assert membership["missing"] == []
    assert membership["duplicates"] == []
    assert membership["extras"] == []
    assert membership["terminal_success"] is True
    assert (
        membership["terminal_membership_identity"]
        == membership["selected_membership_identity"]
    )
    assert Path(payload["membership_receipt_path"]).exists()
    assert not list(report.parent.glob("*.tmp"))


def test_batch_runner_reuses_exact_member_receipt_and_marks_timing_non_comparable(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    root = Path(__file__).resolve().parents[2]
    test_file = tmp_path / "test_resume.py"
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    state = tmp_path / "state"
    report = tmp_path / "report.json"
    command = [
        sys.executable,
        "backend/src/oh_goon/repository_tooling/test_batches.py",
        str(test_file),
        "--granularity",
        "files",
        "--batch-size",
        "1",
        "--workers",
        "1",
        "--batch-timeout",
        "10",
        "--marker",
        "",
        "--resume",
        "--resume-key",
        "unit-resume",
        "--resume-state-root",
        str(state),
        "--report-path",
        str(report),
    ]
    first = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert first.returncode == 0, first.stdout
    assert "reused-members=0" in first.stdout
    first_payload = json.loads(report.read_text(encoding="utf-8"))
    assert first_payload["timing_comparable"] is True

    regrouped_command = list(command)
    regrouped_command[
        regrouped_command.index("1", regrouped_command.index("--batch-size"))
    ] = "8"
    regrouped_command[
        regrouped_command.index("10", regrouped_command.index("--batch-timeout"))
    ] = "20"
    second = subprocess.run(
        regrouped_command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert second.returncode == 0, second.stdout
    assert "reusable-members=1/1" in second.stdout
    assert "PYTEST RESUME TIMING" in second.stdout
    assert "reused-members=1" in second.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["timing_comparable"] is False
    assert payload["reused_member_count"] == 1
    assert payload["executed_member_count"] == 0
    assert payload["resume_suite_identity"] == first_payload["resume_suite_identity"]
    assert (
        payload["execution_plan_identity"] != first_payload["execution_plan_identity"]
    )
    membership = payload["membership_receipt"]
    assert membership["attempted"] == [] and membership["resumed"] == [str(test_file)]
    assert membership["completed"] == [str(test_file)]
    assert membership["terminal_success"] is True


@pytest.mark.skipif(
    os.name != "posix", reason="process-group cleanup is POSIX-specific"
)
def test_long_lived_batch_runner_cleans_completed_pytest_background_groups(
    tmp_path: Path,
) -> None:
    import subprocess

    root = Path(__file__).resolve().parents[2]
    files: list[str] = []
    for index in range(3):
        test_file = tmp_path / f"test_background_{index}.py"
        test_file.write_text(
            "import subprocess, sys\n"
            "def test_ok():\n"
            "    subprocess.Popen([sys.executable, '-S', '-c', 'import time; time.sleep(60)'])\n"
            "    assert True\n",
            encoding="utf-8",
        )
        files.append(str(test_file))

    started = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "backend/src/oh_goon/repository_tooling/test_batches.py",
            *files,
            "--granularity",
            "files",
            "--batch-size",
            "1",
            "--workers",
            "1",
            "--batch-timeout",
            "5",
            "--suite-timeout",
            "30",
            "--marker",
            "",
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stdout
    assert "PYTEST BATCH RESULT: PASS" in result.stdout
    assert "BATCH TIMEOUT" not in result.stdout
    assert elapsed < 25


def test_repeated_batch_commands_leave_no_reader_threads_or_fd_growth() -> None:
    import threading

    fd_root = Path("/proc/self/fd")
    before_fds = len(tuple(fd_root.iterdir())) if fd_root.is_dir() else None
    for index in range(20):
        rc, output, elapsed = _run_command(
            [sys.executable, "-S", "-c", f"print('iteration-{index}')"], timeout=5
        )
        assert rc == 0
        assert output.rstrip().endswith(f"iteration-{index}")
        assert elapsed < 5
    leaked_readers = [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("oh-goon-stdout")
        or thread.name.startswith("oh-goon-stderr")
    ]
    assert leaked_readers == []
    if before_fds is not None:
        after_fds = len(tuple(fd_root.iterdir()))
        assert after_fds <= before_fds + 1


@pytest.mark.skipif(
    os.name != "posix", reason="timeout refinement uses POSIX process groups"
)
def test_timeout_bisection_reuses_completed_member_without_duplicate_coverage(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    root = Path(__file__).resolve().parents[2]
    test_file = tmp_path / "test_refinement_membership.py"
    first_count = tmp_path / "first-count.txt"
    second_attempt = tmp_path / "second-attempt.txt"
    test_file.write_text(
        "import time\n"
        "from pathlib import Path\n"
        f"FIRST_COUNT = Path({str(first_count)!r})\n"
        f"SECOND_ATTEMPT = Path({str(second_attempt)!r})\n"
        "def test_first():\n"
        "    count = int(FIRST_COUNT.read_text() or '0') if FIRST_COUNT.exists() else 0\n"
        "    FIRST_COUNT.write_text(str(count + 1))\n"
        "def test_second():\n"
        "    if not SECOND_ATTEMPT.exists():\n"
        "        SECOND_ATTEMPT.write_text('coarse-attempt-started')\n"
        "        time.sleep(30)\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "backend/src/oh_goon/repository_tooling/test_batches.py",
            str(test_file),
            "--granularity",
            "tests",
            "--batch-size",
            "2",
            "--workers",
            "1",
            "--batch-timeout",
            "8",
            "--suite-timeout",
            "24",
            "--marker",
            "",
            "--resume-state-root",
            str(tmp_path / "state"),
            "--report-path",
            str(report),
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=35,
    )
    assert result.returncode == 0, result.stdout
    assert first_count.read_text(encoding="utf-8") == "1"
    membership = json.loads(report.read_text(encoding="utf-8"))["membership_receipt"]
    assert len(membership["selected"]) == 2
    assert membership["completed"] == membership["selected"]
    assert membership["missing"] == []
    assert membership["duplicates"] == []
    assert membership["extras"] == []
    assert membership["terminal_success"] is True
    attempts_by_member: dict[str, list[str]] = {
        member: [] for member in membership["selected"]
    }
    for row in membership["attempt_records"]:
        attempts_by_member[row["member"]].append(row["status"])
    first_member, second_member = membership["selected"]
    assert attempts_by_member[first_member] == ["PASSED"]
    assert sorted(attempts_by_member[second_member]) == ["PASSED", "STARTED"]
    assert len(membership["retry_bisection_lineage"]) == 1
    assert membership["retry_bisection_lineage"][0]["action"] == "bisect_shard"
