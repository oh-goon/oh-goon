"""Native HTTPx2 WebSocket transport with a local ASGI protocol fixture."""

from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import Any

import anyio
import httpx2
import pytest
from httpx2.websockets import ASGIWebSocketTransport, AsyncWebSocketSession
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.integrations.agent_codex_service import (
    CodexServiceClient,
    NativeCodexCleanupIncomplete,
    NativeCodexRequestUncertain,
)

_INITIAL = {
    "userAgent": "codex_cli_rs/0.163.0 (native) PRIVATE_USER_AGENT_SUFFIX",
    "codexHome": "/PRIVATE_NATIVE_HOME",
    "platformFamily": "unix",
    "platformOs": "linux",
}


def endpoint_transport(
    monkeypatch: pytest.MonkeyPatch,
    endpoint: Callable[[WebSocket], Awaitable[None]],
) -> list[dict[str, Any]]:
    transports: list[dict[str, Any]] = []
    app = Starlette(routes=[WebSocketRoute("/", endpoint)])

    def transport(**kwargs: Any) -> ASGIWebSocketTransport:
        transports.append(kwargs)
        return ASGIWebSocketTransport(app)

    monkeypatch.setattr(
        "oh_goon.integrations.agent_codex_service.httpx2.AsyncHTTPTransport", transport
    )
    return transports


def test_connect_initializes_only_and_never_exposes_native_global_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[dict[str, Any]] = []
    closed: list[bool] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            first = await websocket.receive_json()
            messages.append(first)
            await websocket.send_json(
                {"method": "account/updated", "params": {"private": "PRIVATE_ACCOUNT"}}
            )
            await websocket.send_json({"id": first["id"], "result": _INITIAL})
            messages.append(await websocket.receive_json())
            await websocket.receive_json()
        except WebSocketDisconnect:
            closed.append(True)

    transports = endpoint_transport(monkeypatch, endpoint)
    advertised = tmp_path / "native-advertised.sock"
    advertised.symlink_to(tmp_path / "native-physical-target")

    async def check() -> None:
        client = CodexServiceClient(advertised)
        async with client:
            metadata = client.metadata
            assert metadata["status"] == "connected_not_qualified"
            assert metadata["service_version"] == "0.163.0"
            assert metadata["native_authentication"] == "unverified"
            assert metadata["execution_admitted"] is False
            assert "PRIVATE" not in json.dumps(metadata)
        assert client.metadata["status"] == "not_connected"
        await client.close()

    asyncio.run(check())
    assert [message["method"] for message in messages] == ["initialize", "initialized"]
    assert messages[0]["params"]["capabilities"] == {"experimentalApi": False}
    assert transports == [{"uds": str(advertised), "trust_env": False, "retries": 0}]
    assert closed == [True]


def test_explicit_native_profiles_and_continuation_remain_on_the_selected_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[dict[str, Any]] = []
    profile = "frozen-review"
    thread = "owned-thread"

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                messages.append(message)
                method = message["method"]
                if method == "initialized":
                    continue
                result: dict[str, Any] = {}
                if method == "initialize":
                    result = _INITIAL
                elif method == "permissionProfile/list":
                    result = {"data": [{"id": profile, "allowed": True}], "nextCursor": None}
                elif method == "command/exec":
                    result = {"exitCode": 0, "stdout": "SYNTHETIC_DENIAL_PROOF", "stderr": ""}
                elif method in {"thread/start", "thread/resume"}:
                    result = {"thread": {"id": thread}, "activePermissionProfile": {"id": profile}}
                elif method == "turn/start":
                    await websocket.send_json(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": "foreign-thread",
                                "delta": "PRIVATE_FOREIGN_DATA",
                            },
                        }
                    )
                    await websocket.send_json(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {"threadId": thread, "delta": "owned progress"},
                        }
                    )
                    result = {"turn": {"id": "turn-1"}}
                elif method == "thread/compact/start":
                    await websocket.send_json(
                        {"method": "thread/compacted", "params": {"threadId": thread}}
                    )
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            profiles = await client.list_permission_profiles(cwd=tmp_path)
            assert profiles["data"] == [{"id": profile, "allowed": True}]
            proof = await client.command_exec(("/native/probe",), cwd=tmp_path, permissions=profile)
            assert proof["exitCode"] == 0
            result = await client.start_thread(
                cwd=tmp_path,
                permissions=profile,
                model="gpt-6.1-sol",
                developer_instructions="frozen Hashmarks and Skill context",
            )
            assert result["thread"]["id"] == thread
            await client.start_turn(
                thread, "full initial task", permissions=profile, effort="ultra"
            )
            event = await client.next_notification(thread)
            assert event["params"]["delta"] == "owned progress"
            await client.compact_thread(thread)
            assert (await client.next_notification(thread))["method"] == "thread/compacted"
            await client.start_turn(thread, "follow-up task", permissions=profile)
            await client.next_notification(thread)
            await client.interrupt_turn(thread, "turn-1")
            with pytest.raises(AgentProviderError, match="cannot change"):
                await client.start_turn(
                    thread, "forbidden escalation", permissions=":danger-full-access"
                )
            with pytest.raises(AgentProviderError, match="selected thread"):
                await client.next_notification("foreign-thread")
            before = len(messages)
            with pytest.raises(AgentProviderError, match="cannot replace"):
                await client.resume_thread("foreign-thread", permissions=profile)
            with pytest.raises(AgentProviderError, match="cannot replace"):
                await client.resume_thread(thread, permissions=":danger-full-access")
            with pytest.raises(AgentProviderError, match="cannot replace"):
                await client.resume_thread(thread, permissions=profile, configuration={})
            assert len(messages) == before
            with pytest.raises(AgentProviderError, match="fresh native"):
                await client.start_thread(
                    cwd=tmp_path,
                    permissions=profile,
                    model="native",
                    developer_instructions="fresh phase",
                )
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            assert (await client.resume_thread(thread, permissions=profile))["thread"][
                "id"
            ] == thread

    asyncio.run(check())
    turns = [message for message in messages if message["method"] == "turn/start"]
    assert [message["params"]["input"] for message in turns] == [
        [{"type": "text", "text": "full initial task"}],
        [{"type": "text", "text": "follow-up task"}],
    ]
    assert turns[0]["params"]["effort"] == "ultra"
    start = next(message for message in messages if message["method"] == "thread/start")
    assert start["params"]["developerInstructions"] == "frozen Hashmarks and Skill context"
    assert start["params"]["permissions"] == profile and "sandbox" not in start["params"]


