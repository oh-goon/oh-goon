import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from oh_goon.core.models.termination import TerminationSignal
from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandCancelledError,
    CommandOutputLimitExceeded,
    CommandProcessLifecycleEvent,
    CommandTimeoutExpired,
    _terminate_process_group,
    run_command,
)


def test_run_command_supports_working_directory_and_environment(tmp_path: Path) -> None:
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import os, pathlib; print(pathlib.Path.cwd().name, os.environ['OH_GOON_TEST'])",
        cwd=tmp_path,
        env={"OH_GOON_TEST": "ready"},
    )

    assert result.returncode == 0
    assert result.stdout == f"{tmp_path.name} ready"


def test_run_command_reports_process_birth_while_command_is_live() -> None:
    observed: list[CommandProcessLifecycleEvent] = []
    process_started = threading.Event()
    completed = threading.Event()

    def observe(event: CommandProcessLifecycleEvent) -> None:
        observed.append(event)
        if event.kind == "PROCESS_STARTED":
            process_started.set()

    def execute() -> None:
        try:
            run_command(
                sys.executable,
                "-S",
                "-c",
                "import time; time.sleep(0.4)",
                process_observer=observe,
            )
        finally:
            completed.set()

    worker = threading.Thread(target=execute, daemon=True)
    worker.start()
    assert process_started.wait(timeout=2)
    assert not completed.is_set()
    worker.join(timeout=3)
    assert completed.is_set()
    assert [event.kind for event in observed] == [
        "PROCESS_STARTING",
        "PROCESS_STARTED",
        "PROCESS_EXITED",
        "DESCENDANTS_QUIESCENT",
    ]
    assert observed[0].physical_execution_id == observed[1].physical_execution_id
    assert observed[1].pid is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process identity/reaping assertion")
def test_process_started_observer_failure_kills_just_spawned_process() -> None:
    spawned_pid: int | None = None

    def reject_started(event: CommandProcessLifecycleEvent) -> None:
        nonlocal spawned_pid
        if event.kind == "PROCESS_STARTED":
            spawned_pid = event.pid
            raise RuntimeError("durable lifecycle write failed")

    with pytest.raises(RuntimeError, match="durable lifecycle write failed"):
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(30)",
            process_observer=reject_started,
        )

    assert spawned_pid is not None
    assert not Path(f"/proc/{spawned_pid}").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX descriptor inheritance")
def test_run_command_passes_only_explicit_file_descriptors() -> None:
    descriptor = os.memfd_create("oh-goon-test-fd")
    try:
        os.write(descriptor, b"sealed")
        result = run_command(
            sys.executable,
            "-S",
            "-c",
            (
                "import os,sys; fd=int(sys.argv[1]); "
                "os.lseek(fd,0,0); print(os.read(fd,64).decode())"
            ),
            str(descriptor),
            pass_fds=(descriptor,),
        )
    finally:
        os.close(descriptor)

    assert result.returncode == 0
    assert result.stdout == "sealed"


@pytest.mark.slow
@pytest.mark.process
def test_run_command_terminates_process_group_on_cancellation() -> None:
    cancellation_checks = 0

    def cancel_after_supervision_has_started() -> bool:
        nonlocal cancellation_checks
        cancellation_checks += 1
        # The supervisor polls at a bounded cadence. Drive cancellation from that
        # same supervision boundary instead of a scheduler-dependent Timer thread.
        return cancellation_checks >= 3

    started = time.monotonic()
    with pytest.raises(CommandCancelledError, match="cancelled"):
        run_command(
            sys.executable,
            "-c",
            (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(10)']); "
                "time.sleep(10)"
            ),
            timeout=20,
            cancel_requested=cancel_after_supervision_has_started,
        )
    assert cancellation_checks >= 3
    assert time.monotonic() - started < 3


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_parent_death_guard_establishes_owned_process_group_before_supervision(monkeypatch) -> None:
    observed_start_new_session: list[bool] = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        observed_start_new_session.append(bool(kwargs.get("start_new_session")))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    with pytest.raises(CommandCancelledError) as caught:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(0.2)",
            timeout=2,
            cancel_requested=lambda: True,
            parent_death_signal=True,
        )

    assert observed_start_new_session == [True]
    assert caught.value.supervisor.final_process_group_quiescent is True


