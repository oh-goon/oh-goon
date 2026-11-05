from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from threading import Event, Thread
from time import monotonic, sleep

import pytest

from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.engine.command import CommandTerminal
from oh_goon.runtime.execution.worker_observation import (
    ParentRunObservations,
    WorkerRunObservations,
)
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore


class _Diagnostics:
    def __init__(self) -> None:
        self.events: list[DiagnosticEvent] = []
        self.received = Event()

    def emit(self, event: DiagnosticEvent) -> None:
        self.events.append(event)
        self.received.set()


def _event(run_id: str) -> DiagnosticEvent:
    return DiagnosticEvent(
        severity="INFO",
        source="worker-fixture",
        owner="RunWorker",
        operation="observation-barrier",
        message="native observation delivery",
        execution_affected=False,
        fleet_run_id=run_id,
    )


def _until(predicate: Callable[[], bool]) -> None:
    deadline = monotonic() + 3
    while not predicate():
        if monotonic() >= deadline:
            raise AssertionError("native observation transport did not deliver the bounded fixture")
        sleep(0.005)


def _small_worker_socket(parent: ParentRunObservations) -> WorkerRunObservations:
    channel = socket.socket(fileno=os.dup(parent.child_descriptor))
    try:
        # Native capacity is explicit so large host defaults cannot turn a
        # backpressure regression into a test that never exercised any loss.
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        return WorkerRunObservations("run", channel.detach())
    finally:
        channel.close()


def test_worker_output_and_activity_reach_the_existing_parent_buffer_for_only_this_run() -> None:
    store = GameTapeLiveOutputStore()
    diagnostics = _Diagnostics()
    parent = ParentRunObservations("run-one", store, diagnostics, None)
    worker = WorkerRunObservations("run-one", os.dup(parent.child_descriptor))
    parent.start()
    try:
        worker.open_run("another-run")
        worker.append("another-run", "repo", "step", 1, "stdout", "must not appear")
        worker.open_run("run-one")
        worker.append("run-one", "repo", "step", 1, "stdout", "hello\nworld\n")
        worker.append_activity("run-one", "Preparing the selected repository")
        worker.seal("run-one")
        worker.emit(_event("another-run"))
        worker.emit(_event("run-one"))
        assert diagnostics.received.wait(3)
        batch = store.snapshot("run-one")
        assert batch.state == "SEALED"
        assert batch.latest_activity == "Preparing the selected repository"
        assert "".join(chunk.text for chunk in batch.chunks) == "hello\nworld\n"
        assert [(chunk.repository_id, chunk.step_id, chunk.attempt) for chunk in batch.chunks] == [
            ("repo", "step", 1)
        ]
        assert store.snapshot("another-run").state == "UNAVAILABLE"
        assert [event.fleet_run_id for event in diagnostics.events] == ["run-one"]
    finally:
        worker.close()
        parent.close()
        parent.close()


def test_parent_loss_discards_observations_without_synthesizing_terminal_eof() -> None:
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), None, None)
    worker = WorkerRunObservations("run", os.dup(parent.child_descriptor))
    terminal = worker.terminal((24, 80))
    assert terminal is not None
    parent.close()
    try:
        # A dead display/control channel is not a cancellation request or stdin
        # EOF. The execution owner continues to consult canonical SQLite state.
        assert terminal.read_input(4096) is None
        worker.open_run("run")
        worker.append("run", "repo", "step", 1, "stdout", "diagnostic output")
        worker.append_activity("run", "continued without a display")
        worker.emit(_event("run"))
        terminal.write_output(b"native terminal output")
        worker.seal("run")
    finally:
        worker.close()