@pytest.mark.parametrize(
    "fault",
    [
        "error",
        "stale",
        "duplicate",
        "malformed",
        "non-object",
        "timeout",
        "interaction",
        "oversized",
    ],
)
def test_invalid_native_exchange_fails_without_private_payloads_or_leaking_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    decisions: list[dict[str, Any]] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        message = await websocket.receive_json()
        identifier = message["id"]
        try:
            if fault == "error":
                await websocket.send_json(
                    {"id": identifier, "error": {"message": "PRIVATE_NATIVE_ERROR"}}
                )
            elif fault == "stale":
                await websocket.send_json({"id": identifier + 1, "result": _INITIAL})
            elif fault == "duplicate":
                await websocket.send_text('{"id":1,"id":1,"result":{"PRIVATE":true}}')
            elif fault == "malformed":
                await websocket.send_text("PRIVATE_INVALID_JSON")
            elif fault == "non-object":
                await websocket.send_json(["PRIVATE_NON_OBJECT"])
            elif fault == "timeout":
                await websocket.receive_json()
            elif fault == "interaction":
                await websocket.send_json(
                    {
                        "id": 90,
                        "method": "item/commandExecution/requestApproval",
                        "params": {"private": "PRIVATE_APPROVAL"},
                    }
                )
                decisions.append(await websocket.receive_json())
            else:
                await websocket.send_text("PRIVATE" * 40000)
            await websocket.receive_json()
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        client = CodexServiceClient(tmp_path / "explicit.sock", timeout_seconds=0.03)
        with pytest.raises(AgentProviderError) as error:
            async with asyncio.timeout(1):
                await client.connect()
        assert "PRIVATE" not in str(error.value)
        if fault != "error":
            assert isinstance(error.value, NativeCodexRequestUncertain)
            assert error.value.retryable is False
        assert client.metadata["status"] == "not_connected"
        await client.close()

    asyncio.run(check())
    if fault == "interaction":
        assert decisions == [
            {"id": 90, "error": {"code": -32601, "message": "Client interaction is not supported"}}
        ]


@pytest.mark.parametrize("mismatch", ["profile", "thread"])
def test_native_profile_fallback_or_wrong_resume_identity_is_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    methods: list[str] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                method = message["method"]
                methods.append(method)
                if method == "initialized":
                    continue
                result = (
                    _INITIAL
                    if method == "initialize"
                    else {
                        "thread": {"id": "different" if mismatch == "thread" else "owned"},
                        "activePermissionProfile": {
                            "id": ":read-only" if mismatch == "profile" else "admitted"
                        },
                    }
                )
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            with pytest.raises(AgentProviderError):
                await client.resume_thread("owned", permissions="admitted")

    asyncio.run(check())
    assert methods == ["initialize", "initialized", "thread/resume"]


@pytest.mark.parametrize(
    "invalid", [Path("relative.sock"), Path("/tmp/../private.sock"), Path("/PRIVATE\n.sock")]
)
def test_socket_selection_is_explicit_and_lexical(invalid: Path) -> None:
    with pytest.raises(AgentProviderError):
        CodexServiceClient(invalid)


def test_handshake_failure_is_redacted_without_reading_native_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed_transport(**_kwargs: Any) -> ASGIWebSocketTransport:
        raise httpx2.ConnectError("PRIVATE_NATIVE_SOCKET_PATH")

    monkeypatch.setattr(
        "oh_goon.integrations.agent_codex_service.httpx2.AsyncHTTPTransport", failed_transport
    )

    async def check() -> None:
        client = CodexServiceClient(tmp_path / "explicit.sock")
        with pytest.raises(AgentProviderError) as error:
            await client.connect()
        assert "PRIVATE" not in str(error.value)
        assert client.metadata["status"] == "not_connected"
        await client.close()

    asyncio.run(check())