def test_terminate_process_group_reaps_already_exited_process_pipes() -> None:
    process = subprocess.Popen(
        [sys.executable, "-S", "-c", "print('done')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process.wait(timeout=2)
    assert process.stdout is not None and not process.stdout.closed
    assert process.stderr is not None and not process.stderr.closed

    _terminate_process_group(process)

    assert process.stdout.closed
    assert process.stderr.closed


def test_run_command_streams_output_without_changing_final_result() -> None:
    observed: list[tuple[str, str]] = []

    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr)",
        stream_output=lambda stream, text: observed.append((stream, text)),
    )

    assert result.returncode == 0
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert "".join(text for stream, text in observed if stream == "stdout") == "out\n"
    assert "".join(text for stream, text in observed if stream == "stderr") == "err\n"


@pytest.mark.slow
@pytest.mark.process
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_run_command_cleans_descendant_that_keeps_output_pipe_open(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    # Keep the parent script simple: spawn the resistant child, wait until it has
    # published its pid, then exit without waiting for the child.
    script = """
import pathlib
import subprocess
import sys
import time

pid_file = pathlib.Path(sys.argv[1])
subprocess.Popen(
    [
        sys.executable,
        "-S",
        "-c",
        "import os,signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "open(os.environ['PID_FILE'],'w').write(str(os.getpid())); "
        "time.sleep(30)",
    ],
    env={"PID_FILE": str(pid_file)},
)
deadline = time.time() + 2
while not pid_file.exists() and time.time() < deadline:
    time.sleep(0.01)
if not pid_file.exists():
    raise SystemExit(3)
print("leader-done")
"""

    started = time.monotonic()
    result = run_command(sys.executable, "-S", "-c", script, str(pid_file), timeout=10)
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert result.stdout == "leader-done"
    assert elapsed < 4
    child_pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"descendant process {child_pid} survived command completion")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_run_command_drains_large_stdout_and_stderr_without_deadlock() -> None:
    size = 4 * 1024 * 1024
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import os; os.write(1,b'x'*int(os.environ['N'])); os.write(2,b'y'*int(os.environ['N']))",
        env={"N": str(size)},
        timeout=10,
    )

    assert result.returncode == 0
    assert len(result.stdout) == size
    assert len(result.stderr) == size


def test_run_command_explicit_capture_limit_fails_closed_without_unbounded_retention() -> None:
    limit = 128 * 1024
    argv = (
        sys.executable,
        "-S",
        "-c",
        "import os; os.write(1,b'x'*(4*int(os.environ['LIMIT'])))",
    )
    with pytest.raises(CommandOutputLimitExceeded) as caught:
        run_command(
            *argv,
            env={"LIMIT": str(limit)},
            timeout=10,
            max_capture_bytes_per_stream=limit,
        )

    error = caught.value
    assert error.argv == argv
    assert error.stream == "stdout"
    assert error.limit_bytes == limit
    assert len(error.stdout.encode("utf-8")) <= limit
    assert error.supervisor.cleanup_reason == "output_limit"
    assert error.supervisor.final_process_group_quiescent is True


