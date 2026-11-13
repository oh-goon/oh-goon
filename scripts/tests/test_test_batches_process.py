from __future__ import annotations

import os
import hashlib
import sys
import time
from pathlib import Path

import pytest

from oh_goon.repository_tooling.test_batches import _run_command
from oh_goon.repository_tooling.pytest_batch_orchestration import BatchOutputCapture

pytestmark = pytest.mark.process


def test_batch_command_returns_output_and_status() -> None:
    rc, output, elapsed = _run_command(
        [sys.executable, "-c", "print('batch-ok')"], timeout=5
    )
    assert rc == 0
    assert output.rstrip().endswith("batch-ok")
    assert elapsed < 5


@pytest.mark.parametrize("status", (0, 1))
def test_noisy_batch_retains_bounded_diagnostics_and_native_status(status: int) -> None:
    script = (
        "import os,sys\n"
        "for _ in range(2048): os.write(1, b'x' * 4096)\n"
        "os.write(2, b'\\nDECISIVE_BATCH_DIAGNOSTIC\\n')\n"
        f"sys.exit({status})\n"
    )
    rc, output, _elapsed = _run_command([sys.executable, "-c", script], timeout=20)
    assert rc == status
    assert len(output.encode()) <= 1024 * 1024 + 2048
    assert "DECISIVE_BATCH_DIAGNOSTIC" in output
    assert "Earlier output omitted" in output


def test_batch_capture_hashes_raw_bytes_and_reports_actual_omission() -> None:
    captures: list[BatchOutputCapture] = []
    raw = b"\r\n" * 600000 + b"\nfinal-diagnostic\n"
    rc, output, _elapsed = _run_command(
        [
            sys.executable,
            "-c",
            "import os; os.write(1, b'\\r\\n'*600000 + b'\\nfinal-diagnostic\\n')",
        ],
        timeout=20,
        output_captures=captures,
        members=("case",),
    )
    assert rc == 0
    assert "final-diagnostic" in output
    assert len(captures) == 1
    capture = captures[0]
    assert capture["sha256"] == hashlib.sha256(raw).hexdigest()
    assert capture["byte_count"] == len(raw)
    assert capture["complete"] is True
    # Native universal-newline decoding shrinks this stream. Raw byte length
    # alone cannot establish whether the diagnostic tail actually omitted text.
    assert capture["byte_count"] > capture["limit_bytes"]
    assert capture["truncated"] is False
    assert capture["retained_utf8_bytes"] <= capture["limit_bytes"]


def test_malformed_batch_output_preserves_native_decode_failure() -> None:
    with pytest.raises(UnicodeDecodeError):
        _run_command(
            [sys.executable, "-c", "import os; os.write(1,b'\\xff')"], timeout=5
        )


@pytest.mark.parametrize("passing", (True, False))
def test_noisy_native_pytest_report_preserves_membership_and_result(
    tmp_path: Path, passing: bool
) -> None:
    import json
    import subprocess

    test_file = tmp_path / "test_noisy.py"
    test_file.write_text(
        "import os\n"
        "os.write(1, b'collection-noise' * 100000)\n"
        "def test_noisy():\n"
        "    for _ in range(1024): os.write(1, b'x' * 4096)\n"
        "    print('\\nRESULT: PASSED\\nDECISIVE_NATIVE_OUTCOME', flush=True)\n"
        f"    assert {passing!r}, 'actual native failure'\n"
    )
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(test_file),
            "--qualified",
            "--granularity",
            "tests",
            "--batch-size",
            "1",
            "--workers",
            "1",
            "--batch-timeout",
            "20",
            "--marker",
            "",
            "--pytest-arg=-s",
            "--pytest-arg=-o",
            "--pytest-arg=log_format=PRIVATE_METADATA_CANARY %(message)s",
            "--report-path",
            str(report),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert (result.returncode == 0) is passing, result.stdout[-8192:]
    payload = json.loads(report.read_text())
    assert "PRIVATE_METADATA_CANARY" not in report.read_text()
    assert payload["diagnostics"]["native_pytest_arg_count"] == 3
    assert payload["status"] == ("PASS" if passing else "FAIL")
    membership = payload["membership_receipt"]
    assert membership["terminal_success"] is passing
    assert not membership["missing"] and not membership["duplicates"]
    assert not membership["extras"]
    assert len(membership["passed" if passing else "failed"]) == 1
    capture = payload["batches"][0]["output_captures"][0]
    assert capture["byte_count"] > 4 * 1024 * 1024
    assert capture["retained_utf8_bytes"] <= 1024 * 1024
    assert capture["truncated"] and capture["complete"]
    assert len(capture["sha256"]) == 64
    if not passing:
        assert "DECISIVE_NATIVE_OUTCOME" in result.stdout
        assert "actual native failure" in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="requires native POSIX signal status")