def test_terminal_input_output_resize_and_explicit_eof_keep_native_semantics() -> None:
    queued_input: deque[bytes] = deque([b"first input"])
    output = bytearray()
    sizes = [(24, 80)]

    def read_input(limit: int) -> bytes | None:
        assert limit == 4096
        return queued_input.popleft() if queued_input else None

    terminal = CommandTerminal(read_input, output.extend, sizes[0], lambda: sizes[0])
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), None, terminal)
    worker = WorkerRunObservations("run", os.dup(parent.child_descriptor))
    selected = worker.terminal(terminal.initial_size)
    assert selected is not None
    parent.start()
    try:
        received = bytearray()

        def collect_input() -> bool:
            data = selected.read_input(3)
            if data is not None:
                received.extend(data)
            return bytes(received) == b"first input"

        _until(collect_input)
        assert selected.read_input(4096) is None
        selected.write_output(b"native\x00terminal\n")
        _until(lambda: bytes(output) == b"native\x00terminal\n")
        sizes[0] = (40, 120)
        assert selected.current_size is not None
        _until(lambda: selected.current_size is not None and selected.current_size() == (40, 120))
        queued_input.append(b"")
        _until(lambda: selected.read_input(4096) == b"")
    finally:
        worker.close()
        parent.close()


def test_unread_output_backpressure_never_blocks_native_execution() -> None:
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), None, None)
    worker = WorkerRunObservations("run", os.dup(parent.child_descriptor))
    try:
        # Deliberately do not start the consumer. This exceeds the bounded native
        # socket capacity; non-authoritative output may be dropped, never block a
        # Run waiting for a parent display that stopped consuming.
        worker.append("run", "repo", "step", 1, "stdout", "x" * (512 * 1024))
        worker.emit(_event("run"))
    finally:
        worker.close()
        parent.close()


def test_inherited_observation_capability_is_closed_before_later_native_exec() -> None:
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), None, None)
    descriptor = os.dup(parent.child_descriptor)
    os.set_inheritable(descriptor, True)
    worker = WorkerRunObservations("run", descriptor)
    try:
        assert os.get_inheritable(descriptor) is False
        # Deliberately disable subprocess's ordinary close-fds protection: the
        # worker must revoke bootstrap inheritance itself before any native exec.
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import os,sys\n"
                "try: os.fstat(int(sys.argv[1]))\n"
                "except OSError: raise SystemExit(0)\n"
                "raise SystemExit(11)\n",
                str(descriptor),
            ],
            close_fds=False,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0
    finally:
        worker.close()
        parent.close()


def test_terminal_backpressure_is_nonblocking_and_reports_loss_when_capture_resumes() -> None:
    diagnostics = _Diagnostics()
    output = bytearray()
    terminal = CommandTerminal(lambda limit: None, output.extend)
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), diagnostics, terminal)
    worker = _small_worker_socket(parent)
    selected = worker.terminal(terminal.initial_size)
    assert selected is not None
    canary = b"private-terminal-body-must-not-be-diagnostic"
    data = canary * 16384
    written = Event()

    def write() -> None:
        selected.write_output(data)
        written.set()

    writer = Thread(target=write)
    writer.start()
    try:
        # The parent deliberately consumes nothing until the write completes.
        assert written.wait(1), "terminal output blocked on an unread display socket"
        parent.start()

        def resumed() -> bool:
            worker.emit(_event("run"))
            return any("terminal_bytes=" in event.message for event in diagnostics.events)

        _until(resumed)
        worker.close()
        parent.close()
        reports = [event for event in diagnostics.events if "terminal_bytes=" in event.message]
        assert reports
        match = re.search(r"terminal_bytes=(\d+)", reports[-1].message)
        assert match is not None
        assert int(match.group(1)) == len(data) - len(output) > 0
        assert all(event.execution_affected is False for event in reports)
        assert all(canary.decode() not in event.message for event in diagnostics.events)
        assert not any("without a final" in event.message for event in diagnostics.events)
    finally:
        worker.close()
        parent.close()
        writer.join(timeout=1)
        assert not writer.is_alive()