def test_command_does_not_inherit_control_plane_authority(monkeypatch) -> None:
    authority = {
        "OH_GOON_DB": "authority.sqlite",
        "OH_GOON_EXECUTORS_FILE": "executors.json",
        "OH_GOON_CERTIFICATION_CGROUP_ROOT": "/sys/fs/cgroup/operator",
        "OH_GOON_CERTIFICATION_SYSTEMD_DELEGATED": "1",
        "OH_GOON_CERTIFICATION_SYSTEMD_SUBGROUP": "strict-work",
        "OH_GOON_LEAGUE_FILE": "league.json",
        "OH_GOON_SQLITE_JOURNAL_MODE": "wal",
    }
    for name, value in authority.items():
        monkeypatch.setenv(name, value)

    probe = ";".join(authority)
    result = run_command(
        sys.executable,
        "-c",
        "import os,sys; print(','.join(name for name in sys.argv[1].split(';') if name in os.environ))",
        probe,
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_command_never_exports_process_local_access_capability(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OH_GOON_LOCAL_ACCESS_TOKEN", "control-plane-secret")
    result = run_command(
        sys.executable,
        "-c",
        "import os; print(os.environ.get('OH_GOON_LOCAL_ACCESS_TOKEN', 'absent'))",
    )
    assert result.returncode == 0
    assert result.stdout == "absent"

    explicit = run_command(
        sys.executable,
        "-c",
        "import os; print(os.environ.get('OH_GOON_LOCAL_ACCESS_TOKEN', 'absent'))",
        env={"OH_GOON_LOCAL_ACCESS_TOKEN": "explicit-secret", "PATH": os.environ.get("PATH", "")},
    )
    assert explicit.returncode == 0
    assert explicit.stdout == "absent"


def test_command_never_exports_case_variant_of_process_local_access_capability(
    monkeypatch,
) -> None:
    monkeypatch.setenv("oh_goon_local_access_token", "case-variant-secret")
    result = run_command(
        sys.executable,
        "-c",
        "import os; print(os.environ.get('oh_goon_local_access_token', 'absent'))",
    )
    assert result.returncode == 0
    assert result.stdout == "absent"

    explicit = run_command(
        sys.executable,
        "-c",
        "import os; print(os.environ.get('Oh_Goon_Local_Access_Token', 'absent'))",
        env={"Oh_Goon_Local_Access_Token": "explicit-secret", "PATH": os.environ.get("PATH", "")},
    )
    assert explicit.returncode == 0
    assert explicit.stdout == "absent"


def test_run_command_can_merge_stderr_under_same_process_owner() -> None:
    observed: list[tuple[str, str]] = []
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr)",
        stream_output=lambda stream, text: observed.append((stream, text)),
        merge_stderr=True,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert "out" in result.stdout
    assert "err" in result.stdout
    assert {stream for stream, _ in observed} == {"stdout"}


@pytest.mark.process
def test_live_stdout_marker_cancels_before_child_exits() -> None:
    """A small flushed marker must arrive before EOF; not after timeout."""
    observed: list[str] = []

    def on_output(stream: str, chunk: str) -> None:
        if stream == "stdout":
            observed.append(chunk)

    with pytest.raises(CommandCancelledError) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; print('NATIVE_READY', flush=True); time.sleep(20)",
            timeout=3,
            stream_output=on_output,
            cancel_requested=lambda: "NATIVE_READY" in "".join(observed),
        )
    assert "NATIVE_READY" in "".join(observed)
    assert raised.value.supervisor.cleanup_reason == "cancel"
    assert raised.value.supervisor.final_process_group_quiescent


@pytest.mark.process
def test_live_stdout_marker_revokes_authority_before_timeout() -> None:
    """Trust loss must be observed while the child is still executing."""
    observed: list[str] = []

    def on_output(stream: str, chunk: str) -> None:
        if stream == "stdout":
            observed.append(chunk)

    with pytest.raises(CommandAuthorityTerminatedError) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; print('NATIVE_READY', flush=True); time.sleep(20)",
            timeout=3,
            stream_output=on_output,
            termination_requested=lambda: (
                TerminationSignal(cause="trust_revoke", reason="test-revocation")
                if "NATIVE_READY" in "".join(observed)
                else None
            ),
        )
    assert raised.value.supervisor.cleanup_reason == "authority"
    assert raised.value.supervisor.final_process_group_quiescent


def test_run_command_records_clean_supervisor_receipt() -> None:
    result = run_command(sys.executable, "-S", "-c", "print('ok')")
    assert result.supervisor.leader_exited_before_group_quiescence is False
    assert result.supervisor.process_group_cleanup_requested is False
    assert result.supervisor.cleanup_reason == "none"


