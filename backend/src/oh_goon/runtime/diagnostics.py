from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from threading import Lock
from typing import Protocol, TextIO

from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.path_authority import ensure_private_directory

DEFAULT_MAX_DIAGNOSTIC_LOG_BYTES = 5_000_000
DEFAULT_DIAGNOSTIC_LOG_BACKUPS = 3


class DiagnosticSink(Protocol):
    """A passive representation/storage sink for already-owned diagnostic facts."""

    def write(self, event: DiagnosticEvent) -> None: ...


class DiagnosticRouter:
    """Best-effort fan-out of diagnostics; sink failure can never alter execution."""

    def __init__(self, sinks: Iterable[DiagnosticSink]) -> None:
        self._sinks = tuple(sinks)

    def emit(self, event: DiagnosticEvent) -> None:
        for sink in self._sinks:
            try:
                sink.write(event)
            except Exception as exc:
                _write_sink_failure_fallback(event, sink, exc)


class StderrDiagnosticSink:
    """Human-readable developer sink; the structured JSONL file remains the debug source."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr
        self._lock = Lock()

    def write(self, event: DiagnosticEvent) -> None:
        fields = [
            "OH-GOON DIAGNOSTIC",
            f"Event: {event.event_id}",
            f"Severity: {event.severity}",
            f"Source: {event.source}",
            f"Owner: {event.owner}",
            f"Operation: {event.operation}",
            f"Execution affected: {'yes' if event.execution_affected else 'no'}",
        ]
        if event.diagnostic_capability_affected:
            fields.append(f"Diagnostic capability: {event.diagnostic_capability_affected}")
        if event.fleet_run_id:
            fields.append(f"Run: {event.fleet_run_id}")
        if event.repository_id:
            fields.append(f"Repository: {event.repository_id}")
        if event.step_id:
            fields.append(f"Step: {event.step_id}")
        if event.request_id:
            fields.append(f"Request: {event.request_id}")
        if event.http_method or event.http_path:
            fields.append(f"HTTP: {event.http_method or '?'} {event.http_path or '?'}")
        if event.exception_type:
            fields.append(f"Exception: {event.exception_type}")
        fields.append(f"Message: {event.message}")
        with self._lock:
            self._stream.write("\n" + "\n".join(fields) + "\n")
            self._stream.flush()


class JsonlDiagnosticSink:
    """Private bounded local diagnostic history; never an execution/state authority."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = DEFAULT_MAX_DIAGNOSTIC_LOG_BYTES,
        backups: int = DEFAULT_DIAGNOSTIC_LOG_BACKUPS,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("Diagnostic log max_bytes must be positive")
        if backups < 1:
            raise ValueError("Diagnostic log backups must be positive")
        self._path = path.resolve()
        self._max_bytes = max_bytes
        self._backups = backups
        self._lock = Lock()

    @property
    def path(self) -> Path:
        return self._path

    def write(self, event: DiagnosticEvent) -> None:
        payload = event.model_dump_json(exclude_none=True) + "\n"
        encoded_size = len(payload.encode("utf-8"))
        with self._lock:
            ensure_private_directory(self._path.parent)
            if self._path.exists() and self._path.stat().st_size + encoded_size > self._max_bytes:
                self._rotate_locked()
            descriptor = os.open(
                self._path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "a", encoding="utf-8", closefd=False) as handle:
                    handle.write(payload)
                    handle.flush()
            finally:
                os.close(descriptor)

    def _rotate_locked(self) -> None:
        oldest = self._path.with_name(f"{self._path.name}.{self._backups}")
        oldest.unlink(missing_ok=True)
        for index in range(self._backups - 1, 0, -1):
            source = self._path.with_name(f"{self._path.name}.{index}")
            if source.exists():
                source.replace(self._path.with_name(f"{self._path.name}.{index + 1}"))
        if self._path.exists():
            self._path.replace(self._path.with_name(f"{self._path.name}.1"))


def _write_sink_failure_fallback(
    event: DiagnosticEvent, sink: DiagnosticSink, exc: Exception
) -> None:
    try:
        message = str(exc) or exc.__class__.__name__
    except Exception:
        message = exc.__class__.__name__
    payload = {
        "event": "DIAGNOSTIC_SINK_FAILED",
        "diagnostic_event_id": event.event_id,
        "sink": sink.__class__.__name__,
        "exception_type": exc.__class__.__name__,
        "message": message[:1_000],
    }
    try:
        sys.stderr.write(json.dumps(payload, sort_keys=True) + "\n")
        sys.stderr.flush()
    except Exception:
        # This is the final non-authoritative fallback. There is no safe recursive sink.
        return
