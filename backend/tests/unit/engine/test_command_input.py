"""Bounded ongoing stdin uses the original command's native lifetime owner."""

from __future__ import annotations

import hashlib
import os
import select
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from queue import Empty, Queue
from typing import Any, cast

import pytest

from oh_goon.engine.command import (
    MAX_COMMAND_INPUT_BYTES,
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandTerminal,
    CommandTimeoutExpired,
    run_command,
)

pytestmark = [
    pytest.mark.process,
    pytest.mark.skipif(sys.platform != "linux", reason="native Linux pipe and pidfd assertions"),
]


class _QueuedInput:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks: Queue[bytes] = Queue(maxsize=32)
        self.idle_reads = 0
        for chunk in chunks:
            self.chunks.put_nowait(chunk)

    def read_input(self, max_bytes: int) -> bytes | None:
        try:
            return self.chunks.get_nowait()
        except Empty:
            self.idle_reads += 1
            return None


@contextmanager
def _owned_processes() -> Generator[
    tuple[list[CommandProcessLifecycleEvent], Callable[[CommandProcessLifecycleEvent], None]],
    None,
    None,
]:
    events: list[CommandProcessLifecycleEvent] = []
    handles: list[int] = []

    def observe(event: CommandProcessLifecycleEvent) -> None:
        events.append(event)
        if event.kind == "PROCESS_STARTED":
            assert event.pid is not None
            handles.append(os.pidfd_open(event.pid))

    try:
        yield events, observe
        for handle in handles:
            assert select.select([handle], [], [], 0)[0], (
                "original child did not retire before fallback cleanup"
            )
    finally:
        for handle in handles:
            try:
                if not select.select([handle], [], [], 0)[0]:
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                    assert select.select([handle], [], [], 5)[0]
            finally:
                os.close(handle)