def test_native_send_backpressure_has_the_same_nonretryable_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    methods: list[str] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                methods.append(message["method"])
                if message["method"] == "initialize":
                    await websocket.send_json({"id": message["id"], "result": _INITIAL})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)
    original_send = AsyncWebSocketSession.send_text

    async def bounded_send(session: AsyncWebSocketSession, data: str) -> None:
        if json.loads(data)["method"] == "command/exec":
            await asyncio.Event().wait()
        else:
            await original_send(session, data)

    monkeypatch.setattr(AsyncWebSocketSession, "send_text", bounded_send)

    async def check() -> None:
        async with CodexServiceClient(
            tmp_path / "explicit.sock", experimental_api=True, timeout_seconds=0.03
        ) as client:
            async with asyncio.timeout(1):
                with pytest.raises(NativeCodexRequestUncertain) as error:
                    await client.command_exec(
                        ("/native/qualification",),
                        cwd=tmp_path,
                        permissions="review",
                        timeout_seconds=0.02,
                    )
            assert error.value.retryable is False

    asyncio.run(check())
    assert methods == ["initialize", "initialized"]


def test_cancelled_initialize_preserves_cancellation_and_closes_owned_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialized = asyncio.Event()
    disconnected: list[bool] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.receive_json()
        initialized.set()
        try:
            await websocket.receive_json()
        except WebSocketDisconnect:
            disconnected.append(True)

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        client = CodexServiceClient(tmp_path / "explicit.sock")
        task = asyncio.create_task(client.connect())
        await initialized.wait()
        task.cancel()
        async with asyncio.timeout(1):
            with pytest.raises(asyncio.CancelledError):
                await task
        assert client.metadata["status"] == "not_connected"
        await client.close()

    asyncio.run(check())
    assert disconnected == [True]


def test_websocket_upgrade_is_inside_the_total_connection_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reached: list[bool] = []
    original = httpx2.AsyncClient.websocket

    @asynccontextmanager
    async def slow_upgrade(
        client: httpx2.AsyncClient, *args: Any, **kwargs: Any
    ) -> AsyncIterator[AsyncWebSocketSession]:
        reached.append(True)
        await asyncio.sleep(1)
        async with original(client, *args, **kwargs) as session:
            yield session

    monkeypatch.setattr(httpx2.AsyncClient, "websocket", slow_upgrade)

    async def check() -> None:
        client = CodexServiceClient(tmp_path / "explicit.sock", timeout_seconds=0.03)
        started = monotonic()
        with pytest.raises(AgentProviderError, match="connection is unavailable"):
            await client.connect()
        assert monotonic() - started < 0.5
        assert client.metadata["transport_cleanup"] == "complete"

    asyncio.run(check())
    assert reached == [True]


@pytest.mark.parametrize("primary", ["none", "safe", "cancel"])
@pytest.mark.parametrize("failure", ["error", "shielded-hang"])
def test_teardown_is_bounded_redacted_and_does_not_replace_the_primary_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    primary: str,
    failure: str,
) -> None:
    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        message = await websocket.receive_json()
        await websocket.send_json({"id": message["id"], "result": _INITIAL})
        try:
            while True:
                await websocket.receive_json()
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        client = CodexServiceClient(tmp_path / "explicit.sock", timeout_seconds=0.03)
        started = monotonic()
        caught: BaseException | None = None
        try:
            async with client:
                assert client._stack is not None
                original_close = client._stack.aclose

                async def failed_close() -> None:
                    await original_close()
                    if failure == "error":
                        raise OSError("PRIVATE_CLOSE_CANARY")
                    with anyio.CancelScope(shield=True):
                        await asyncio.Event().wait()

                monkeypatch.setattr(client._stack, "aclose", failed_close)
                if primary == "safe":
                    raise AgentProviderError("safe primary failure")
                if primary == "cancel":
                    raise asyncio.CancelledError()
        except BaseException as error:
            caught = error
        assert caught is not None
        assert monotonic() - started < 0.5
        assert "PRIVATE_CLOSE_CANARY" not in str(caught)
        assert "PRIVATE_CLOSE_CANARY" not in "".join(traceback.format_exception(caught))
        assert caught.__cause__ is None
        assert client.metadata["transport_cleanup"] == "incomplete"
        if primary == "none":
            assert isinstance(caught, NativeCodexCleanupIncomplete)
        elif primary == "safe":
            assert type(caught) is AgentProviderError and str(caught) == "safe primary failure"
            assert caught.__notes__ == ["Native Codex transport cleanup is incomplete"]
        else:
            assert isinstance(caught, asyncio.CancelledError)
            assert caught.__notes__ == ["Native Codex transport cleanup is incomplete"]
        await client.close()

    asyncio.run(check())
