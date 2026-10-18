from __future__ import annotations

import json
from pathlib import Path

from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.runtime.diagnostics import DiagnosticRouter, JsonlDiagnosticSink


class _CaptureSink:
    def __init__(self) -> None:
        self.events: list[DiagnosticEvent] = []

    def write(self, event: DiagnosticEvent) -> None:
        self.events.append(event)


class _BrokenSink:
    def write(self, event: DiagnosticEvent) -> None:
        del event
        raise OSError("diagnostic disk unavailable")


def _event(message: str = "projection failed") -> DiagnosticEvent:
    return DiagnosticEvent(
        severity="WARNING",
        source="test_source",
        owner="TestOwner",
        operation="project",
        message=message,
        execution_affected=False,
        diagnostic_capability_affected="test_projection",
        fleet_run_id="run-1",
    )


def test_router_keeps_delivering_after_one_sink_fails(capsys) -> None:
    capture = _CaptureSink()
    router = DiagnosticRouter((_BrokenSink(), capture))

    router.emit(_event())

    assert len(capture.events) == 1
    fallback = json.loads(capsys.readouterr().err)
    assert fallback["event"] == "DIAGNOSTIC_SINK_FAILED"
    assert fallback["sink"] == "_BrokenSink"
    assert fallback["diagnostic_event_id"] == capture.events[0].event_id


def test_jsonl_sink_writes_structured_private_debug_evidence(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics" / "diagnostics.jsonl"
    sink = JsonlDiagnosticSink(path)

    sink.write(_event())

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "test_source"
    assert payload["owner"] == "TestOwner"
    assert payload["fleet_run_id"] == "run-1"
    assert payload["execution_affected"] is False
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600


def test_jsonl_sink_rotates_without_becoming_an_execution_owner(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.jsonl"
    sink = JsonlDiagnosticSink(path, max_bytes=550, backups=2)

    sink.write(_event("a" * 180))
    sink.write(_event("b" * 180))
    sink.write(_event("c" * 180))

    assert path.exists()
    assert path.with_name("diagnostics.jsonl.1").exists()


def test_exception_event_is_bounded_even_when_exception_stringification_is_broken() -> None:
    class BrokenStringError(RuntimeError):
        def __str__(self) -> str:
            raise ValueError("cannot stringify")

    event = DiagnosticEvent.from_exception(
        BrokenStringError(),
        source="test_source",
        owner="TestOwner",
        operation="capture",
        execution_affected=False,
    )

    assert event.message == "BrokenStringError"
    assert event.exception_type == "BrokenStringError"
    assert event.traceback is not None
    assert len(event.traceback) <= 20_000


def test_router_fallback_survives_broken_sink_exception_stringification(capsys) -> None:
    class BrokenStringError(RuntimeError):
        def __str__(self) -> str:
            raise ValueError("cannot stringify")

    class BrokenStringSink:
        def write(self, event: DiagnosticEvent) -> None:
            del event
            raise BrokenStringError()

    capture = _CaptureSink()
    DiagnosticRouter((BrokenStringSink(), capture)).emit(_event())

    assert len(capture.events) == 1
    fallback = json.loads(capsys.readouterr().err)
    assert fallback["exception_type"] == "BrokenStringError"
    assert fallback["message"] == "BrokenStringError"


def test_exception_event_redacts_credentials_and_local_user_paths() -> None:
    try:
        raise RuntimeError(
            "Bearer secret-token password=hunter2 "
            "https://alice:private@example.test/api?token=abc "
            "/home/alice/private/repository.py"
        )
    except RuntimeError as exc:
        event = DiagnosticEvent.from_exception(
            exc,
            source="test_source",
            owner="TestOwner",
            operation="capture",
            execution_affected=False,
        )

    assert "secret-token" not in event.message
    assert "hunter2" not in event.message
    assert "alice:private" not in event.message
    assert "token=abc" not in event.message
    assert "/home/alice" not in event.message
    assert "[REDACTED]" in event.message
    assert "[LOCAL_PATH]" in event.message
    assert event.traceback is not None
    assert "hunter2" not in event.traceback
    assert "/home/alice" not in event.traceback
