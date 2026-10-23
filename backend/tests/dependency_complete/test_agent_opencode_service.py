from __future__ import annotations

import asyncio
import base64
import json
import time
import traceback
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict

import httpx2
import pytest
from pydantic import SecretStr

from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.integrations.agent_opencode_service import (
    OpenCodeServiceClient,
    OpenCodeServiceError,
    OpenCodeServiceMetadata,
    validate_opencode_service_endpoint,
)

_PASSWORD = "private-control-transport-password"
_ENDPOINT = "http://127.0.0.1:49374"


class TrackingStream(httpx2.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.reads = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.reads += 1
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


def transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> tuple[list[dict[str, object]], list[httpx2.AsyncClient]]:
    original = httpx2.AsyncClient
    options: list[dict[str, object]] = []
    clients: list[httpx2.AsyncClient] = []

    def client(**kwargs):
        options.append(kwargs)
        selected = original(**kwargs, transport=httpx2.MockTransport(handler))
        clients.append(selected)
        return selected

    monkeypatch.setattr(httpx2, "AsyncClient", client)
    return options, clients


@pytest.mark.parametrize(
    ("endpoint", "normalized"),
    [
        (_ENDPOINT, _ENDPOINT),
        ("http://127.0.0.2:1", "http://127.0.0.2:1"),
        ("http://[0:0:0:0:0:0:0:1]:65535", "http://[::1]:65535"),
    ],
)
def test_explicit_numeric_loopback_authority_is_pure_and_normalized(endpoint, normalized):
    assert validate_opencode_service_endpoint(endpoint) == normalized


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:49374",
        "https://127.0.0.1:49374",
        "http://192.0.2.1:49374",
        "http://0.0.0.0:49374",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:49374/",
        "http://127.0.0.1:49374/api/info",
        "http://127.0.0.1:49374?",
        "http://127.0.0.1:49374?auth_token=PRIVATE",
        "http://127.0.0.1:49374#",
        "http://PRIVATE@127.0.0.1:49374",
        "http://@127.0.0.1:49374",
        "http://[::1%25lo]:49374",
        "http://127.0.0.1:49374\n",
        "http://127.0.0.1:49374\\PRIVATE",
    ],
)
def test_invalid_authority_rejects_before_transport_without_echo(monkeypatch, endpoint):
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kwargs: pytest.fail("invalid endpoint connected")
    )
    with pytest.raises(OpenCodeServiceError) as caught:
        OpenCodeServiceClient(endpoint, password=SecretStr(_PASSWORD))
    assert "PRIVATE" not in str(caught.value)
    assert _PASSWORD not in str(caught.value)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf")])
def test_invalid_timeout_refuses_before_transport(monkeypatch, timeout):
    monkeypatch.setattr(
        httpx2, "AsyncClient", lambda **kwargs: pytest.fail("invalid timeout connected")
    )
    with pytest.raises(OpenCodeServiceError, match="finite and positive"):
        OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD), timeout_seconds=timeout)


