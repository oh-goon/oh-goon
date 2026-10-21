from __future__ import annotations

import errno
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.engine.command import (
    MAX_COMMAND_INPUT_BYTES,
    CommandCancelledError,
    CommandOutputLimitExceeded,
    CommandProcessLifecycleEvent,
    CommandTerminal,
    CommandTimeoutExpired,
    run_command,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX pseudo-terminal transport")


def test_terminal_has_private_controlling_tty_and_initial_size() -> None:
    output: list[bytes] = []
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import os,fcntl,termios,struct; "
        "print([os.isatty(n) for n in (0,1,2)]); "
        "fd=os.open('/dev/tty',os.O_RDWR); os.close(fd); "
        "print(os.tcgetpgrp(0)==os.getpgrp()); "
        "print(struct.unpack('HHHH',fcntl.ioctl(0,termios.TIOCGWINSZ,b'0'*8))[:2])",
        terminal=CommandTerminal(lambda maximum: None, output.append, initial_size=(31, 101)),
        timeout=3,
    )
    assert result.returncode == 0
    assert b"[True, True, True]\r\nTrue\r\n(31, 101)\r\n" == b"".join(output)
    assert result.stderr == ""
    assert result.physical_execution_id is not None
    assert result.runtime_authority is not None
    assert result.supervisor.final_process_group_quiescent


def test_terminal_preserves_raw_input_output_and_resize() -> None:
    output: list[bytes] = []
    sent = False

    def read_input(maximum: int) -> bytes | None:
        nonlocal sent
        if not sent and b"READY" in b"".join(output):
            sent = True
            assert maximum >= 4
            return b"\x00\xff\r\x1b"
        return None

    def current_size() -> tuple[int, int] | None:
        return (42, 109) if b"READY" in b"".join(output) else None

    script = """import os, tty, fcntl, termios, struct
tty.setraw(0)
os.write(1, b'READY')
data = b''
while len(data) < 4:
    data += os.read(0, 4 - len(data))
size = struct.unpack('HHHH', fcntl.ioctl(0, termios.TIOCGWINSZ, b'0'*8))[:2]
os.write(1, b'\\x1b[31m' + data + b'\\r' + str(size).encode())
"""
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        script,
        terminal=CommandTerminal(read_input, output.append, current_size=current_size),
        timeout=3,
    )
    assert result.returncode == 0
    assert b"".join(output) == b"READY\x1b[31m\x00\xff\r\x1b\r(42, 109)"
    assert result.stdout_evidence is not None
    assert result.stdout_evidence.sha256 == hashlib.sha256(b"".join(output)).hexdigest()
    assert result.stdout_evidence.byte_count == len(b"".join(output))
    assert result.stdout_evidence.line_count == len(
        b"".join(output).decode("utf-8", errors="replace").splitlines()
    )


