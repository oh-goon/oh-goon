"""The native command owner keeps its Step group after forced worker loss.

This proves owned process-group containment, not rollback or containment of an
untrusted workload that escapes its declared group/provider boundary.
"""

from __future__ import annotations

import errno
import json
import os
import select
import signal
import socket
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

from oh_goon.core.process_birth import ProcessBirthIdentity, observe_process_birth
from oh_goon.engine import command
from oh_goon.engine.atomic_execution_bytes import atomic_execution_bytes_supported
from oh_goon.engine.command import (
    CommandProcessLifecycleEvent,
    observe_command_processes,
    run_command,
)
from oh_goon.engine.command_process_tree import CommandParentGuard, _terminate_process_group
from oh_goon.runtime import exec_guard_protocol
from oh_goon.runtime.exec_guard_protocol import GUARD_CHANNEL_ENV, GUARD_QUIESCENT
from oh_goon.runtime.execution.run_supervisor import RunExecutionSupervisor, RunWorkerLaunch
from oh_goon.runtime.process_group import (
    wait_for_process_group_quiescence,
    wait_for_process_session_quiescence,
)


def _ready(path: Path) -> dict[str, int]:
    deadline = monotonic() + 20
    while not path.exists():
        if monotonic() >= deadline:
            pytest.fail("native Step did not publish its owned process group")
        Event().wait(0.01)
    result = json.loads(path.read_text())
    assert isinstance(result, dict)
    assert all(type(result.get(key)) is int for key in ("leader", "child", "group"))
    return result