@pytest.mark.parametrize(
    "signal_termination", (True, False), ids=("sigkill", "thread-exhaustion")
)
def test_native_environment_retry_respects_process_exit_authority(
    tmp_path: Path, signal_termination: bool
) -> None:
    import json
    import signal
    import subprocess

    first_attempt = tmp_path / "first-attempt"
    retried = tmp_path / "retried"
    test_file = tmp_path / "test_native_exit.py"
    test_file.write_text(
        "import os, signal\nfrom pathlib import Path\n"
        "def test_native_exit():\n"
        f"    first = Path({str(first_attempt)!r})\n"
        "    if not first.exists():\n"
        "        first.write_text(str(os.getpid()))\n"
        '        print("RuntimeError: can\'t start new thread", flush=True)\n'
        + (
            "        os.kill(os.getpid(), signal.SIGKILL)\n"
            if signal_termination
            else '        raise RuntimeError("can\'t start new thread")\n'
        )
        + f"    Path({str(retried)!r}).write_text(str(os.getpid()))\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(test_file),
            "--qualified",
            "--granularity=tests",
            "--batch-size=1",
            "--workers=1",
            "--batch-timeout=20",
            "--suite-timeout=60",
            "--no-timeout-bisection",
            "--environment-retries=1",
            "--marker=",
            "--pytest-arg=-s",
            "--report-path",
            str(report),
            "--resume-state-root",
            str(tmp_path / "state"),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert first_attempt.exists(), result.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    membership = payload["membership_receipt"]
    selected = [f"{test_file}::test_native_exit"]
    assert payload["source_identity"].startswith("sha256:")
    assert payload["test_plan_identity"].startswith("sha256:")
    assert payload["toolchain_identity"].startswith("sha256:")
    assert not payload["resume_enabled"]
    assert membership["selected"] == selected
    assert membership["attempted"] == selected
    assert not membership["resumed"]
    assert not membership["duplicates"] and not membership["extras"]
    batch = payload["batches"][0]
    if signal_termination:
        assert result.returncode != 0, result.stdout
        assert payload["status"] == "FAIL"
        assert not membership["terminal_success"]
        assert not membership["passed"] and not membership["completed"]
        assert membership["missing"] == selected
        assert not retried.exists()
        assert batch["returncode"] == -signal.SIGKILL
        assert batch["attempt_count"] == 1
        assert batch["environment_retry_count"] == 0
        assert "ENVIRONMENT RETRY" not in result.stdout
        assert "PYTEST BATCH RESULT: PASS" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout
        assert payload["status"] == "PASS" and membership["terminal_success"]
        assert membership["passed"] == membership["completed"] == selected
        assert not membership["missing"]
        assert retried.exists()
        assert batch["returncode"] == 0
        assert batch["attempt_count"] == 2
        assert batch["environment_retry_count"] == 1
        assert "ENVIRONMENT RETRY" in result.stdout


def test_noisy_batch_timeout_keeps_partial_capture_evidence_and_failure() -> None:
    captures: list[BatchOutputCapture] = []
    rc, output, _elapsed = _run_command(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(1,b'x'*2097152); print('waiting',flush=True); time.sleep(30)",
        ],
        timeout=1,
        output_captures=captures,
    )
    assert rc == 124
    assert "BATCH TIMEOUT after 1s" in output
    assert "waiting" in output
    assert len(output.encode()) < 1024 * 1024 + 2048
    assert captures[0]["truncated"] is True
    assert captures[0]["complete"] is False


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
    assert "feedback" not in payload
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


