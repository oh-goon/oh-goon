"""Bounded, non-authorizing live-output and terminal transport for one Run worker."""

from __future__ import annotations

import base64
import json
import select
import socket
from threading import Event, Lock, Thread
from typing import Any, cast

from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.engine.command import CommandTerminal
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore

_PACKET_BYTES = 64 * 1024
_TEXT_CHARS = 4096
_MAX_LOSS_COUNT = (1 << 63) - 1
_LOSS_FIELDS = ("output_chunks", "terminal_bytes", "other_frames")


def _send(channel: socket.socket, payload: dict[str, Any]) -> bool:
    packet = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    if len(packet) > _PACKET_BYTES:
        return False
    try:
        return channel.send(packet) == len(packet)
    except OSError:
        # Observation loss never changes durable execution authority or results.
        return False


def _receive(channel: socket.socket) -> dict[str, Any] | None:
    packet = channel.recv(_PACKET_BYTES + 1)
    if not packet:
        raise EOFError
    if len(packet) > _PACKET_BYTES:
        return None
    try:
        payload = json.loads(packet)
    except (ValueError, UnicodeError, RecursionError):
        return None
    return payload if isinstance(payload, dict) else None


class ParentRunObservations:
    """Own one transient native socket pair, never a Run status or execution callable."""

    def __init__(
        self,
        fleet_run_id: str,
        live_output: GameTapeLiveOutputStore,
        diagnostics: DiagnosticEmitter | None,
        terminal: CommandTerminal | None,
    ) -> None:
        self._run_id = fleet_run_id
        self._live_output = live_output
        self._diagnostics = diagnostics
        self._terminal = terminal
        self._parent, self._child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self._parent.setblocking(False)
        self._child.setblocking(False)
        self._stop = Event()
        self._thread: Thread | None = None
        self._closed = False
        self._lock = Lock()
        self._loss_counts = (0, 0, 0)
        self._final_summary_received = False
        self._incomplete_reported = False

    @property
    def child_descriptor(self) -> int:
        return self._child.fileno()

    def start(self) -> None:
        self._child.close()
        self._thread = Thread(target=self._observe, name="oh-goon-run-output", daemon=True)
        self._thread.start()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._child.close()
        if self._thread is not None:
            # A retired child has closed its peer. Drain its bounded queued tail
            # before stopping, so quick Runs retain their final output.
            self._thread.join(timeout=1)
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.1)
        self._parent.close()
        try:
            self._live_output.seal(self._run_id)
        except Exception:
            self._degraded("Run live-output capture could not be sealed")
        if self._thread is not None:
            self._report_incomplete()

    def _observe(self) -> None:
        pending: dict[str, Any] | None = None
        previous_size = self._terminal.initial_size if self._terminal is not None else None
        try:
            while not self._stop.is_set():
                readable, _, _ = select.select([self._parent], [], [], 0.02)
                if readable:
                    payload = _receive(self._parent)
                    if payload is not None:
                        self._apply(payload)
                if pending is not None:
                    if _send(self._parent, pending):
                        pending = None
                    continue
                terminal = self._terminal
                if terminal is None:
                    continue
                size = terminal.current_size() if terminal.current_size is not None else None
                if size is not None and size != previous_size:
                    previous_size = size
                    pending = {"kind": "resize", "size": list(size)}
                else:
                    data = terminal.read_input(4096)
                    if data is not None:
                        pending = {"kind": "input", "data": base64.b64encode(data).decode("ascii")}
                        if not data:
                            self._terminal = None
                if pending is not None and _send(self._parent, pending):
                    pending = None
        except Exception:
            # Display, terminal and diagnostic callbacks are observations. Their
            # arbitrary exception text may contain output and cannot escape a
            # background thread or become execution/cancellation authority.
            self._report_incomplete()

    def _degraded(self, message: str) -> None:
        if self._diagnostics is not None:
            try:
                self._diagnostics.emit(
                    DiagnosticEvent(
                        severity="WARNING",
                        source="run_worker_observation",
                        owner="ParentRunObservations",
                        operation="capture_output",
                        message=message,
                        execution_affected=False,
                        diagnostic_capability_affected="live_output_capture",
                        failure_class="diagnostic_degraded",
                        fleet_run_id=self._run_id,
                    )
                )
            except Exception:
                # The original sink is itself unavailable. Do not print output,
                # add a fallback evidence writer or change the Run result.
                return

    def _report_incomplete(self) -> None:
        with self._lock:
            if self._final_summary_received or self._incomplete_reported:
                return
            self._incomplete_reported = True
        self._degraded(
            "Run output observation ended without a final capture summary; completeness is unavailable"
        )

    def _apply_loss_summary(self, payload: dict[str, Any], *, final: bool) -> None:
        observed = tuple(payload.get(field) for field in _LOSS_FIELDS)
        if any(type(value) is not int or not 0 <= value <= _MAX_LOSS_COUNT for value in observed):
            return
        counts = cast(tuple[int, int, int], observed)
        output, terminal, other = counts
        if any(
            current < previous for current, previous in zip(counts, self._loss_counts, strict=True)
        ):
            return
        previous_output, previous_terminal, previous_other = self._loss_counts
        self._loss_counts = (output, terminal, other)
        if output > previous_output:
            self._live_output.record_dropped_chunks(self._run_id, output - previous_output)
        if (output, terminal, other) != (previous_output, previous_terminal, previous_other):
            self._degraded(
                f"Run output observation dropped data: output_chunks={output}; "
                f"terminal_bytes={terminal}; other_frames={other}"
            )
        if final:
            with self._lock:
                self._final_summary_received = True

    def _apply(self, payload: dict[str, Any]) -> None:
        kind = payload.get("kind")
        if kind == "open":
            self._live_output.open_run(self._run_id)
        elif kind == "seal":
            self._live_output.seal(self._run_id)
        elif kind == "output":
            if (
                all(isinstance(payload.get(key), str) for key in ("repository", "step", "text"))
                and type(payload.get("attempt")) is int
                and payload.get("stream") in {"stdout", "stderr"}
            ):
                self._live_output.append(
                    self._run_id,
                    payload["repository"],
                    payload["step"],
                    payload["attempt"],
                    payload["stream"],
                    payload["text"],
                )
        elif kind == "activity" and isinstance(payload.get("message"), str):
            self._live_output.append_activity(self._run_id, payload["message"])
        elif kind in {"observation-loss", "final-observation"}:
            self._apply_loss_summary(payload, final=kind == "final-observation")
        elif kind == "diagnostic" and self._diagnostics is not None:
            event = DiagnosticEvent.model_validate(payload.get("event"))
            if event.fleet_run_id in {None, self._run_id}:
                self._diagnostics.emit(event)
        elif kind == "terminal" and self._terminal is not None:
            data = payload.get("data")
            if isinstance(data, str):
                self._terminal.write_output(base64.b64decode(data, validate=True))