def _cleanup_owned_group(group: int, identities: tuple[ProcessBirthIdentity | None, ...]) -> None:
    # A failing regression must clean up only its exact remaining native group.
    # This is fixture ownership, not a product recovery/cleanup implementation.
    for expected in identities:
        if expected is not None and observe_process_birth(expected.pid) == expected:
            try:
                if os.getpgid(expected.pid) == group:
                    os.killpg(group, signal.SIGKILL)
                    break
            except ProcessLookupError:
                continue
    assert wait_for_process_group_quiescence(group, timeout=5)


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_forced_worker_exit_contains_its_separate_term_resistant_step_group(tmp_path: Path) -> None:
    ready, effect = tmp_path / "ready.json", tmp_path / "visible-effect"
    child = tmp_path / "child.py"
    child.write_text(
        "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)\n"
    )
    step = tmp_path / "step.py"
    step.write_text(
        "import json, os, signal, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "Path(sys.argv[3]).write_text('externally visible before containment')\n"
        "temporary = Path(sys.argv[2] + '.pending')\n"
        "temporary.write_text(json.dumps({'leader': os.getpid(), 'child': child.pid, 'group': os.getpgrp()}))\n"
        "temporary.replace(sys.argv[2])\n"
        "time.sleep(30)\n"
    )
    worker = tmp_path / "worker.py"
    worker.write_text(
        "import sys\n"
        "from oh_goon.engine.command import bind_process_child_custody, run_command\n"
        "bind_process_child_custody()\n"
        "run_command(sys.executable, *sys.argv[1:], timeout=None, "
        "require_process_group_quiescence=True)\n"
    )
    supervisor = RunExecutionSupervisor()
    identities: tuple[ProcessBirthIdentity | None, ...] = ()
    group: int | None = None
    try:
        supervisor.submit(
            "worker-containment",
            RunWorkerLaunch(
                argv=(
                    sys.executable,
                    "-I",
                    str(worker),
                    str(step),
                    str(child),
                    str(ready),
                    str(effect),
                ),
                cwd=tmp_path,
                environment=dict(os.environ),
            ),
        )
        observed = _ready(ready)
        group = observed["group"]
        assert group == observed["leader"]
        assert os.getpgid(observed["child"]) == group
        identities = tuple(observe_process_birth(observed[key]) for key in ("leader", "child"))
        assert all(identity is not None for identity in identities)
        assert supervisor.request_containment(grace_seconds=0) == ("worker-containment",)
        assert supervisor.wait_until_inactive("worker-containment", timeout=10)
        assert wait_for_process_group_quiescence(group, timeout=3), (
            "the worker group exited while its owned native Step group remained executable"
        )
        assert effect.read_text() == "externally visible before containment"
    finally:
        supervisor.request_containment(grace_seconds=0)
        supervisor.wait_until_idle(timeout=10)
        supervisor.shutdown(wait=True)
        if group is not None:
            _cleanup_owned_group(group, identities)


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_guard_does_not_change_workload_pid_group_or_child_wait_semantics() -> None:
    observed_pids: list[int] = []

    def observe(event: CommandProcessLifecycleEvent) -> None:
        if event.kind == "PROCESS_STARTED":
            assert event.pid is not None
            observed_pids.append(event.pid)

    source = (
        "import json, os\n"
        "try:\n"
        "    child = os.waitpid(-1, os.WNOHANG)\n"
        "except ChildProcessError:\n"
        "    child = None\n"
        "print(json.dumps({'pid':os.getpid(),'group':os.getpgrp(),'child':child}))\n"
    )
    result = run_command(
        sys.executable,
        "-I",
        "-c",
        source,
        parent_death_signal=True,
        process_observer=observe,
    )
    snapshot = json.loads(result.stdout)
    assert snapshot["child"] is None, "a native guard must not become the workload's child"
    assert observed_pids == [snapshot["pid"]]
    assert snapshot["group"] == snapshot["pid"]
    assert result.returncode == 0
    assert result.supervisor is not None and result.supervisor.final_process_group_quiescent


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_workload_group_signals_do_not_retire_guard_before_owner_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held, release = Event(), Event()
    captured: dict[str, int] = {}
    original = CommandParentGuard.accept
    ready = tmp_path / "target-ready"

    def accept(guard: CommandParentGuard, process: subprocess.Popen[str]) -> None:
        original(guard, process)
        assert guard.guardian_pidfd is not None
        captured.update(
            target=process.pid,
            target_pidfd=os.pidfd_open(process.pid),
            guardian_pidfd=os.dup(guard.guardian_pidfd),
        )
        held.set()
        assert release.wait(10), "test did not return control to the native command owner"

    monkeypatch.setattr(CommandParentGuard, "accept", accept)
    source = (
        "import signal,time,sys\n"
        "from pathlib import Path\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        "Path(sys.argv[1]).touch()\n"
        "time.sleep(30)\n"
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                run_command,
                sys.executable,
                "-I",
                "-c",
                source,
                str(ready),
                parent_death_signal=True,
            )
            try:
                assert held.wait(10)
                deadline = monotonic() + 10
                while not ready.exists():
                    if monotonic() >= deadline:
                        pytest.fail("native target did not become ready")
                    Event().wait(0.01)
                os.killpg(captured["target"], signal.SIGTERM)
                assert not select.select([captured["guardian_pidfd"]], [], [], 0)[0]
                os.killpg(captured["target"], signal.SIGKILL)
                assert select.select([captured["target_pidfd"]], [], [], 5)[0]
                assert not select.select([captured["guardian_pidfd"]], [], [], 0)[0]
            finally:
                release.set()
            result = future.result(timeout=10)
        assert result.returncode == -signal.SIGKILL
        assert result.supervisor.final_process_group_quiescent
        assert select.select([captured["guardian_pidfd"]], [], [], 0)[0]
        with pytest.raises(ChildProcessError):
            os.waitid(os.P_PIDFD, captured["guardian_pidfd"], os.WEXITED | os.WNOHANG)
    finally:
        release.set()
        for key in ("target_pidfd", "guardian_pidfd"):
            if key in captured:
                os.close(captured[key])


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_guard_has_no_provider_environment_or_input_descriptor_and_is_reaped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "private-input"
    private.write_text("selected-native-input")
    descriptor = os.open(private, os.O_RDONLY | os.O_CLOEXEC)
    guardian_pidfds: list[int] = []
    original = CommandParentGuard.accept

    def accept(guard: CommandParentGuard, process: subprocess.Popen[str]) -> None:
        original(guard, process)
        assert guard.guardian_pid is not None and guard.guardian_pidfd is not None
        root = Path(f"/proc/{guard.guardian_pid}")
        assert b"NATIVE_TEST_PROVIDER_SECRET" not in (root / "environ").read_bytes()
        descriptors = {entry.name: entry.readlink() for entry in (root / "fd").iterdir()}
        assert not {"0", "1", "2"} & descriptors.keys()
        assert private not in descriptors.values()
        assert os.getsid(guard.guardian_pid) == process.pid
        assert os.getpgid(guard.guardian_pid) == guard.guardian_pid
        guardian_pidfds.append(os.dup(guard.guardian_pidfd))

    monkeypatch.setattr(CommandParentGuard, "accept", accept)
    try:
        result = run_command(
            sys.executable,
            "-I",
            "-c",
            "import json,os,sys; print(json.dumps([os.getenv('NATIVE_TEST_PROVIDER_SECRET'),os.read(int(sys.argv[1]),128).decode()]))",
            str(descriptor),
            parent_death_signal=True,
            pass_fds=(descriptor,),
            env={
                "PATH": os.environ.get("PATH", ""),
                "NATIVE_TEST_PROVIDER_SECRET": "caller-selected",
            },
        )
        assert json.loads(result.stdout) == ["caller-selected", "selected-native-input"]
        assert len(guardian_pidfds) == 1 and select.select(guardian_pidfds, [], [], 0)[0]
        with pytest.raises(ChildProcessError):
            os.waitid(os.P_PIDFD, guardian_pidfds[0], os.WEXITED | os.WNOHANG)
    finally:
        os.close(descriptor)
        for pidfd in guardian_pidfds:
            os.close(pidfd)


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_missing_guard_cleanup_ack_keeps_native_exit_and_records_helper_containment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = socket.socket.sendall

    def drop_quiescent_ack(channel: socket.socket, data: bytes, flags: int = 0) -> None:
        if data != GUARD_QUIESCENT:
            original(channel, data, flags)

    monkeypatch.setattr(socket.socket, "sendall", drop_quiescent_ack)
    result = run_command(
        sys.executable,
        "-I",
        "-c",
        "print('work completed'); raise SystemExit(17)",
        parent_death_signal=True,
    )
    assert result.returncode == 17 and result.stdout == "work completed"
    assert result.supervisor.final_process_group_quiescent
    assert result.supervisor.forced_termination
    assert any(
        event.reason_code == "guard_ack_unavailable" for event in result.supervisor.recovery_events
    )


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
@pytest.mark.parametrize("substitution", ("missing-pidfd", "wrong-pidfd"))
def test_guard_startup_rejects_missing_or_substituted_kernel_identity_before_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    substitution: str,
) -> None:
    import oh_goon.engine.command as command

    marker = tmp_path / "must-not-execute"
    fake = tmp_path / "fake-guard.py"
    fake.write_text(
        "import array,os,socket,time\n"
        f"channel=socket.socket(fileno=int(os.environ[{GUARD_CHANNEL_ENV!r}]))\n"
        f"mode={substitution!r}\n"
        "if mode=='missing-pidfd':\n"
        "    channel.send(b'1 123')\n"
        "else:\n"
        "    descriptor=os.pidfd_open(os.getpid())\n"
        "    channel.sendmsg([b'1 '+str(os.getppid()).encode()],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[descriptor]))])\n"
        "    os.close(descriptor)\n"
        "time.sleep(30)\n"
    )
    monkeypatch.setattr(command, "_EXEC_GUARD_PATH", fake)
    with pytest.raises(RuntimeError, match="parent-death guard"):
        run_command(
            sys.executable,
            "-I",
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
            parent_death_signal=True,
        )
    assert not marker.exists()


