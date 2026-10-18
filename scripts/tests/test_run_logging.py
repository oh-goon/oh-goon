from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from run_logging_host_tools import logging_host_env, logging_host_tool_names

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_logged.sh"
SETUP = ROOT / "scripts" / "strict_executor_setup.sh"


def test_logged_runner_host_frontdoor_fixture_is_explicit_and_bounded() -> None:
    assert logging_host_tool_names() == ("ln", "mkdir", "mv", "rm")


def test_logged_runner_streams_persists_latest_and_preserves_exit(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    result = subprocess.run(
        (
            "bash",
            str(RUNNER),
            "test",
            "bash",
            "-c",
            "printf 'alpha\\n'; printf 'beta\\n' >&2; exit 7",
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 7
    assert "alpha" in result.stdout and "beta" in result.stdout
    latest = tmp_path / "logs" / "test" / "latest.log"
    assert latest.is_file()
    text = latest.read_text(encoding="utf-8")
    assert "Kind: test" in text
    assert "alpha" in text and "beta" in text
    assert "Completed UTC:" in text
    assert "Duration seconds:" in text
    assert "OH-GOON RUN RESULT: FAIL (exit 7)" in text
    retained = [
        path for path in latest.parent.glob("*.log") if path.name != "latest.log"
    ]
    assert len(retained) == 1
    assert retained[0].read_bytes() == latest.read_bytes()


def test_logged_runner_exposes_output_before_child_completion(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    process = subprocess.Popen(
        (
            "bash",
            str(RUNNER),
            "certify",
            sys.executable,
            "-c",
            "import time; print('child-start', flush=True); time.sleep(4); print('child-end', flush=True)",
        ),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=False,
        bufsize=0,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    observed = bytearray()
    deadline = time.monotonic() + 2.0
    try:
        while time.monotonic() < deadline and b"child-start" not in observed:
            events = selector.select(timeout=max(0.0, deadline - time.monotonic()))
            if not events:
                break
            observed.extend(os.read(process.stdout.fileno(), 65536))
        assert b"child-start" in observed, observed.decode("utf-8", errors="replace")
        assert process.poll() is None
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        selector.close()


def test_logged_runner_exposes_transient_progress_outside_durable_log(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    env["OH_GOON_TEST_PROGRESS"] = "progress-pulse"
    result = subprocess.run(
        (
            "bash",
            str(RUNNER),
            "certify",
            "python3",
            "-c",
            (
                "import os; "
                "pulse = os.environ['OH_GOON_TEST_PROGRESS'].encode() + b'\\n'; "
                "os.write(int(os.environ['OH_GOON_PROGRESS_FD']), pulse); "
                "print('durable-output', flush=True)"
            ),
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "progress-pulse" in result.stdout
    assert "durable-output" in result.stdout
    latest = tmp_path / "logs" / "certify" / "latest.log"
    logged = latest.read_text(encoding="utf-8")
    assert "durable-output" in logged
    assert "progress-pulse" not in logged


def _progress_probe_command() -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        (
            "import time; import oh_goon.cli_runtime_plans as p; "
            "p._progress_heartbeat('[|] Certification working · 00:00:00 · waiting'); "
            "time.sleep(0.02); "
            "p._progress_heartbeat('[/] Certification working · 00:00:05 · backend-tests'); "
            "p._progress_print('Observed activity: Goon 2/14 — oh-goon/backend-tests'); "
            "print('durable-output', flush=True)"
        ),
    )


def test_logged_runner_suppresses_spinner_frames_when_output_is_captured(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONPATH"] = str(ROOT / "backend" / "src")
    result = subprocess.run(
        ("bash", str(RUNNER), "certify", *_progress_probe_command()),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert not any(
        line.startswith(("[|] Certification working", "[/] Certification working"))
        for line in result.stdout.splitlines()
    )
    assert "Observed activity: Goon 2/14 — oh-goon/backend-tests" in result.stdout
    latest = tmp_path / "logs" / "certify" / "latest.log"
    logged = latest.read_text(encoding="utf-8")
    assert not any(
        line.startswith(("[|] Certification working", "[/] Certification working"))
        for line in logged.splitlines()
    )
    assert not any(
        line.startswith("Observed activity:") for line in logged.splitlines()
    )
    assert "durable-output" in logged


def test_logged_runner_uses_one_live_spinner_line_on_a_real_terminal(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    master_fd, slave_fd = os.openpty()
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONPATH"] = str(ROOT / "backend" / "src")
    process = None
    try:
        process = subprocess.Popen(
            ("bash", str(RUNNER), "certify", *_progress_probe_command()),
            cwd=ROOT,
            env=env,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        chunks: list[bytes] = []
        selector = selectors.DefaultSelector()
        selector.register(master_fd, selectors.EVENT_READ)
        deadline = time.monotonic() + 10
        while process.poll() is None or time.monotonic() < deadline:
            events = selector.select(timeout=0.05)
            for key, _mask in events:
                try:
                    chunk = os.read(key.fd, 8192)
                except OSError:
                    chunk = b""
                if chunk:
                    chunks.append(chunk)
            if process.poll() is not None and not events:
                break
        selector.close()
        assert process.wait(timeout=2) == 0
        output = b"".join(chunks).decode("utf-8", errors="replace")
        assert "\r\x1b[2K[|] Certification working · 00:00:00 · waiting" in output
        assert "\r\x1b[2K[/] Certification working · 00:00:05 · backend-tests" in output
        assert "Observed activity: Goon 2/14 — oh-goon/backend-tests" in output
        latest = tmp_path / "logs" / "certify" / "latest.log"
        logged = latest.read_text(encoding="utf-8")
        assert not any(
            line.startswith(("[|] Certification working", "[/] Certification working"))
            for line in logged.splitlines()
        )
        assert not any(
            line.startswith("Observed activity:") for line in logged.splitlines()
        )
        assert "durable-output" in logged
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)


def test_logged_runner_retention_consumes_repository_python_without_ambient_python_authority() -> (
    None
):
    source = RUNNER.read_text(encoding="utf-8")
    assert 'retention_python="$ROOT/backend/.venv/bin/python"' in source
    assert '[[ -x "$retention_python" ]]' in source
    assert "PYTHONPATH=" not in source
    assert "python3 -m oh_goon.runtime.run_log_retention" not in source


def test_logged_runner_cannot_turn_log_directory_failure_into_a_test_blocker(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("occupied", encoding="utf-8")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(blocker)
    result = subprocess.run(
        ("bash", str(RUNNER), "test", "bash", "-c", "printf 'still-ran\\n'; exit 0"),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "still-ran" in result.stdout
    assert "Continuing without persistent command logging" in result.stderr


def test_strict_executor_host_setup_is_explicit_wsl_host_mutation_not_certify_magic() -> (
    None
):
    setup = SETUP.read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "command -v sudo" not in setup
    assert 'sudo loginctl enable-linger "$user_now"' in setup
    assert (
        'sudo systemctl start "user-runtime-dir@${uid_now}.service" "user@${uid_now}.service"'
        in setup
    )
    assert "--property=Delegate=yes" in setup
    certify = makefile.split("certify:", 1)[1].split("\n\n", 1)[0]
    test = makefile.split("test:", 1)[1].split("\n\n", 1)[0]
    assert "strict-executor-setup" not in certify
    assert "sudo" not in certify
    assert "strict-executor-setup" not in test
    assert "sudo" not in test
    assert "strict-executor-setup:" in makefile
    assert "certify-setup:" not in makefile
    assert "scripts/strict_executor_setup.sh" in makefile
    assert "scripts/certify_setup.sh" not in makefile


def test_logged_runner_sanitizes_repo_state_tmp_and_home_paths_without_changing_execution(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "private-state" / "logs")
    private_tmp = "/tmp/tmp.Private123"
    private_home_path = str(Path.home() / ".local/share/uv/python/cpython/bin/python")
    result = subprocess.run(
        (
            "bash",
            str(RUNNER),
            "certify",
            "bash",
            "-c",
            'printf "%s\n" "$PWD/backend" "/tmp/tmp.Private123/work" "$HOME/.local/share/uv/python/cpython/bin/python"',
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert str(ROOT) not in result.stdout
    assert str(tmp_path / "private-state") not in result.stdout
    assert private_tmp not in result.stdout
    assert private_home_path not in result.stdout
    assert "Working directory: <repo>" in result.stdout
    assert "<repo>/backend" in result.stdout
    assert "<tmp>/work" in result.stdout
    assert "<home>/.local/share/uv/python/cpython/bin/python" in result.stdout
    assert "Saved run log: <state>/run-logs/certify/" in result.stdout

    latest = tmp_path / "private-state" / "logs" / "certify" / "latest.log"
    text = latest.read_text(encoding="utf-8")
    assert str(ROOT) not in text
    assert private_tmp not in text
    assert private_home_path not in text
    assert "<repo>/backend" in text
    assert "<tmp>/work" in text
    assert "<home>/.local/share/uv/python/cpython/bin/python" in text


def test_logged_runner_midstream_persistence_failure_never_changes_child_result(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    child = (
        "from pathlib import Path; import os; "
        "d=Path(os.environ['OH_GOON_RUN_LOG_DIR'])/'certify'; "
        "active=next(d.glob('*.log.inprogress')); "
        "active.unlink(); active.mkdir(); "
        "print('live-output', flush=True); raise SystemExit(7)"
    )
    result = subprocess.run(
        ("bash", str(RUNNER), "certify", sys.executable, "-c", child),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 7
    assert "live-output" in result.stdout
    assert "persistent run logging became unavailable during execution" in result.stderr
    assert "logging failure did not change the command result" in result.stderr
    assert "Saved run log:" not in result.stdout


def test_active_run_log_is_not_pruned_before_atomic_completion(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    from oh_goon.runtime.run_log_retention import prune_run_log_directory

    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    gate = tmp_path / "gate"
    os.mkfifo(gate)
    process = subprocess.Popen(
        (
            "bash",
            str(RUNNER),
            "test",
            "bash",
            "-c",
            f"printf 'early-line\\n'; read _ < {gate}; printf 'late-line\\n'",
        ),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    log_dir = tmp_path / "logs" / "test"
    deadline = time.monotonic() + 5
    active: Path | None = None
    while time.monotonic() < deadline:
        candidates = list(log_dir.glob("*.log.inprogress")) if log_dir.exists() else []
        if candidates and "early-line" in candidates[0].read_text(encoding="utf-8"):
            active = candidates[0]
            break
        time.sleep(0.01)
    assert active is not None
    assert list(log_dir.glob("[0-9]*.log")) == []

    os.utime(active, (1, 1))
    for index in range(25):
        completed = log_dir / f"20260102T0000{index % 60:02d}Z-{5000 + index}.log"
        completed.write_text(f"completed-{index}\n", encoding="utf-8")
        os.utime(completed, (index + 2, index + 2))

    result = prune_run_log_directory(log_dir)
    assert active.exists()
    assert active.name not in result.removed_files

    with gate.open("w", encoding="utf-8") as stream:
        stream.write("continue\n")
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr
    assert not active.exists()
    completed = log_dir / active.name.removesuffix(".inprogress")
    assert completed.is_file()
    text = completed.read_text(encoding="utf-8")
    assert "OH-GOON RUN LOG" in text
    assert "early-line" in text
    assert "late-line" in text
    assert "OH-GOON RUN RESULT: PASS (exit 0)" in text
    assert f"Saved run log: <state>/run-logs/test/{completed.name}" in stdout


def test_abandoned_inprogress_run_log_is_never_promoted_by_retention(
    tmp_path: Path,
) -> None:
    from oh_goon.runtime.run_log_retention import prune_run_log_directory

    log_dir = tmp_path / "logs" / "test"
    log_dir.mkdir(parents=True)
    abandoned = log_dir / "20260102T000000Z-4321.log.inprogress"
    abandoned.write_text("partial-run\n", encoding="utf-8")

    result = prune_run_log_directory(log_dir)

    assert result == type(result)((), (), 0)
    assert abandoned.read_text(encoding="utf-8") == "partial-run\n"
    assert not (log_dir / "20260102T000000Z-4321.log").exists()


def test_logged_runner_bounds_completed_run_logs(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("run logging is POSIX shell tooling")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    log_dir = tmp_path / "logs" / "test"
    log_dir.mkdir(parents=True)
    for index in range(30):
        path = log_dir / f"20260101T0000{index % 60:02d}Z-{1000 + index}.log"
        path.write_text(f"old-{index}", encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
    result = subprocess.run(
        ("bash", str(RUNNER), "test", "bash", "-c", "printf 'new-run\\n'"),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    retained = [path for path in log_dir.glob("*.log") if path.name != "latest.log"]
    assert len(retained) <= 20
    assert (log_dir / "latest.log").read_text(encoding="utf-8").find("new-run") >= 0


@pytest.mark.process
def test_logged_runner_sigint_exits_130_and_leaves_no_owned_child(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX signal contract")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    child_pid_file = tmp_path / "child.pid"
    child = (
        "import os,time; "
        f"open({str(child_pid_file)!r},'w').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    process = subprocess.Popen(
        ("bash", str(RUNNER), "verify-p0", sys.executable, "-c", child),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 3
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text())

    try:
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=5) == 130
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail("foreground owned child survived Ctrl+C")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()


@pytest.mark.process
def test_logged_runner_ctrl_c_reaps_nested_workflow_process_group(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-group contract")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONPATH"] = str(ROOT / "backend" / "src")
    grandchild_pid_file = tmp_path / "nested.pid"
    nested = (
        "import subprocess,sys,time; "
        f"p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
        f"open({str(grandchild_pid_file)!r},'w').write(str(p.pid)); time.sleep(30)"
    )
    workflow_probe = (
        "import sys; from oh_goon import workflow; "
        f"rc,_=workflow._stream([sys.executable,'-c',{nested!r}], env=dict(__import__('os').environ)); "
        "raise SystemExit(rc)"
    )
    process = subprocess.Popen(
        ("bash", str(RUNNER), "verify-p0", sys.executable, "-c", workflow_probe),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 4
    while not grandchild_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert grandchild_pid_file.exists()
    grandchild_pid = int(grandchild_pid_file.read_text())

    try:
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=6) == 130
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            stat_path = Path(f"/proc/{grandchild_pid}/stat")
            if not stat_path.exists():
                break
            fields = stat_path.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                break
            time.sleep(0.01)
        else:
            pytest.fail("nested workflow grandchild survived Ctrl+C")
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=2)
        if process.stdout is not None:
            process.stdout.close()


def _cleanup_runner_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    if process.stdout is not None:
        process.stdout.close()


def _wait_for_process_exit(pid: int, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        stat_path = Path(f"/proc/{pid}/stat")
        if not stat_path.exists():
            return
        fields = stat_path.read_text(encoding="utf-8").split()
        if len(fields) > 2 and fields[2] == "Z":
            return
        time.sleep(0.01)
    pytest.fail(f"owned process {pid} survived bounded cancellation")


@pytest.mark.process
def test_logged_runner_ctrl_c_kills_direct_child_that_ignores_int_and_term(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-group contract")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    child_pid_file = tmp_path / "stubborn-direct.pid"
    child = (
        "import os,signal,time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(child_pid_file)!r},'w').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    process = subprocess.Popen(
        ("bash", str(RUNNER), "verify-p0", sys.executable, "-c", child),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 3
    while not child_pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text())

    try:
        os.killpg(process.pid, signal.SIGINT)
        time.sleep(0.05)
        os.kill(process.pid, signal.SIGINT)
        time.sleep(0.05)
        os.kill(process.pid, signal.SIGINT)
        assert process.wait(timeout=6) == 130
        _wait_for_process_exit(child_pid)
    finally:
        _cleanup_runner_process(process)


@pytest.mark.process
def test_ctrl_c_releases_certification_state_for_immediate_reacquisition(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX signal/lease contract")
    env = logging_host_env(tmp_path)
    env["OH_GOON_RUN_LOG_DIR"] = str(tmp_path / "logs")
    env["PYTHONPATH"] = str(ROOT / "backend" / "src")
    state = tmp_path / "cert-state"
    ready = tmp_path / "lease-ready"
    child = (
        "import sys,time; from pathlib import Path; "
        "from oh_goon.services.certification_bootstrap import certification_state_lease; "
        "state=Path(sys.argv[1]); ready=Path(sys.argv[2]); "
        "cm=certification_state_lease(state); cm.__enter__(); "
        "ready.write_text('ready'); time.sleep(30)"
    )
    process = subprocess.Popen(
        (
            "bash",
            str(RUNNER),
            "certify",
            sys.executable,
            "-c",
            child,
            str(state),
            str(ready),
        ),
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    deadline = time.monotonic() + 4
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists()

    try:
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=6) == 130
        from oh_goon.services.certification_bootstrap import certification_state_lease

        with certification_state_lease(state):
            pass
    finally:
        _cleanup_runner_process(process)