@pytest.fixture
def pipe_pressure(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    import fcntl

    original_write = os.write
    selected_fd: int | None = None
    observed = {"partial": 0, "blocked": 0}

    def write(descriptor: int, data: bytes | memoryview) -> int:
        nonlocal selected_fd
        if selected_fd is None:
            selected_fd = descriptor
            fcntl.fcntl(descriptor, fcntl.F_SETPIPE_SZ, 4096)
        try:
            count = original_write(descriptor, data)
        except BlockingIOError:
            observed["blocked"] += 1
            raise
        observed["partial"] += int(count < len(data))
        return count

    monkeypatch.setattr(os, "write", write)
    return observed


def test_output_can_provide_later_input_before_explicit_eof() -> None:
    source = _QueuedInput()
    observed = ""
    sent = 0
    messages = (b'{"id":"first"}\n', b'{"id":"second"}\n', b"")

    def output(stream: str, text: str) -> None:
        nonlocal observed, sent
        if stream != "stdout":
            return
        observed += text
        markers = ("READY\n", "ACK first\n", "ACK second\n")
        if sent < len(markers) and markers[sent] in observed:
            source.chunks.put_nowait(messages[sent])
            sent += 1

    script = """import json, os, sys, time
time.sleep(0.15)
print('READY', flush=True)
for _ in range(2):
    request = json.loads(sys.stdin.buffer.readline())
    os.write(1, b'ACK ')
    time.sleep(0.02)
    os.write(1, request['id'].encode() + b'\\n')
assert sys.stdin.buffer.read() == b''
print('EOF', flush=True)
"""
    with _owned_processes() as (events, observe):
        result = run_command(
            sys.executable,
            "-S",
            "-c",
            script,
            input_source=source,
            stream_output=output,
            process_observer=observe,
            timeout=5,
        )
    assert result.returncode == 0 and result.stdout == "READY\nACK first\nACK second\nEOF"
    assert sent == 3 and source.idle_reads > 0
    assert result.supervisor.final_process_group_quiescent
    assert {event.physical_execution_id for event in events} == {result.physical_execution_id}
    assert [event.kind for event in events][-2:] == ["PROCESS_EXITED", "DESCENDANTS_QUIESCENT"]


def test_dynamic_input_preserves_partial_writes_and_native_backpressure(
    pipe_pressure: dict[str, int],
) -> None:
    payload = bytes(range(256)) * 512
    source = _QueuedInput(payload[:65536], payload[65536:], b"")
    with _owned_processes() as (_, observe):
        result = run_command(
            sys.executable,
            "-S",
            "-c",
            "import hashlib,sys,time; time.sleep(0.4); "
            "print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())",
            input_source=source,
            process_observer=observe,
            timeout=8,
        )
    assert result.returncode == 0 and result.stdout == hashlib.sha256(payload).hexdigest()
    assert pipe_pressure["partial"] > 0 and pipe_pressure["blocked"] > 0
    assert source.chunks.empty()


@pytest.mark.parametrize("stop", ("cancel", "timeout"))
def test_blocked_dynamic_input_keeps_original_cancel_and_deadline_cleanup(
    stop: str,
    pipe_pressure: dict[str, int],
) -> None:
    source = _QueuedInput(b"x" * 65536)
    ready = threading.Event()

    def output(stream: str, text: str) -> None:
        if stream == "stdout" and "READY" in text:
            ready.set()

    expected = CommandCancelledError if stop == "cancel" else CommandTimeoutExpired
    with _owned_processes() as (events, observe):
        with pytest.raises(expected) as caught:
            run_command(
                sys.executable,
                "-S",
                "-c",
                "import time; print('READY',flush=True); time.sleep(30)",
                input_source=source,
                stream_output=output,
                process_observer=observe,
                cancel_requested=(lambda: ready.is_set() and pipe_pressure["blocked"] > 0)
                if stop == "cancel"
                else None,
                timeout=5 if stop == "cancel" else 0.5,
            )
        assert ready.is_set()
        assert pipe_pressure["partial"] > 0 and pipe_pressure["blocked"] > 0
        error = caught.value
        assert isinstance(error, (CommandCancelledError, CommandTimeoutExpired))
        assert error.supervisor.cleanup_reason == stop
        assert error.supervisor.final_process_group_quiescent
        assert {event.physical_execution_id for event in events} == {error.physical_execution_id}
        assert [event.kind for event in events][-2:] == ["PROCESS_EXITED", "DESCENDANTS_QUIESCENT"]


def test_native_stdin_closure_stops_source_without_replacing_native_result() -> None:
    ready = threading.Event()
    chunks = 0

    class Source:
        def read_input(self, max_bytes: int) -> bytes | None:
            nonlocal chunks
            if not ready.is_set():
                return None
            chunks += 1
            return b"x" * max_bytes

    def output(stream: str, text: str) -> None:
        if stream == "stdout" and "CLOSED" in text:
            ready.set()

    with _owned_processes() as (_, observe):
        result = run_command(
            sys.executable,
            "-S",
            "-c",
            "import os,time; os.close(0); print('CLOSED',flush=True); time.sleep(0.3); raise SystemExit(7)",
            input_source=Source(),
            stream_output=output,
            process_observer=observe,
            timeout=3,
        )
    assert result.returncode == 7 and result.stdout == "CLOSED"
    assert chunks == 1 and result.supervisor.final_process_group_quiescent


@pytest.mark.parametrize("bad_input", ("mutable", "oversized", "source_error", "total"))
def test_invalid_dynamic_input_closes_original_native_child(bad_input: str) -> None:
    ready = threading.Event()
    delivered = 0
    original_error = RuntimeError("input producer stopped")

    class Source:
        def read_input(self, max_bytes: int) -> bytes | None:
            nonlocal delivered
            if not ready.is_set():
                return None
            if bad_input == "mutable":
                return cast(bytes, bytearray(b"mutable"))
            if bad_input == "oversized":
                return b"x" * (max_bytes + 1)
            if bad_input == "source_error":
                raise original_error
            delivered += max_bytes
            return b"x" * max_bytes

    def output(stream: str, text: str) -> None:
        if stream == "stdout" and "READY" in text:
            ready.set()

    expected = RuntimeError if bad_input == "source_error" else ValueError
    with _owned_processes() as (events, observe):
        with pytest.raises(expected) as caught:
            run_command(
                sys.executable,
                "-S",
                "-c",
                "import sys; print('READY',flush=True); sys.stdin.buffer.read()",
                input_source=Source(),
                stream_output=output,
                process_observer=observe,
                timeout=20,
            )
        if bad_input == "source_error":
            assert caught.value is original_error
        if bad_input == "total":
            assert delivered > MAX_COMMAND_INPUT_BYTES
        assert [event.kind for event in events][-2:] == ["PROCESS_EXITED", "DESCENDANTS_QUIESCENT"]


@pytest.mark.parametrize("conflict", ("immutable", "terminal", "not_callable"))
def test_invalid_dynamic_transport_refuses_before_native_creation(
    monkeypatch: pytest.MonkeyPatch,
    conflict: str,
) -> None:
    def spawn(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid input transport reached native process creation")

    monkeypatch.setattr(subprocess, "Popen", spawn)
    source = _QueuedInput(b"")
    with pytest.raises(ValueError):
        if conflict == "immutable":
            run_command("unused", input_data=b"", input_source=source)
        elif conflict == "terminal":
            run_command(
                "unused",
                terminal=CommandTerminal(lambda size: None, lambda data: None),
                input_source=source,
            )
        else:
            run_command("unused", input_source=cast(Any, object()))