def test_process_observer_scope_resets_after_failure_and_explicit_observer_wins() -> None:
    inherited: list[CommandProcessLifecycleEvent] = []
    explicit: list[CommandProcessLifecycleEvent] = []
    with pytest.raises(RuntimeError, match="scope ended"):
        with observe_command_processes(inherited.append):
            run_command(sys.executable, "-I", "-c", "pass")
            inherited_cut = tuple(inherited)
            run_command(sys.executable, "-I", "-c", "pass", process_observer=explicit.append)
            assert tuple(inherited) == inherited_cut
            raise RuntimeError("scope ended")
    inherited_cut = tuple(inherited)
    run_command(sys.executable, "-I", "-c", "pass")
    assert tuple(inherited) == inherited_cut
    for events in (inherited, explicit):
        assert [event.kind for event in events] == [
            "PROCESS_STARTING",
            "PROCESS_STARTED",
            "PROCESS_EXITED",
            "DESCENDANTS_QUIESCENT",
        ]


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_cleanup_observation_failure_keeps_primary_work_failure_and_retires_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import oh_goon.engine.command as command

    original_terminate = _terminate_process_group
    original_accept = CommandParentGuard.accept
    guardian_pidfds: list[int] = []

    def accept(guard: CommandParentGuard, process: subprocess.Popen[str]) -> None:
        original_accept(guard, process)
        assert guard.guardian_pidfd is not None
        guardian_pidfds.append(os.dup(guard.guardian_pidfd))

    def cleanup_error(
        process: subprocess.Popen[str],
        *,
        close_pipes: bool = True,
        grace_seconds: float = 1.0,
        parent_guard: CommandParentGuard | None = None,
    ) -> bool:
        original_terminate(
            process,
            close_pipes=close_pipes,
            grace_seconds=grace_seconds,
            parent_guard=parent_guard,
        )
        raise OSError("cleanup observation failed")

    monkeypatch.setattr(CommandParentGuard, "accept", accept)
    monkeypatch.setattr(command, "_terminate_process_group", cleanup_error)
    try:
        with pytest.raises(UnicodeDecodeError) as failure:
            run_command(
                sys.executable,
                "-I",
                "-c",
                "import os,time; os.write(1,b'\\xff'); time.sleep(30)",
                parent_death_signal=True,
            )
        assert any("cleanup" in note for note in failure.value.__notes__)
        assert len(guardian_pidfds) == 1 and select.select(guardian_pidfds, [], [], 0)[0]
        with pytest.raises(ChildProcessError):
            os.waitid(os.P_PIDFD, guardian_pidfds[0], os.WEXITED | os.WNOHANG)
    finally:
        for descriptor in guardian_pidfds:
            os.close(descriptor)


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
@pytest.mark.parametrize("failure_phase", ("process-observer", "stdin-setup"))
def test_startup_failure_retires_the_owned_session_before_reraising(
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    original_accept = CommandParentGuard.accept
    original_blocking = os.set_blocking
    owned: list[tuple[int, int]] = []

    def accept(guard: CommandParentGuard, process: subprocess.Popen[str]) -> None:
        original_accept(guard, process)
        assert guard.guardian_pidfd is not None
        owned.append((process.pid, os.dup(guard.guardian_pidfd)))

    def observe(event: CommandProcessLifecycleEvent) -> None:
        if failure_phase == "process-observer" and event.kind == "PROCESS_STARTED":
            raise ValueError("recording process start failed")

    def set_blocking(descriptor: int, blocking: bool) -> None:
        if failure_phase == "stdin-setup":
            raise ValueError("preparing command input failed")
        original_blocking(descriptor, blocking)

    monkeypatch.setattr(CommandParentGuard, "accept", accept)
    monkeypatch.setattr(os, "set_blocking", set_blocking)
    try:
        with pytest.raises(ValueError, match="failed"):
            run_command(
                sys.executable,
                "-I",
                "-c",
                "import time; time.sleep(30)",
                parent_death_signal=True,
                input_data=b"bounded input",
                process_observer=observe,
            )
        assert len(owned) == 1
        session, descriptor = owned[0]
        assert select.select([descriptor], [], [], 0)[0]
        assert wait_for_process_session_quiescence(session, timeout=1)
        with pytest.raises(ChildProcessError):
            os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG)
    finally:
        for _, descriptor in owned:
            os.close(descriptor)


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
@pytest.mark.parametrize("fault", ("native-errno", "withheld", "malformed", "zero-errno"))
def test_exec_startup_failure_closes_pipes_and_reaps_the_exact_owned_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    baseline = run_command(
        sys.executable, "-I", "-c", "print('native control')", parent_death_signal=True
    )
    assert baseline.returncode == 0 and baseline.stdout == "native control"
    assert baseline.supervisor.final_process_group_quiescent

    marker = tmp_path / "unexecuted-target"
    executable = tmp_path / "invalid-exec"
    executable.write_text("not a native executable format\n")
    executable.chmod(0o700)
    argv: tuple[str, ...] = (str(executable),)
    if fault != "native-errno":
        original_guard = command._EXEC_GUARD_PATH
        fake = tmp_path / "withheld-exec-guard.py"
        packet = {"withheld": b"", "malformed": b"error", "zero-errno": bytes(4)}[fault]
        fake.write_text(
            "import os,runpy,sys,time\n"
            f"sys.path.insert(0, {str(original_guard.parent)!r})\n"
            f"descriptor=int(os.environ[{exec_guard_protocol.EXEC_ERROR_FD_ENV!r}])\n"
            "def interrupted_exec(*args, **kwargs):\n"
            f"    packet={packet!r}\n"
            "    if packet: os.write(descriptor, packet)\n"
            "    time.sleep(30)\n"
            "os.execvpe=interrupted_exec\n"
            f"runpy.run_path({str(original_guard)!r},run_name='__main__')\n"
        )
        monkeypatch.setattr(command, "_EXEC_GUARD_PATH", fake)
        monkeypatch.setattr(exec_guard_protocol, "STARTUP_TIMEOUT_SECONDS", 0.75)
        argv = (
            sys.executable,
            "-I",
            "-c",
            f"from pathlib import Path; Path({str(marker)!r}).touch()",
        )

    owned: list[tuple[subprocess.Popen[str], int, int]] = []
    original_accept = CommandParentGuard.accept

    def accept(guard: CommandParentGuard, process: subprocess.Popen[str]) -> None:
        original_accept(guard, process)
        assert guard.guardian_pidfd is not None
        owned.append((process, os.pidfd_open(process.pid), os.dup(guard.guardian_pidfd)))

    monkeypatch.setattr(CommandParentGuard, "accept", accept)
    started = monotonic()
    try:
        if fault == "native-errno":
            with pytest.raises(OSError) as failure:
                run_command(*argv, input_data=b"owned input", parent_death_signal=True)
            assert failure.value.errno == errno.ENOEXEC
            assert failure.value.filename == str(executable)
        else:
            expected = "confirmation is unavailable" if fault == "withheld" else "error is invalid"
            with pytest.raises(RuntimeError, match=expected):
                run_command(*argv, input_data=b"owned input", parent_death_signal=True)
        assert monotonic() - started < 5
        assert not marker.exists()
        assert len(owned) == 1
        process, leader, guardian = owned[0]
        assert process.returncode is not None
        assert all(
            pipe is None or pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)
        )
        assert select.select([leader, guardian], [], [], 0)[0] == [leader, guardian]
        assert wait_for_process_session_quiescence(process.pid, timeout=1)
        for descriptor in (leader, guardian):
            with pytest.raises(ChildProcessError):
                os.waitid(os.P_PIDFD, descriptor, os.WEXITED | os.WNOHANG)
    finally:
        for _, leader, guardian in owned:
            os.close(leader)
            os.close(guardian)


