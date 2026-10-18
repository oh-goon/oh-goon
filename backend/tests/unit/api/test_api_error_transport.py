from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from fastapi import HTTPException, Request

from oh_goon.api.errors import http_exception_handler, unexpected_exception_handler


def _request(diagnostics=None) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/test",
            "raw_path": b"/test",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
            "state": {"request_id": "request-test"},
            "app": SimpleNamespace(state=SimpleNamespace(diagnostics=diagnostics)),
        }
    )


def test_http_exception_handler_preserves_structured_error_payload() -> None:
    response = asyncio.run(
        http_exception_handler(
            _request(),
            HTTPException(
                status_code=409,
                detail={"code": "STATE_CONFLICT", "message": "changed", "retryable": True},
            ),
        )
    )
    payload = json.loads(response.body)
    assert payload["code"] == "STATE_CONFLICT"
    assert payload["kind"] == "CONFLICT"
    assert payload["request_id"] == "request-test"


def test_http_exception_handler_normalizes_unstructured_error_payload() -> None:
    response = asyncio.run(
        http_exception_handler(
            _request(), HTTPException(status_code=503, detail="temporarily unavailable")
        )
    )
    payload = json.loads(response.body)
    assert payload["code"] == "SERVICE_UNAVAILABLE"
    assert payload["kind"] == "INTERNAL"
    assert payload["retryable"] is True
    assert payload["request_id"] == "request-test"


def test_domain_error_transport_maps_semantic_error_kind() -> None:
    from oh_goon.api.errors import domain_exception_handler
    from oh_goon.core.errors import ExecutionBusy, InvalidRequest, JournalCorruption, RunNotFound

    cases = (
        (InvalidRequest("bad input"), 400),
        (RunNotFound("missing"), 404),
        (ExecutionBusy("busy"), 409),
        (JournalCorruption("corrupt"), 500),
    )
    for error, expected_status in cases:
        response = asyncio.run(domain_exception_handler(_request(), error))
        payload = json.loads(response.body)
        assert response.status_code == expected_status
        assert payload["code"] == error.code
        assert payload["kind"] == error.kind.value
        assert payload["request_id"] == "request-test"


def test_unexpected_error_emits_request_correlated_diagnostic(monkeypatch) -> None:
    class Diagnostics:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    diagnostics = Diagnostics()

    response = asyncio.run(
        unexpected_exception_handler(
            _request(diagnostics), RuntimeError("backend exploded")
        )
    )
    payload = json.loads(response.body)

    assert response.status_code == 500
    assert payload["code"] == "INTERNAL_ERROR"
    assert payload["message"] == "Unexpected internal error"
    assert payload["request_id"] == "request-test"
    assert len(diagnostics.events) == 1
    event = diagnostics.events[0]
    assert event.request_id == "request-test"
    assert event.http_method == "GET"
    assert event.http_path == "/test"
    assert event.exception_type == "RuntimeError"
    assert "backend exploded" in event.traceback