class WorkerRunObservations(GameTapeLiveOutputStore):
    """Forward existing output-owner calls without transporting execution authority."""

    def __init__(self, fleet_run_id: str, descriptor: int) -> None:
        self._run_id = fleet_run_id
        self._channel = socket.socket(fileno=descriptor)
        self._channel.set_inheritable(False)
        if self._channel.type != socket.SOCK_SEQPACKET:
            self._channel.close()
            raise ValueError("Run worker observation socket has another transport type")
        self._channel.setblocking(False)
        self._write_lock = Lock()
        self._input = bytearray()
        self._eof = False
        self._size: tuple[int, int] | None = None
        self._loss_counts = [0, 0, 0]
        self._reported_counts: tuple[int, ...] = (0, 0, 0)
        self._closed = False

    def close(self) -> None:
        with self._write_lock:
            if self._closed:
                return
            self._closed = True
            try:
                # One bounded final attempt accounts for loss even when the
                # final output/seal saturated the socket. Absence is explicitly
                # unavailable at the parent; this never carries a Run result.
                summary = self._summary(final=True)
                if not _send(self._channel, summary):
                    if select.select([], [self._channel], [], 0.1)[1]:
                        _send(self._channel, summary)
            except OSError:
                pass  # Closed observation peers do not own execution outcome.
            finally:
                self._channel.close()

    def _summary(self, *, final: bool) -> dict[str, Any]:
        return {
            "kind": "final-observation" if final else "observation-loss",
            **dict(zip(_LOSS_FIELDS, self._loss_counts, strict=True)),
        }

    def _emit(self, payload: dict[str, Any], *, terminal_bytes: int = 0) -> None:
        with self._write_lock:
            if self._closed:
                return
            if tuple(self._loss_counts) != self._reported_counts and _send(
                self._channel, self._summary(final=False)
            ):
                self._reported_counts = tuple(self._loss_counts)
            if not _send(self._channel, payload):
                kind = payload.get("kind")
                index, amount = (
                    (0, 1)
                    if kind == "output"
                    else (1, terminal_bytes)
                    if kind == "terminal"
                    else (2, 1)
                )
                self._loss_counts[index] = min(_MAX_LOSS_COUNT, self._loss_counts[index] + amount)

    def open_run(self, fleet_run_id: str) -> None:
        if fleet_run_id == self._run_id:
            self._emit({"kind": "open"})

    def seal(self, fleet_run_id: str) -> None:
        if fleet_run_id == self._run_id:
            self._emit({"kind": "seal"})

    def append(
        self,
        fleet_run_id: str,
        repository_id: str,
        step_id: str,
        attempt: int,
        stream: str,
        text: str,
    ) -> None:
        if fleet_run_id != self._run_id:
            return
        for offset in range(0, len(text), _TEXT_CHARS):
            self._emit(
                {
                    "kind": "output",
                    "repository": repository_id,
                    "step": step_id,
                    "attempt": attempt,
                    "stream": stream,
                    "text": text[offset : offset + _TEXT_CHARS],
                }
            )

    def append_activity(self, fleet_run_id: str, message: str) -> None:
        if fleet_run_id == self._run_id:
            self._emit({"kind": "activity", "message": message[:512]})

    def emit(self, event: DiagnosticEvent) -> None:
        self._emit({"kind": "diagnostic", "event": event.model_dump(mode="json")})

    def terminal(self, initial_size: tuple[int, int] | None) -> CommandTerminal | None:
        if initial_size is None:
            return None
        self._size = initial_size
        return CommandTerminal(
            self._read_input, self._write_terminal, initial_size, self._current_size
        )

    def _read_controls(self) -> None:
        # Bound work and retained terminal input per native poll. A disconnected
        # control process is not cancellation; durable Run cancellation stays SQLite-owned.
        for _ in range(16):
            if len(self._input) >= 64 * 1024:
                return
            try:
                payload = _receive(self._channel)
            except (OSError, EOFError):
                return
            if payload is None:
                continue
            if payload.get("kind") == "input" and isinstance(payload.get("data"), str):
                try:
                    data = base64.b64decode(payload["data"], validate=True)
                except ValueError:
                    continue
                if len(data) > 4096:
                    continue
                if not data:
                    self._eof = True
                self._input.extend(data)
            elif payload.get("kind") == "resize":
                size = payload.get("size")
                if (
                    isinstance(size, list)
                    and len(size) == 2
                    and all(type(value) is int and 1 <= value <= 65535 for value in size)
                ):
                    self._size = (size[0], size[1])

    def _read_input(self, limit: int) -> bytes | None:
        self._read_controls()
        if self._input:
            data = bytes(self._input[:limit])
            del self._input[:limit]
            return data
        return b"" if self._eof else None

    def _current_size(self) -> tuple[int, int] | None:
        self._read_controls()
        return self._size

    def _write_terminal(self, data: bytes) -> None:
        for offset in range(0, len(data), 4096):
            chunk = data[offset : offset + 4096]
            self._emit(
                {
                    "kind": "terminal",
                    "data": base64.b64encode(chunk).decode("ascii"),
                },
                terminal_bytes=len(chunk),
            )