@pytest.mark.process
@pytest.mark.skipif(not atomic_execution_bytes_supported(), reason="native Linux Run transport")
def test_explicit_false_preserves_workload_after_owner_death_and_retires_only_its_pin(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "owner-ready.json"
    effect = tmp_path / "completed-after-owner-death"
    owner_script = tmp_path / "owner.py"
    owner_script.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "from oh_goon.engine.command import run_command\n"
        "from oh_goon.engine.command_process_tree import CommandParentGuard\n"
        "run_command(sys.executable, '-I', '-c', 'pass', parent_death_signal=True)\n"
        "original=CommandParentGuard.accept\n"
        "captured={}\n"
        "def accept(guard, process):\n"
        "    original(guard,process)\n"
        "    captured.update(leader=process.pid, child=guard.guardian_pid, group=process.pid)\n"
        "CommandParentGuard.accept=accept\n"
        "def observe(event):\n"
        "    if event.kind=='PROCESS_STARTED':\n"
        "        pending=Path(sys.argv[1]+'.pending')\n"
        "        pending.write_text(json.dumps(captured))\n"
        "        pending.replace(sys.argv[1])\n"
        'source="import os,sys; from pathlib import Path; os.read(int(sys.argv[1]),1); Path(sys.argv[2]).touch()"\n'
        "run_command(sys.executable,'-I','-c',source,sys.argv[3],sys.argv[2],\n"
        "    timeout=None,parent_death_signal=False,pass_fds=(int(sys.argv[3]),),process_observer=observe)\n"
    )
    read, write = os.pipe2(os.O_CLOEXEC)
    target_pidfd = guardian_pidfd = None
    process = subprocess.Popen(
        [sys.executable, "-I", str(owner_script), str(ready), str(effect), str(read)],
        pass_fds=(read,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    os.close(read)
    try:
        observed = _ready(ready)
        target_pidfd = os.pidfd_open(observed["leader"])
        guardian_pidfd = os.pidfd_open(observed["child"])
        process.kill()
        assert process.wait(timeout=5) == -signal.SIGKILL
        assert select.select([guardian_pidfd], [], [], 5)[0]
        assert not select.select([target_pidfd], [], [], 0)[0]
        assert not effect.exists()
        os.write(write, b"continue")
        os.close(write)
        write = -1
        assert select.select([target_pidfd], [], [], 5)[0]
        assert effect.exists(), "explicit False must preserve native work after control-owner death"
        assert wait_for_process_session_quiescence(observed["group"], timeout=3)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if write >= 0:
            os.close(write)
        for descriptor in (target_pidfd, guardian_pidfd):
            if descriptor is not None:
                if not select.select([descriptor], [], [], 0)[0]:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    assert select.select([descriptor], [], [], 5)[0]
                try:
                    os.waitid(os.P_PIDFD, descriptor, os.WEXITED)
                except ChildProcessError:
                    pass
                os.close(descriptor)