def test_connect_observes_only_native_identity_and_closes_its_transport(monkeypatch):
    async def exercise():
        requests = []
        monkeypatch.setenv("HTTP_PROXY", "http://UNSELECTED_PROXY:1")
        monkeypatch.setenv("OPENCODE_PASSWORD", "UNSELECTED_PASSWORD")
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "UNSELECTED_LEGACY_PASSWORD")
        stream = TrackingStream(
            (
                json.dumps(
                    {
                        "version": "2.1.2",
                        "pid": 42,
                        "urls": ["http://UNRELATED_URL:1"],
                        "paths": {"tmp": "PRIVATE_NATIVE_PATH"},
                        "extra": {"credential": "PRIVATE_RESPONSE_CANARY"},
                    }
                ).encode(),
            )
        )

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, stream=stream)

        options, clients = transport(monkeypatch, handler)
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD), timeout_seconds=3)
        assert client.metadata is None
        async with client as connected:
            assert connected is client
            assert client.metadata == OpenCodeServiceMetadata("2.1.2", 42)
            assert await client.connect() is client.metadata
        assert asdict(client.metadata) == {"version": "2.1.2", "pid": 42}
        assert len(requests) == 1
        request = requests[0]
        assert request.method == "GET" and str(request.url) == _ENDPOINT + "/api/info"
        assert (
            request.headers["authorization"]
            == "Basic " + base64.b64encode(("opencode:" + _PASSWORD).encode()).decode()
        )
        assert request.headers["accept-encoding"] == "identity"
        assert options[0]["trust_env"] is False and options[0]["follow_redirects"] is False
        assert request.extensions["timeout"]["read"] == 3
        assert stream.closed and clients[0].is_closed
        assert "PRIVATE" not in repr(client.metadata) and _PASSWORD not in repr(client)
        await client.close()
        with pytest.raises(OpenCodeServiceError, match="closed"):
            await client.connect()

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [301, 302, 401, 403, 500])
def test_http_failure_never_follows_redirect_or_reads_server_error(monkeypatch, status):
    async def exercise():
        requests = []
        stream = TrackingStream((b"PRIVATE_RESPONSE_CANARY",))

        def handler(request):
            requests.append(request)
            return httpx2.Response(
                status, headers={"Location": "http://UNRELATED:1"}, stream=stream
            )

        _, clients = transport(monkeypatch, handler)
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        with pytest.raises(OpenCodeServiceError, match="rejected readiness") as caught:
            await client.connect()
        assert len(requests) == 1 and stream.reads == 0
        assert stream.closed and clients[0].is_closed and client.metadata is None
        assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("headers", "chunks", "expected_reads"),
    [
        ({"Content-Length": "65537"}, (b"PRIVATE",), 0),
        ({"Content-Encoding": "gzip"}, (b"PRIVATE",), 0),
        ({}, (b"x" * 32768, b"x" * 32768, b"PRIVATE" * 1024, b"unread"), 3),
    ],
)
def test_readiness_bound_rejects_before_accumulation_and_closes(
    monkeypatch, headers, chunks, expected_reads
):
    async def exercise():
        stream = TrackingStream(chunks)
        _, clients = transport(
            monkeypatch, lambda request: httpx2.Response(200, headers=headers, stream=stream)
        )
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        with pytest.raises(OpenCodeServiceError):
            await client.connect()
        assert stream.reads == expected_reads
        assert stream.closed and clients[0].is_closed and client.metadata is None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "payload",
    [
        b"PRIVATE malformed response",
        b"[]",
        b'{"version":"2.1.2","version":"PRIVATE","pid":42}',
        b'{"version":"2.1.2","pid":NaN}',
        b'{"version":"PRIVATE","pid":42}',
        b'{"version":"2.1.2","pid":true}',
        b'{"version":"2.1.2","pid":0}',
        b'{"version":"2.1.2","pid":-1}',
        b'{"version":"2.1.2","pid":"42"}',
    ],
)
def test_incompatible_native_identity_is_safe_and_not_retained(monkeypatch, payload):
    async def exercise():
        stream = TrackingStream((payload,))
        _, clients = transport(monkeypatch, lambda request: httpx2.Response(200, stream=stream))
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        with pytest.raises(OpenCodeServiceError, match="incompatible V2 readiness") as caught:
            await client.connect()
        assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))
        assert stream.closed and clients[0].is_closed and client.metadata is None

    asyncio.run(exercise())


@pytest.mark.parametrize("during_read", [False, True])
def test_transport_failure_has_safe_diagnostic_and_resource_closure(monkeypatch, during_read):
    async def exercise():
        stream = TrackingStream((b'{"version":',), httpx2.ReadTimeout("PRIVATE native failure"))

        def handler(request):
            if during_read:
                return httpx2.Response(200, stream=stream)
            raise httpx2.ConnectError("PRIVATE native failure")

        _, clients = transport(monkeypatch, handler)
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        with pytest.raises(OpenCodeServiceError, match="Cannot establish") as caught:
            await client.connect()
        assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))
        assert clients[0].is_closed and client.metadata is None
        if during_read:
            assert stream.closed

    asyncio.run(exercise())


@pytest.mark.parametrize("connected", [False, True])
def test_protected_work_is_unavailable_before_any_submission(monkeypatch, connected):
    async def exercise():
        requests = []

        def handler(request):
            requests.append(request)
            return httpx2.Response(200, json={"version": "2.1.2", "pid": 42})

        transport(monkeypatch, handler)
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        try:
            if connected:
                await client.connect()
            with pytest.raises(CapabilityUnavailable, match="store exclusion are unqualified"):
                client.require_protected_execution()
        finally:
            await client.close()
        assert [request.method for request in requests] == (["GET"] if connected else [])

    asyncio.run(exercise())


def test_whole_exchange_deadline_stops_a_trickle_before_buffered_output(monkeypatch):
    class Trickle(TrackingStream):
        async def __aiter__(self):
            for chunk in self.chunks:
                await asyncio.sleep(0.01)
                self.reads += 1
                yield chunk

    async def exercise():
        stream = Trickle(tuple(b"x" for _ in range(1000)))
        _, clients = transport(monkeypatch, lambda request: httpx2.Response(200, stream=stream))
        client = OpenCodeServiceClient(
            _ENDPOINT, password=SecretStr(_PASSWORD), timeout_seconds=0.04
        )
        started = time.monotonic()
        with pytest.raises(OpenCodeServiceError, match="Cannot establish"):
            await client.connect()
        assert time.monotonic() - started < 1
        assert 0 < stream.reads < len(stream.chunks)
        assert stream.closed and clients[0].is_closed and client.metadata is None

    asyncio.run(exercise())