@pytest.mark.process
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_strict_quiescence_cleans_descendants_when_leader_has_already_succeeded(
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"
    script = """
import os, pathlib, subprocess, sys, time
pid_file = pathlib.Path(sys.argv[1])
subprocess.Popen([sys.executable, '-S', '-c', "import os,time; open(os.environ['PID_FILE'],'w').write(str(os.getpid())); time.sleep(30)"], env={'PID_FILE': str(pid_file)})
while not pid_file.exists(): time.sleep(0.01)
"""
    started = time.monotonic()
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        script,
        str(pid_file),
        timeout=10,
        require_process_group_quiescence=True,
    )
    assert time.monotonic() - started < 4
    assert result.returncode == 0
    receipt = result.supervisor
    assert receipt.leader_exited_before_group_quiescence is True
    assert receipt.process_group_cleanup_requested is True
    assert receipt.cleanup_reason == "output_cleanup"
    assert len(receipt.recovery_events) == 1
    event = receipt.recovery_events[0]
    assert event.kind == "process_group_cleanup"
    assert event.reason_code == "leader_exited_with_descendants"
    assert event.outcome == "requested"


@pytest.mark.process
@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group semantics")
def test_strict_quiescence_timeout_still_records_deadline_cleanup() -> None:
    with pytest.raises(CommandTimeoutExpired) as caught:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import sys,time; print('partial output',flush=True); print('partial error',file=sys.stderr,flush=True); time.sleep(30)",
            timeout=0.3,
            require_process_group_quiescence=True,
        )
    receipt = caught.value.supervisor
    assert receipt.process_group_cleanup_requested is True
    assert receipt.cleanup_reason == "timeout"
    assert len(receipt.recovery_events) == 1
    assert receipt.recovery_events[0].reason_code == "deadline_expired"
    assert caught.value.duration_ms >= 300
    assert caught.value.physical_execution_id is not None
    assert caught.value.output == "partial output\n"
    assert caught.value.stderr == "partial error\n"
    assert receipt.final_process_group_quiescent


def test_explicit_environment_requires_path_for_bare_executable() -> None:
    with pytest.raises(ValueError, match="bare executable must carry PATH"):
        run_command("git", "--version", env={"LC_ALL": "C"})


def test_explicit_environment_with_path_preserves_bare_executable_lookup() -> None:
    result = run_command(
        "sh",
        "-c",
        "printf tool-path-ready",
        env={"PATH": os.defpath, "LC_ALL": "C"},
    )
    assert result.returncode == 0
    assert result.stdout == "tool-path-ready"


def test_run_command_cancellation_wins_over_concurrent_terminal_observation() -> None:
    requested = threading.Event()
    requested.set()
    with pytest.raises(CommandCancelledError, match="cancelled"):
        run_command(
            sys.executable,
            "-S",
            "-c",
            "pass",
            timeout=2,
            cancel_requested=requested.is_set,
        )


def test_cancellation_returns_terminal_cleanup_and_runtime_authority_evidence() -> None:
    requested = threading.Event()
    requested.set()
    with pytest.raises(CommandCancelledError) as caught:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(30)",
            timeout=5,
            cancel_requested=requested.is_set,
        )
    error = caught.value
    assert error.runtime_authority is not None
    assert error.supervisor.process_group_cleanup_requested is True
    assert error.supervisor.cleanup_reason == "cancel"
    assert error.supervisor.final_process_group_quiescent is True
    assert len(error.supervisor.recovery_events) == 1
    assert error.supervisor.recovery_events[0].reason_code == "cancellation_requested"


def test_live_stream_decoding_preserves_split_unicode_and_newline() -> None:
    """Small UTF-8 fragments cannot corrupt the original command transcript."""
    observed: list[str] = []
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import os,sys,time; fd=sys.stdout.fileno();"
        "os.write(fd,b'caf\\xc3'); time.sleep(0.1);"
        "os.write(fd,b'\\xa9\\r\\n'); time.sleep(0.1);"
        "os.write(fd,b'done\\n')",
        stream_output=lambda stream, value: observed.append(value) if stream == "stdout" else None,
    )
    assert result.returncode == 0
    assert result.stdout == "café\ndone"
    assert "".join(observed).rstrip("\n") == result.stdout


@pytest.mark.parametrize("descriptor", [1, 2], ids=["stdout", "stderr"])
def test_reader_decode_failure_cannot_return_a_successful_prefix(descriptor: int) -> None:
    with pytest.raises(UnicodeDecodeError):
        run_command(
            sys.executable,
            "-S",
            "-c",
            f"import os,time; os.write({descriptor},b'valid prefix\\n'); "
            f"time.sleep(0.1); os.write({descriptor},b'\\xff')",
            timeout=3,
        )