def test_final_summary_accounts_for_last_output_and_terminal_loss_without_another_write() -> None:
    store = GameTapeLiveOutputStore()
    diagnostics = _Diagnostics()
    terminal = CommandTerminal(lambda limit: None, lambda data: None)
    parent = ParentRunObservations("run", store, diagnostics, terminal)
    worker = _small_worker_socket(parent)
    selected = worker.terminal(terminal.initial_size)
    assert selected is not None
    try:
        worker.open_run("run")
        worker.append("run", "repo", "step", 1, "stdout", "x" * (512 * 1024))
        selected.write_output(b"t" * (512 * 1024))
        parent.start()
        # Only close sends the final loss counters; no subsequent callback can
        # opportunistically flush the pending observation summary.
        worker.close()
        parent.close()
        assert store.snapshot("run").dropped_chunk_count > 0
        assert any("terminal_bytes=524288" in event.message for event in diagnostics.events)
        assert not any("without a final" in event.message for event in diagnostics.events)
    finally:
        worker.close()
        parent.close()


def test_abrupt_peer_eof_without_summary_reports_unavailable_capture_once() -> None:
    diagnostics = _Diagnostics()
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), diagnostics, None)
    peer = socket.socket(fileno=os.dup(parent.child_descriptor))
    parent.start()
    try:
        peer.send(json.dumps({"kind": "open"}).encode())
        peer.close()  # Physical worker loss cannot manufacture a final summary.
        assert diagnostics.received.wait(1)
        parent.close()
        parent.close()
        assert len(diagnostics.events) == 1
        event = diagnostics.events[0]
        assert "without a final capture summary" in event.message
        assert "completeness is unavailable" in event.message
        assert event.failure_class == "diagnostic_degraded"
        assert event.execution_affected is False
    finally:
        peer.close()
        parent.close()


def test_unread_display_bounds_final_close_and_does_not_claim_complete_capture() -> None:
    diagnostics = _Diagnostics()
    parent = ParentRunObservations("run", GameTapeLiveOutputStore(), diagnostics, None)
    worker = _small_worker_socket(parent)
    worker.open_run("run")
    worker.append("run", "repo", "step", 1, "stdout", "x" * (512 * 1024))
    closed = Event()

    def close() -> None:
        worker.close()
        closed.set()

    closer = Thread(target=close)
    closer.start()
    try:
        assert closed.wait(1), "final observation summary waited indefinitely for a display"
        parent.start()
        _until(lambda: any("without a final" in event.message for event in diagnostics.events))
        assert all(event.execution_affected is False for event in diagnostics.events)
    finally:
        parent.close()
        closer.join(timeout=1)
        assert not closer.is_alive()
        worker.close()


def test_terminal_and_diagnostic_sink_failures_stay_nonfatal_and_private(
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary = "private-terminal-callback-failure"
    called = Event()

    def broken_terminal(data: bytes) -> None:
        called.set()
        raise RuntimeError(canary)

    class BrokenDiagnostics(_Diagnostics):
        def emit(self, event: DiagnosticEvent) -> None:
            super().emit(event)
            raise RuntimeError(canary)

    class BrokenCapture(GameTapeLiveOutputStore):
        def seal(self, fleet_run_id: str) -> None:
            raise RuntimeError(canary)

    diagnostics = BrokenDiagnostics()
    terminal = CommandTerminal(lambda limit: None, broken_terminal)
    parent = ParentRunObservations("run", BrokenCapture(), diagnostics, terminal)
    worker = WorkerRunObservations("run", os.dup(parent.child_descriptor))
    selected = worker.terminal(terminal.initial_size)
    assert selected is not None
    parent.start()
    try:
        selected.write_output(b"terminal output")
        assert called.wait(1)
        parent.close()
        assert selected.read_input(4096) is None
        assert diagnostics.events
        assert all(event.execution_affected is False for event in diagnostics.events)
        assert all(canary not in event.message for event in diagnostics.events)
        assert canary not in capsys.readouterr().err
    finally:
        worker.close()
        parent.close()