@pytest.mark.parametrize("close_fails", [False, True])
def test_cancel_during_context_entry_closes_client_and_preserves_cancellation(
    monkeypatch, close_fails
):
    async def exercise():
        pending = asyncio.Event()
        entered = asyncio.Event()

        async def handler(request):
            entered.set()
            await pending.wait()
            return httpx2.Response(200, json={"version": "2.1.2", "pid": 42})

        _, clients = transport(monkeypatch, handler)
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))

        async def enter():
            async with client:
                pytest.fail("cancelled entry reached its body")

        task = asyncio.create_task(enter())
        await entered.wait()
        if close_fails:
            original_close = clients[0].aclose

            async def broken_close():
                await original_close()
                raise RuntimeError("PRIVATE_CLOSE_CANARY")

            monkeypatch.setattr(clients[0], "aclose", broken_close)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert task.cancelled() and clients[0].is_closed and client.metadata is None
        assert "PRIVATE_CLOSE_CANARY" not in "".join(traceback.format_exception(caught.value))
        if close_fails:
            assert caught.value.__notes__ == ["OpenCode service transport cleanup was not verified"]

    asyncio.run(exercise())


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, SystemExit])
def test_base_exception_during_connection_closes_without_translation(monkeypatch, exception_type):
    async def exercise():
        primary = exception_type()

        def handler(request):
            raise primary

        _, clients = transport(monkeypatch, handler)
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        with pytest.raises(exception_type) as caught:
            await client.connect()
        assert caught.value is primary and clients[0].is_closed and client.metadata is None

    asyncio.run(exercise())


def test_close_failure_is_safe_and_does_not_replace_body_cancellation(monkeypatch):
    async def exercise():
        _, clients = transport(
            monkeypatch, lambda request: httpx2.Response(200, json={"version": "2.1.2", "pid": 42})
        )
        client = OpenCodeServiceClient(_ENDPOINT, password=SecretStr(_PASSWORD))
        primary = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError) as caught:
            async with client:
                original_close = clients[0].aclose

                async def broken_close():
                    await original_close()
                    raise RuntimeError("PRIVATE_CLOSE_CANARY")

                monkeypatch.setattr(clients[0], "aclose", broken_close)
                raise primary
        assert caught.value is primary and clients[0].is_closed
        assert "PRIVATE_CLOSE_CANARY" not in "".join(traceback.format_exception(caught.value))
        assert primary.__notes__ == ["OpenCode service transport cleanup was not verified"]

    asyncio.run(exercise())


@pytest.mark.parametrize("hangs", [False, True])
def test_normal_close_has_a_bounded_safe_failure(monkeypatch, hangs):
    async def exercise():
        _, clients = transport(
            monkeypatch, lambda request: httpx2.Response(200, json={"version": "2.1.2", "pid": 42})
        )
        client = OpenCodeServiceClient(
            _ENDPOINT, password=SecretStr(_PASSWORD), timeout_seconds=0.04
        )
        await client.connect()
        original_close = clients[0].aclose
        entered = asyncio.Event()

        async def broken_close():
            entered.set()
            if hangs:
                await asyncio.Event().wait()
            await original_close()
            raise RuntimeError("PRIVATE_CLOSE_CANARY")

        monkeypatch.setattr(clients[0], "aclose", broken_close)
        started = time.monotonic()
        try:
            with pytest.raises(OpenCodeServiceError, match="cleanup failed") as caught:
                await client.close()
            assert entered.is_set() and time.monotonic() - started < 1
            assert "PRIVATE_CLOSE_CANARY" not in "".join(traceback.format_exception(caught.value))
        finally:
            await original_close()
        assert clients[0].is_closed

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["connect", "read_timeout", "exchange_timeout"])
def test_translated_connection_failure_preserves_sanitized_cleanup_uncertainty(
    monkeypatch, failure
):
    async def exercise():
        stream = TrackingStream((b'{"version":',), httpx2.ReadTimeout("PRIVATE_REQUEST_CANARY"))

        async def handler(request):
            original_close = clients[0].aclose

            async def broken_close():
                await original_close()
                raise RuntimeError("PRIVATE_CLOSE_CANARY")

            monkeypatch.setattr(clients[0], "aclose", broken_close)
            if failure == "connect":
                raise httpx2.ConnectError("PRIVATE_REQUEST_CANARY")
            if failure == "exchange_timeout":
                await asyncio.Event().wait()
            return httpx2.Response(200, stream=stream)

        _, clients = transport(monkeypatch, handler)
        client = OpenCodeServiceClient(
            _ENDPOINT, password=SecretStr(_PASSWORD), timeout_seconds=0.04
        )
        with pytest.raises(OpenCodeServiceError, match="Cannot establish") as caught:
            await client.connect()
        assert caught.value.__notes__ == ["OpenCode service transport cleanup was not verified"]
        diagnostic = "".join(traceback.format_exception(caught.value))
        assert "PRIVATE_REQUEST_CANARY" not in diagnostic
        assert "PRIVATE_CLOSE_CANARY" not in diagnostic and _PASSWORD not in diagnostic
        assert clients[0].is_closed and client.metadata is None
        if failure == "read_timeout":
            assert stream.closed

    asyncio.run(exercise())