@pytest.mark.parametrize("selected_outcome", ("pass", "fail", "missing", "deselected"))
def test_focused_native_selection_never_claims_unselected_integration_verification(
    selected_outcome: str, tmp_path: Path
) -> None:
    import json
    import subprocess

    selected = tmp_path / "test_selected.py"
    selected.write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('value', (1, 2))\n"
        f"def test_selected(value): assert value != {2 if selected_outcome == 'fail' else 3}\n",
        encoding="utf-8",
    )
    integration = tmp_path / "integration"
    integration.mkdir()
    unselected_effect = tmp_path / "unselected-integration-ran"
    (integration / "test_unselected.py").write_text(
        "from pathlib import Path\n"
        "def test_unselected_integration_failure():\n"
        f"    Path({str(unselected_effect)!r}).touch()\n"
        "    assert False, 'unselected integration deliberately fails'\n",
        encoding="utf-8",
    )
    if selected_outcome == "missing":
        selected.unlink()
    report = tmp_path / "focused.json"
    escalation_effect = tmp_path / "escalation-ran"
    next_check = f"touch {escalation_effect}"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(selected),
            "--focused",
            "--next-check",
            next_check,
            "--granularity",
            "tests",
            "--batch-size",
            "8",
            "--workers",
            "1",
            "--batch-timeout",
            "10",
            "--marker",
            "never_selected" if selected_outcome == "deselected" else "",
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
    assert (result.returncode == 0) is (selected_outcome == "pass"), result.stdout
    assert "FOCUSED PYTEST FEEDBACK: NOT_FULLY_VERIFIED" in result.stdout
    assert "Full verification: NOT_FULLY_VERIFIED" in result.stdout
    assert f"Next check: UNVERIFIED | {json.dumps(next_check)}" in result.stdout
    assert "RESULT: PASSED" not in result.stdout
    assert not unselected_effect.exists()
    assert not escalation_effect.exists(), "the next check is guidance, never an effect"
    if selected_outcome in ("missing", "deselected"):
        assert "PYTEST BATCH RESULT: PASS" not in result.stdout
        assert not report.exists()
        return
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["status"] == ("PASS" if selected_outcome == "pass" else "FAIL")
    assert payload["membership_receipt"]["selected"] == [
        f"{selected}::test_selected[1]",
        f"{selected}::test_selected[2]",
    ]
    assert payload["feedback"] == {
        "scope": "FOCUSED",
        "full_verification_status": "NOT_FULLY_VERIFIED",
        "next_check": {"command": next_check, "status": "UNVERIFIED"},
    }


def _four_worker_launcher(tmp_path: Path) -> str:
    native_proc = tmp_path / "native-proc"
    native_proc.mkdir()
    (native_proc / "meminfo").write_text("MemAvailable: 4194304 kB\n", encoding="utf-8")
    # Exercise actual one/two/four-worker launches independently of the host's
    # quota. Only native capacity observation is controlled; children, receipts,
    # timeouts and collection remain real. Native-limit precedence has unit proof.
    return (
        "import os\nfrom functools import partial\nfrom pathlib import Path\n"
        "from oh_goon.repository_tooling import pytest_batch_orchestration as owner\n"
        "os.sched_getaffinity = lambda _pid: set(range(16))\n"
        "owner.resolve_pytest_resource_budget = partial(\n"
        "    owner.resolve_pytest_resource_budget,\n"
        f"    proc_root=Path({str(native_proc)!r}),\n"
        f"    cgroup_root=Path({str(tmp_path / 'native-cgroup')!r}),\n"
        ")\n"
        "from oh_goon.repository_tooling.test_batches import main\n"
        "raise SystemExit(main())\n"
    )