@pytest.mark.parametrize("payload", [b"", b"\x00\xff\r\n" * 32768], ids=["empty", "binary"])
def test_headless_input_is_exact_bounded_bytes_and_closes_stdin(payload: bytes) -> None:
    result = run_command(
        sys.executable,
        "-S",
        "-c",
        "import sys,hashlib; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
        input_data=payload,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == hashlib.sha256(payload).hexdigest()


def test_terminal_disconnect_cancels_and_cleans_owned_child(tmp_path: Path) -> None:
    output: list[bytes] = []
    events: list[CommandProcessLifecycleEvent] = []
    child_pid = tmp_path / "child.pid"
    script = """import pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-S', '-c', 'import time; time.sleep(30)'])
pathlib.Path(sys.argv[1]).write_text(str(child.pid))
print('READY', flush=True)
time.sleep(30)
"""
    with pytest.raises(CommandCancelledError) as caught:
        run_command(
            sys.executable,
            "-S",
            "-c",
            script,
            str(child_pid),
            terminal=CommandTerminal(
                lambda maximum: b"" if b"READY" in b"".join(output) else None,
                output.append,
            ),
            process_observer=events.append,
            timeout=5,
        )
    assert caught.value.supervisor.cleanup_reason == "cancel"
    assert caught.value.duration_ms > 0
    assert caught.value.physical_execution_id is not None
    assert "READY" in caught.value.stdout
    assert caught.value.supervisor.final_process_group_quiescent
    assert events[-1].kind == "DESCENDANTS_QUIESCENT"
    proc = Path("/proc") / child_pid.read_text() / "stat"
    if proc.exists():
        assert proc.read_text().rsplit(")", 1)[1].split()[0] == "Z"


def test_terminal_keeps_original_timeout_cleanup() -> None:
    script = "import os,time; os.write(1,b'x'*8192); time.sleep(30)"
    with pytest.raises(CommandTimeoutExpired) as caught:
        run_command(
            sys.executable,
            "-S",
            "-c",
            script,
            terminal=CommandTerminal(lambda maximum: None, lambda chunk: None),
            timeout=0.2,
            max_capture_bytes_per_stream=16384,
        )
    assert caught.value.supervisor.cleanup_reason == "timeout"
    assert caught.value.supervisor.final_process_group_quiescent


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("capture_limit", [None, 4096])
def test_terminal_repaint_over_budget_retains_tail_and_full_raw_evidence(
    cancel: bool, capture_limit: int | None
) -> None:
    chunk = b"\x1b[H" + b"x" * 4092 + b"\r"
    payload = chunk * 320 + b"REPAINT_DONE"
    observed = hashlib.sha256()
    count = 0

    def output(data: bytes) -> None:
        nonlocal count
        observed.update(data)
        count += len(data)

    script = (
        "import sys,time,tty; tty.setraw(0); "
        "sys.stdout.buffer.write((b'\\x1b[H'+b'x'*4092+b'\\r')*320+b'REPAINT_DONE'); "
        "sys.stdout.buffer.flush(); " + ("time.sleep(30)" if cancel else "")
    )
    terminal = CommandTerminal(
        lambda maximum: b"" if cancel and count == len(payload) else None, output
    )
    if cancel:
        with pytest.raises(CommandCancelledError) as caught:
            run_command(
                sys.executable,
                "-S",
                "-c",
                script,
                terminal=terminal,
                max_capture_bytes_per_stream=capture_limit,
                timeout=5,
            )
        text, evidence = caught.value.stdout, caught.value.stdout_evidence
        assert caught.value.supervisor.final_process_group_quiescent
    else:
        result = run_command(
            sys.executable,
            "-S",
            "-c",
            script,
            terminal=terminal,
            max_capture_bytes_per_stream=capture_limit,
            timeout=5,
        )
        assert result.returncode == 0
        text, evidence = result.stdout, result.stdout_evidence
    assert count == len(payload) > 1024 * 1024
    assert observed.hexdigest() == hashlib.sha256(payload).hexdigest()
    assert len(text.encode()) <= (capture_limit or 1024 * 1024)
    assert "omitted" in text and text.endswith("REPAINT_DONE")
    assert evidence is not None
    assert evidence.sha256 == observed.hexdigest() and evidence.byte_count == count


def test_headless_output_still_fails_its_capture_ceiling() -> None:
    with pytest.raises(CommandOutputLimitExceeded) as caught:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.stdout.buffer.write((b'\\x1b[H'+b'x'*4092+b'\\r')*320)",
            max_capture_bytes_per_stream=4096,
            timeout=5,
        )
    assert caught.value.stream == "stdout"
    assert caught.value.supervisor.cleanup_reason == "output_limit"
    assert caught.value.supervisor.final_process_group_quiescent


@pytest.mark.parametrize("failure", ["none", "spawn", "observer", "output"])
def test_terminal_descriptors_close_after_their_last_use(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    descriptors: list[int] = []
    original_openpty = os.openpty

    def openpty() -> tuple[int, int]:
        pair = original_openpty()
        descriptors.extend(pair)
        return pair

    def observe(event: CommandProcessLifecycleEvent) -> None:
        if failure == "observer" and event.kind == "PROCESS_STARTED":
            raise RuntimeError("observer failed")

    def output(chunk: bytes) -> None:
        if failure == "output":
            raise RuntimeError("output failed")

    monkeypatch.setattr(os, "openpty", openpty)
    argv = (
        ("/absent/terminal-fixture",)
        if failure == "spawn"
        else (sys.executable, "-S", "-c", "print('hello', flush=True)")
    )
    if failure == "none":
        assert (
            run_command(
                *argv,
                terminal=CommandTerminal(lambda maximum: None, output),
                process_observer=observe,
            ).returncode
            == 0
        )
    else:
        with pytest.raises(OSError if failure == "spawn" else RuntimeError):
            run_command(
                *argv,
                terminal=CommandTerminal(lambda maximum: None, output),
                process_observer=observe,
            )
    assert len(descriptors) == 2
    for descriptor in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("attack", ["both", "oversized", "mutable", "capture"])
def test_invalid_transport_fails_before_process_creation(
    monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    def spawn(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid input reached process creation")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    if attack == "both":
        with pytest.raises(ValueError):
            run_command(
                "unused", input_data=b"", terminal=CommandTerminal(lambda n: None, lambda b: None)
            )
    elif attack == "oversized":
        with pytest.raises(ValueError):
            run_command("unused", input_data=b"x" * (MAX_COMMAND_INPUT_BYTES + 1))
    elif attack == "mutable":
        with pytest.raises(ValueError):
            run_command("unused", input_data=bytearray(b"x"))  # type: ignore[arg-type]
    else:
        with pytest.raises(ValueError):
            run_command("unused", max_capture_bytes_per_stream=0)