def test_batch_runner_reuses_exact_member_receipt_and_marks_timing_non_comparable(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    root = Path(__file__).resolve().parents[2]
    test_files = [tmp_path / f"test_resume_{index}.py" for index in range(4)]
    for test_file in test_files:
        test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    expected_members = [str(path) for path in test_files]
    launcher = _four_worker_launcher(tmp_path)
    state = tmp_path / "state"
    report = tmp_path / "report.json"
    command = [
        sys.executable,
        "-c",
        launcher,
        *expected_members,
        "--granularity",
        "files",
        "--batch-size",
        "1",
        "--workers",
        "4",
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
    for budget in (0, 1, 2, 4):
        command[command.index("--resume-state-root") + 1] = str(state / str(budget))
        first = subprocess.run(
            command,
            cwd=root,
            env={**os.environ, "OH_GOON_TEST_CPUS": str(budget)},
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
        assert first_payload["diagnostics"]["resource_budget"]["effective_workers"] == (
            budget or 4
        )
        assert (
            first_payload["diagnostics"]["resource_budget"]["configured_cpus"] == budget
        )
        first_membership = first_payload["membership_receipt"]
        assert first_membership["selected"] == expected_members
        assert first_membership["completed"] == expected_members
        assert first_membership["passed"] == expected_members
        assert first_membership["missing"] == []
        assert first_membership["duplicates"] == []
        assert first_membership["extras"] == []
        assert first_membership["terminal_success"] is True

    first_payload = json.loads(report.read_text(encoding="utf-8"))
    regrouped_command = list(command)
    regrouped_command += ["--focused", "--next-check", "make tooling-check"]
    regrouped_command[
        regrouped_command.index("1", regrouped_command.index("--batch-size"))
    ] = "8"
    regrouped_command[
        regrouped_command.index("10", regrouped_command.index("--batch-timeout"))
    ] = "20"
    second = subprocess.run(
        regrouped_command,
        cwd=root,
        env={**os.environ, "OH_GOON_TEST_CPUS": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert second.returncode == 0, second.stdout
    assert "reusable-members=4/4" in second.stdout
    assert "PYTEST RESUME TIMING" in second.stdout
    assert "reused-members=4" in second.stdout
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["timing_comparable"] is False
    assert payload["reused_member_count"] == 4
    assert payload["executed_member_count"] == 0
    assert payload["feedback"]["full_verification_status"] == "NOT_FULLY_VERIFIED"
    assert payload["resume_suite_identity"] == first_payload["resume_suite_identity"]
    assert (
        payload["execution_plan_identity"] != first_payload["execution_plan_identity"]
    )
    membership = payload["membership_receipt"]
    assert membership["attempted"] == [] and membership["resumed"] == expected_members
    assert membership["completed"] == expected_members
    assert membership["terminal_success"] is True


@pytest.mark.skipif(
    os.name != "posix", reason="process-group cleanup is POSIX-specific"
)
@pytest.mark.parametrize("workers", (1, 4))
def test_long_lived_batch_runner_cleans_completed_pytest_background_groups(
    tmp_path: Path,
    workers: int,
) -> None:
    import subprocess

    root = Path(__file__).resolve().parents[2]
    files: list[str] = []
    for index in range(4):
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
            "-c",
            _four_worker_launcher(tmp_path),
            *files,
            "--granularity",
            "files",
            "--batch-size",
            "1",
            "--workers",
            str(workers),
            "--batch-timeout",
            "5",
            "--suite-timeout",
            "30",
            "--marker",
            "",
        ],
        cwd=root,
        env={**os.environ, "OH_GOON_TEST_CPUS": "0"},
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


@pytest.mark.skipif(
    os.name != "posix", reason="native cancellation uses process groups"
)
def test_four_worker_fail_fast_cancels_active_pytest_without_starting_queued_work(
    tmp_path: Path,
) -> None:
    import json
    import subprocess

    root = Path(__file__).resolve().parents[2]
    files: list[str] = []
    for index in range(5):
        test_file = tmp_path / f"test_fail_fast_{index}.py"
        test_file.write_text(
            "import os,time\nfrom pathlib import Path\n"
            "def test_work():\n"
            f"    root = Path({str(tmp_path)!r})\n"
            f"    (root / 'started-{index}').write_text(str(os.getpid()))\n"
            + (
                "    deadline = time.monotonic() + 10\n"
                "    while len(list(root.glob('started-*'))) < 4:\n"
                "        assert time.monotonic() < deadline, 'four workers did not start'\n"
                "        time.sleep(0.02)\n"
                "    assert False, 'controlled native failure'\n"
                if index == 0
                else "    time.sleep(30)\n"
            ),
            encoding="utf-8",
        )
        files.append(str(test_file))
    report = tmp_path / "fail-fast.json"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _four_worker_launcher(tmp_path),
            *files,
            "--granularity",
            "files",
            "--batch-size",
            "1",
            "--workers",
            "4",
            "--batch-timeout",
            "20",
            "--suite-timeout",
            "25",
            "--fail-fast",
            "--marker",
            "",
            "--report-path",
            str(report),
        ],
        cwd=root,
        env={**os.environ, "OH_GOON_TEST_CPUS": "0"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0, result.stdout
    payload = json.loads(report.read_text())
    assert payload["diagnostics"]["resource_budget"]["effective_workers"] == 4
    assert payload["fail_fast_triggered"] and payload["stop_reason"] == "first_failure"
    assert not payload["suite_timed_out"]
    membership = payload["membership_receipt"]
    assert membership["selected"] == files
    assert membership["attempted"] == files[:4]
    assert files[0] in membership["failed"]
    assert files[4] in membership["missing"]
    assert not membership["terminal_success"]
    assert not membership["duplicates"] and not membership["extras"]
    assert not (tmp_path / "started-4").exists()
    for marker in tmp_path.glob("started-*"):
        proc = Path("/proc") / marker.read_text() / "stat"
        if proc.exists():
            assert proc.read_text().split()[2] == "Z", (
                "cancelled pytest is still running"
            )


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
        "import pytest\n"
        "from pathlib import Path\n"
        f"FIRST_COUNT = Path({str(first_count)!r})\n"
        f"SECOND_ATTEMPT = Path({str(second_attempt)!r})\n"
        "@pytest.mark.no_resume\n"
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
    payload = json.loads(report.read_text(encoding="utf-8"))
    membership = payload["membership_receipt"]
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
    from oh_goon.repository_tooling.daily_regression import native_membership

    assert native_membership(payload)[:2] == (True, True)
    assert len(membership["retry_bisection_lineage"]) == 1
    assert membership["retry_bisection_lineage"][0]["action"] == "bisect_shard"


def _member_deadline_case(tmp_path: Path, *, delay: float = 0.0) -> tuple[Path, Path]:
    test_file = tmp_path / "test_member_deadline.py"
    calls = tmp_path / "calls.txt"
    test_file.write_text(
        "import time\nimport pytest\nfrom pathlib import Path\n"
        f"calls = Path({str(calls)!r})\n"
        "def record(name):\n"
        "    with calls.open('a') as stream: stream.write(name+'\\n')\n"
        "def test_quick_first(): record('first')\n"
        "@pytest.mark.daily\n"
        "def test_slow():\n"
        f"    record('slow'); time.sleep({delay!r})\n"
        "def test_quick_last(): record('last')\n"
    )
    return test_file, calls


def _run_member_deadline_case(
    test_file: Path, report: Path, *options: str
) -> tuple[int, str]:
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "oh_goon.repository_tooling.test_batches",
            str(test_file),
            "--qualified",
            "--granularity=tests",
            "--workers=1",
            "--batch-size=8",
            "--batch-timeout=3",
            "--no-timeout-bisection",
            "--marker=",
            "--report-path",
            str(report),
            *options,
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=35,
        check=False,
    )
    return result.returncode, result.stdout


def test_exact_member_deadline_isolates_slow_native_work_without_changing_membership(
    tmp_path: Path,
) -> None:
    import json

    test_file, calls = _member_deadline_case(tmp_path, delay=3.2)
    report = tmp_path / "report.json"
    rc, output = _run_member_deadline_case(test_file, report)
    assert rc != 0 and "BATCH TIMEOUT after 3s" in output
    assert calls.read_text().splitlines() == ["first", "slow"]
    calls.unlink()
    slow = str(test_file) + "::test_slow"
    rc, output = _run_member_deadline_case(
        test_file, report, "--member-timeout=" + slow + "=8"
    )
    assert rc == 0, output
    assert calls.read_text().splitlines() == ["first", "slow", "last"]
    payload = json.loads(report.read_text())
    members = [
        str(test_file) + "::test_" + name
        for name in ("quick_first", "slow", "quick_last")
    ]
    assert [batch["members"] for batch in payload["batches"]] == [
        [member] for member in members
    ]
    assert [batch["timeout_seconds"] for batch in payload["batches"]] == [3, 8, 3]
    assert payload["membership_receipt"]["selected"] == sorted(members)
    assert payload["membership_receipt"]["passed"] == sorted(members)
    assert payload["membership_receipt"]["terminal_success"] is True
    assert all(
        payload["membership_receipt"][key] == []
        for key in ("missing", "duplicates", "extras")
    )


@pytest.mark.parametrize("fault", ("unknown", "duplicate", "files", "suite-budget"))
def test_invalid_member_deadline_refuses_before_any_native_test_body(
    tmp_path: Path, fault: str
) -> None:
    test_file, calls = _member_deadline_case(tmp_path)
    report = tmp_path / "report.json"
    slow = str(test_file) + "::test_slow"
    valid = "--member-timeout=" + slow + "=8"
    rc, output = _run_member_deadline_case(test_file, report, valid)
    assert rc == 0, output
    assert calls.read_text().splitlines() == ["first", "slow", "last"]
    calls.unlink()
    report.unlink()
    options = {
        "unknown": ("--member-timeout=" + str(test_file) + "::test_missing=8",),
        "duplicate": (valid, valid),
        "files": (valid, "--granularity=files"),
        "suite-budget": (valid, "--suite-timeout=8"),
    }[fault]
    rc, output = _run_member_deadline_case(test_file, report, *options)
    assert rc == 2, output
    assert not calls.exists() and not report.exists()


def test_native_deselection_keeps_member_deadline_inactive_and_adds_no_work(
    tmp_path: Path,
) -> None:
    import json

    test_file, calls = _member_deadline_case(tmp_path)
    report = tmp_path / "report.json"
    slow = str(test_file) + "::test_slow"
    deadline = "--member-timeout=" + slow + "=8"
    rc, output = _run_member_deadline_case(test_file, report, deadline)
    assert rc == 0, output
    assert calls.read_text().splitlines() == ["first", "slow", "last"]
    calls.unlink()
    rc, output = _run_member_deadline_case(
        test_file, report, deadline, "--marker=not daily", "--suite-timeout=6"
    )
    assert rc == 0, output
    assert calls.read_text().splitlines() == ["first", "last"]
    payload = json.loads(report.read_text())
    selected = [
        str(test_file) + "::test_quick_" + suffix for suffix in ("first", "last")
    ]
    assert payload["membership_receipt"]["selected"] == selected
    assert payload["membership_receipt"]["passed"] == selected
    assert payload["native_collection"]["deselected"] == [slow]
    assert payload["batches"][0]["members"] == selected
    assert payload["batches"][0]["timeout_seconds"] == 3
    assert "member_timeouts_seconds" not in payload
    assert all(
        payload["membership_receipt"][key] == []
        for key in ("missing", "duplicates", "extras")
    )


def test_removing_member_deadline_does_not_reuse_its_native_pass_receipts(
    tmp_path: Path,
) -> None:
    import json

    test_file, calls = _member_deadline_case(tmp_path)
    report = tmp_path / "report.json"
    slow = str(test_file) + "::test_slow"
    resume = ("--resume", "--resume-state-root=" + str(tmp_path / "receipts"))
    exception = "--member-timeout=" + slow + "=8"
    rc, output = _run_member_deadline_case(test_file, report, *resume, exception)
    assert rc == 0, output
    first = json.loads(report.read_text())
    rc, output = _run_member_deadline_case(test_file, report, *resume, exception)
    assert rc == 0, output
    reused = json.loads(report.read_text())
    assert reused["reused_member_count"] == 3 and reused["executed_member_count"] == 0
    assert calls.read_text().splitlines() == ["first", "slow", "last"]
    rc, output = _run_member_deadline_case(test_file, report, *resume)
    assert rc == 0, output
    ordinary = json.loads(report.read_text())
    assert (
        ordinary["reused_member_count"] == 0 and ordinary["executed_member_count"] == 3
    )
    assert ordinary["resume_suite_identity"] != first["resume_suite_identity"]
    assert ordinary["execution_plan_identity"] != first["execution_plan_identity"]
    assert calls.read_text().splitlines() == ["first", "slow", "last"] * 2
