"""Native HTTPx2 WebSocket transport with a local ASGI protocol fixture."""

from __future__ import annotations

import asyncio
import json
import sys
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
from oh_goon.core.models.agent_review import AgentAdjudicationRecord
from oh_goon.core.process_birth import observe_process_birth
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


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native peer birth identity")
def test_expected_peer_refuses_unknown_transport_before_native_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    methods: list[str] = []
    closed: list[bool] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                methods.append(message["method"])
                if "id" in message:
                    await websocket.send_json({"id": message["id"], "result": _INITIAL})
        except WebSocketDisconnect:
            closed.append(True)

    endpoint_transport(monkeypatch, endpoint)
    expected = observe_process_birth()
    assert expected is not None

    async def check() -> None:
        advertised = tmp_path / "explicit.sock"
        async with CodexServiceClient(advertised) as readiness:
            assert readiness.metadata["status"] == "connected_not_qualified"
        assert methods == ["initialize", "initialized"]
        methods.clear()
        guarded = CodexServiceClient(advertised, expected_process=expected)
        with pytest.raises(AgentProviderError):
            await guarded.connect()
        assert methods == []
        assert guarded.metadata["status"] == "not_connected"
        assert guarded.metadata["transport_cleanup"] == "complete"
        await guarded.close()

    asyncio.run(check())
    assert closed == [True, True]


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
                        {
                            "method": "item/completed",
                            "params": {
                                "threadId": thread,
                                "turnId": "compaction-turn",
                                "item": {"id": "compact-item", "type": "contextCompaction"},
                            },
                        }
                    )
                    await websocket.send_json(
                        {
                            "method": "turn/completed",
                            "params": {
                                "threadId": thread,
                                "turn": {"id": "compaction-turn", "status": "completed"},
                            },
                        }
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
                thread,
                "full initial task",
                permissions=profile,
                effort="ultra",
                output_schema=AgentAdjudicationRecord.model_json_schema(by_alias=True),
            )
            event = await client.next_notification(thread)
            assert event["params"]["delta"] == "owned progress"
            await client.resume_thread(thread, permissions=profile)
            await client.compact_thread(thread)
            compacted = await client.next_notification(thread)
            assert compacted["method"] == "item/completed"
            assert compacted["params"]["item"]["type"] == "contextCompaction"
            assert (await client.next_notification(thread))["params"]["turn"][
                "status"
            ] == "completed"
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
            with pytest.raises(AgentProviderError, match="cannot replace"):
                await client.resume_thread(
                    thread, permissions=profile, developer_instructions="different context"
                )
            assert len(messages) == before
            with pytest.raises(AgentProviderError, match="fresh native"):
                await client.start_thread(
                    cwd=tmp_path,
                    permissions=profile,
                    model="native",
                    developer_instructions="fresh phase",
                )
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            before = len(messages)
            with pytest.raises(AgentProviderError, match="original frozen instructions"):
                await client.resume_thread(thread, permissions=profile)
            assert len(messages) == before
            assert (
                await client.resume_thread(
                    thread,
                    permissions=profile,
                    developer_instructions="frozen Hashmarks and Skill context",
                )
            )["thread"]["id"] == thread

    asyncio.run(check())
    turns = [message for message in messages if message["method"] == "turn/start"]
    assert [message["params"]["input"] for message in turns] == [
        [{"type": "text", "text": "full initial task"}],
        [{"type": "text", "text": "follow-up task"}],
    ]
    assert turns[0]["params"]["effort"] == "ultra"
    assert turns[0]["params"]["outputSchema"] == AgentAdjudicationRecord.model_json_schema(
        by_alias=True
    )
    assert turns[1]["params"]["outputSchema"] is None
    start = next(message for message in messages if message["method"] == "thread/start")
    assert start["params"]["developerInstructions"] == "frozen Hashmarks and Skill context"
    assert start["params"]["permissions"] == profile and "sandbox" not in start["params"]
    assert start["params"]["environments"] == [
        {"environmentId": "local", "cwd": str(tmp_path), "runtimeWorkspaceRoots": [str(tmp_path)]}
    ]
    assert start["params"]["selectedCapabilityRoots"] == []
    resumes = [message for message in messages if message["method"] == "thread/resume"]
    assert len(resumes) == 2
    assert all(
        message["params"]["developerInstructions"] == start["params"]["developerInstructions"]
        for message in resumes
    )


def _mcp_registration(name: str = "PRIVATE_OPAQUE_REGISTRATION") -> dict[str, Any]:
    return {
        "name": name,
        "pluginId": None,
        "runtimeStatus": "disabled",
        "tools": {},
        "resources": [],
        "resourceTemplates": [],
        "toolsError": None,
        "serverInfo": {"description": "PRIVATE_NATIVE_INVENTORY"},
    }


def test_overlapping_native_connections_keep_late_events_and_selection_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[tuple[int, dict[str, Any]]] = []
    connections = 0
    release_old = asyncio.Event()
    # Native thread IDs may coincide at different explicitly selected endpoints.
    thread = "native-thread"

    async def endpoint(websocket: WebSocket) -> None:
        nonlocal connections
        connections += 1
        generation = connections
        profile = f"profile-{generation}"
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                messages.append((generation, message))
                method = message["method"]
                if method == "initialized":
                    continue
                result: dict[str, Any] = {}
                if method == "initialize":
                    result = _INITIAL
                elif method in {"thread/start", "thread/resume"}:
                    result = {
                        "thread": {"id": thread},
                        "activePermissionProfile": {"id": profile},
                    }
                elif method == "turn/start":
                    result = {"turn": {"id": f"turn-{generation}"}}
                await websocket.send_json({"id": message["id"], "result": result})
                if method == "turn/start":
                    if generation == 1:
                        await asyncio.wait_for(release_old.wait(), timeout=5)
                    await websocket.send_json(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": "foreign-thread",
                                "delta": "PRIVATE_FOREIGN_PROGRESS",
                            },
                        }
                    )
                    await websocket.send_json(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": thread,
                                "turnId": f"turn-{generation}",
                                "delta": f"generation-{generation}-progress",
                            },
                        }
                    )
        except WebSocketDisconnect:
            pass

    transports = endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        old_selected = asyncio.Event()
        new_selected = asyncio.Event()
        new_consumed = asyncio.Event()
        old_closed = asyncio.Event()

        async def original_owner() -> None:
            async with CodexServiceClient(tmp_path / "old.sock", experimental_api=True) as old:
                await old.start_thread(
                    cwd=tmp_path,
                    permissions="profile-1",
                    model="original-model",
                    developer_instructions="original task, Skill and evidence",
                )
                await old.start_turn(thread, "original task", permissions="profile-1")
                original_metadata = old.metadata
                old_selected.set()
                await new_selected.wait()
                old_event = await old.next_notification(thread)
                assert old_event["params"]["delta"] == "generation-1-progress"
                assert "PRIVATE" not in json.dumps(old_event)
                assert old.metadata == original_metadata
                before = len(messages)
                with pytest.raises(AgentProviderError, match="cannot replace"):
                    await old.resume_thread(thread, permissions="profile-2")
                assert len(messages) == before
                await old.resume_thread(thread, permissions="profile-1")
                await new_consumed.wait()
            old_closed.set()

        async def replacement_owner() -> None:
            await old_selected.wait()
            async with CodexServiceClient(tmp_path / "new.sock", experimental_api=True) as new:
                await new.start_thread(
                    cwd=tmp_path,
                    permissions="profile-2",
                    model="new-model",
                    developer_instructions="new task, Skill and evidence",
                )
                await new.start_turn(thread, "new task", permissions="profile-2")
                new_metadata = new.metadata
                new_selected.set()
                release_old.set()
                new_event = await new.next_notification(thread)
                assert new_event["params"]["delta"] == "generation-2-progress"
                assert "PRIVATE" not in json.dumps(new_event)
                before = len(messages)
                with pytest.raises(AgentProviderError, match="cannot replace"):
                    await new.resume_thread(
                        thread,
                        permissions="profile-2",
                        developer_instructions="original task, Skill and evidence",
                    )
                assert len(messages) == before
                await new.resume_thread(thread, permissions="profile-2")
                new_consumed.set()
                await old_closed.wait()
                assert new.metadata == new_metadata
                await new.start_turn(thread, "new follow-up", permissions="profile-2")
                assert (await new.next_notification(thread))["params"][
                    "delta"
                ] == "generation-2-progress"

        async with asyncio.timeout(5):
            await asyncio.gather(original_owner(), replacement_owner())

    asyncio.run(check())
    assert connections == 2
    assert [item["uds"] for item in transports] == [
        str(tmp_path / "old.sock"),
        str(tmp_path / "new.sock"),
    ]
    starts = [
        (generation, message["params"])
        for generation, message in messages
        if message["method"] == "thread/start"
    ]
    assert [
        (generation, params["model"], params["permissions"]) for generation, params in starts
    ] == [
        (1, "original-model", "profile-1"),
        (2, "new-model", "profile-2"),
    ]
    resumes = [
        (generation, message["params"])
        for generation, message in messages
        if message["method"] == "thread/resume"
    ]
    assert sorted(
        (generation, params["developerInstructions"]) for generation, params in resumes
    ) == [
        (1, "original task, Skill and evidence"),
        (2, "new task, Skill and evidence"),
    ]


@pytest.mark.parametrize("names", [(), ("opaque.native",), ('quoted."native"', "other/native")])
def test_native_mcp_exclusion_uses_one_threadless_snapshot_and_one_exact_disabled_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> None:
    messages: list[dict[str, Any]] = []
    snapshots = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                messages.append(message)
                method = message["method"]
                if method == "initialized":
                    continue
                result: dict[str, Any]
                if method == "initialize":
                    result = _INITIAL
                elif method == "mcpServerStatus/list":
                    snapshots.append(message["params"])
                    selected = names if len(snapshots) == 1 else tuple(reversed(names))
                    result = {
                        "data": [_mcp_registration(name) for name in selected],
                        "nextCursor": None,
                    }
                elif method == "thread/start":
                    assert len(snapshots) == 1
                    assert message["params"]["config"] == {
                        "mcp_servers": {name: {"enabled": False} for name in names}
                    }
                    result = {
                        "thread": {"id": "owned"},
                        "activePermissionProfile": {"id": "review"},
                    }
                else:
                    assert method == "turn/start" and len(snapshots) == 2
                    result = {"turn": {"id": "owned-turn"}}
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            configuration = await client.mcp_exclusion_configuration()
            assert configuration == {"mcp_servers": {name: {"enabled": False} for name in names}}
            before = len(messages)
            with pytest.raises(AgentProviderError, match="one pre-thread snapshot"):
                await client.mcp_exclusion_configuration()
            assert len(messages) == before
            await client.start_thread(
                cwd=tmp_path,
                permissions="review",
                model="native",
                developer_instructions="Frozen task and producer evidence",
                configuration=configuration,
            )
            await client.require_mcp_servers_excluded("owned")
            before = len(messages)
            with pytest.raises(AgentProviderError, match="original snapshot"):
                await client.require_mcp_servers_excluded("owned")
            assert len(messages) == before
            await client.start_turn("owned", "Captured task", permissions="review")
            assert "PRIVATE" not in json.dumps(client.metadata)

    asyncio.run(check())
    assert snapshots == [
        {"threadId": thread, "cursor": None, "limit": 64, "detail": "toolsAndAuthOnly"}
        for thread in (None, "owned")
    ]
    assert [message["method"] for message in messages] == [
        "initialize",
        "initialized",
        "mcpServerStatus/list",
        "thread/start",
        "mcpServerStatus/list",
        "turn/start",
    ]


@pytest.mark.parametrize("stage", ("discovery", "verification"))
@pytest.mark.parametrize(
    "fault",
    (
        "missing_data",
        "invalid_data",
        "invalid_entry",
        "missing_cursor",
        "pagination",
        "overflow",
        "duplicate",
        "invalid_identifier",
        "missing_origin",
        "plugin_origin",
    ),
)
def test_incomplete_or_unaddressable_native_mcp_inventory_fails_without_model_or_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, fault: str
) -> None:
    messages: list[str] = []
    snapshot_count = 0
    bad: dict[str, Any] = {"data": [_mcp_registration()], "nextCursor": None}
    if fault == "missing_data":
        bad.pop("data")
    elif fault == "invalid_data":
        bad["data"] = {}
    elif fault == "invalid_entry":
        bad["data"] = [None]
    elif fault == "missing_cursor":
        bad.pop("nextCursor")
    elif fault == "pagination":
        bad["nextCursor"] = "PRIVATE_NATIVE_CURSOR"
    elif fault == "overflow":
        bad["data"] = [_mcp_registration(f"PRIVATE_{index}") for index in range(65)]
    elif fault == "duplicate":
        bad["data"].append(_mcp_registration())
    elif fault == "invalid_identifier":
        bad["data"][0]["name"] = "PRIVATE\nINVALID"
    elif fault == "missing_origin":
        bad["data"][0].pop("pluginId")
    else:
        bad["data"][0]["pluginId"] = "PRIVATE_PLUGIN_ORIGIN"

    async def endpoint(websocket: WebSocket) -> None:
        nonlocal snapshot_count
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                method = message["method"]
                messages.append(method)
                if method == "initialized":
                    continue
                result: dict[str, Any]
                if method == "initialize":
                    result = _INITIAL
                elif method == "mcpServerStatus/list":
                    snapshot_count += 1
                    result = (
                        bad
                        if stage == "discovery" or snapshot_count == 2
                        else {
                            "data": [_mcp_registration()],
                            "nextCursor": None,
                        }
                    )
                else:
                    assert method == "thread/start"
                    result = {
                        "thread": {"id": "owned"},
                        "activePermissionProfile": {"id": "review"},
                    }
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            with pytest.raises(AgentProviderError) as error:
                configuration = await client.mcp_exclusion_configuration()
                await client.start_thread(
                    cwd=tmp_path,
                    permissions="review",
                    model="native",
                    configuration=configuration,
                    developer_instructions="Frozen task and producer evidence",
                )
                await client.require_mcp_servers_excluded("owned")
            assert "PRIVATE" not in str(error.value)

    asyncio.run(check())
    assert snapshot_count == (1 if stage == "discovery" else 2)
    assert messages.count("thread/start") == (stage == "verification")
    assert "turn/start" not in messages


@pytest.mark.parametrize(
    "fault",
    ("active", "unknown_status", "tools", "resources", "templates", "tool_error", "new", "removed"),
)
def test_workload_mcp_capabilities_must_be_disabled_with_exact_frozen_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    methods: list[str] = []
    snapshots = 0
    changed = [_mcp_registration()]
    if fault in ("active", "unknown_status"):
        changed[0]["runtimeStatus"] = "connected" if fault == "active" else None
    elif fault == "tools":
        changed[0]["tools"] = {"PRIVATE_TOOL": {}}
    elif fault == "resources":
        changed[0]["resources"] = [{"PRIVATE_RESOURCE": True}]
    elif fault == "templates":
        changed[0]["resourceTemplates"] = [{"PRIVATE_TEMPLATE": True}]
    elif fault == "tool_error":
        changed[0]["toolsError"] = "PRIVATE_DISCOVERY_ERROR"
    elif fault == "new":
        changed.append(_mcp_registration("PRIVATE_ADDED"))
    else:
        changed = []

    async def endpoint(websocket: WebSocket) -> None:
        nonlocal snapshots
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                method = message["method"]
                methods.append(method)
                if method == "initialized":
                    continue
                result: dict[str, Any]
                if method == "initialize":
                    result = _INITIAL
                elif method == "thread/start":
                    result = {
                        "thread": {"id": "owned"},
                        "activePermissionProfile": {"id": "review"},
                    }
                else:
                    assert method == "mcpServerStatus/list"
                    snapshots += 1
                    result = {
                        "data": [_mcp_registration()] if snapshots == 1 else changed,
                        "nextCursor": None,
                    }
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True) as client:
            configuration = await client.mcp_exclusion_configuration()
            await client.start_thread(
                cwd=tmp_path,
                permissions="review",
                model="native",
                configuration=configuration,
                developer_instructions="Frozen task and producer evidence",
            )
            with pytest.raises(AgentProviderError) as error:
                await client.require_mcp_servers_excluded("owned")
            assert "PRIVATE" not in str(error.value)

    asyncio.run(check())
    assert snapshots == 2 and "turn/start" not in methods


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
    methods: list[str] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        message = await websocket.receive_json()
        methods.append(message["method"])
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
        client = CodexServiceClient(tmp_path / "explicit.sock", timeout_seconds=1)
        with pytest.raises(AgentProviderError) as error:
            async with asyncio.timeout(3):
                await client.connect()
        assert "PRIVATE" not in str(error.value)
        if fault != "error":
            assert isinstance(error.value, NativeCodexRequestUncertain)
            assert error.value.retryable is False
        assert client.metadata["status"] == "not_connected"
        await client.close()

    asyncio.run(check())
    assert methods == ["initialize"]
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
                await client.resume_thread(
                    "owned", permissions="admitted", developer_instructions="frozen"
                )

    asyncio.run(check())
    assert methods == ["initialize", "initialized", "thread/resume"]


def test_lost_turn_response_is_not_replayed_or_inherited_after_native_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[tuple[int, dict[str, Any]]] = []
    connections = 0

    async def endpoint(websocket: WebSocket) -> None:
        nonlocal connections
        connections += 1
        generation = connections
        thread = f"owned-generation-{generation}"
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                messages.append((generation, message))
                method = message["method"]
                if method == "initialized":
                    continue
                result: dict[str, Any]
                if method == "initialize":
                    result = {
                        **_INITIAL,
                        "userAgent": f"codex_cli_rs/0.16{generation + 2}.0 (native)",
                    }
                elif method == "thread/start":
                    result = {
                        "thread": {"id": thread},
                        "activePermissionProfile": {"id": "admitted"},
                    }
                else:
                    assert method == "turn/start"
                    await websocket.send_json(
                        {
                            "method": "item/agentMessage/delta",
                            "params": {
                                "threadId": thread,
                                "delta": f"generation {generation} progress",
                            },
                        }
                    )
                    if generation == 1:
                        # The native service accepted the request but its turn
                        # response was lost. Replaying could duplicate work.
                        await websocket.close()
                        return
                    result = {"turn": {"id": "new-generation-turn"}}
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        client = CodexServiceClient(tmp_path / "explicit.sock", experimental_api=True)
        async with client:
            initial_metadata = client.metadata
            assert initial_metadata["service_version"] == "0.163.0"
            await client.start_thread(
                cwd=tmp_path,
                permissions="admitted",
                model="native-model",
                developer_instructions="original frozen context",
            )
            with pytest.raises(NativeCodexRequestUncertain) as error:
                await client.start_turn(
                    "owned-generation-1", "original task", permissions="admitted"
                )
            assert error.value.retryable is False
        assert client.metadata["transport_cleanup"] == "complete"

        async with client:
            restarted_metadata = client.metadata
            assert restarted_metadata["service_version"] == "0.164.0"
            before = len(messages)
            with pytest.raises(AgentProviderError, match="selected thread"):
                await client.start_turn(
                    "owned-generation-1", "unsafe replay", permissions="admitted"
                )
            with pytest.raises(AgentProviderError, match="selected thread"):
                await client.next_notification("owned-generation-1")
            with pytest.raises(AgentProviderError, match="original frozen instructions"):
                await client.resume_thread("owned-generation-1", permissions="admitted")
            assert len(messages) == before
            await client.start_thread(
                cwd=tmp_path,
                permissions="admitted",
                model="native-model",
                developer_instructions="separately selected frozen context",
            )
            result = await client.start_turn(
                "owned-generation-2", "new task", permissions="admitted"
            )
            assert result["turn"]["id"] == "new-generation-turn"
            notification = await client.next_notification("owned-generation-2")
            assert notification["params"]["delta"] == "generation 2 progress"

    asyncio.run(check())
    assert connections == 2
    turns = [
        (generation, message)
        for generation, message in messages
        if message["method"] == "turn/start"
    ]
    assert [(generation, message["params"]["input"]) for generation, message in turns] == [
        (1, [{"type": "text", "text": "original task"}]),
        (2, [{"type": "text", "text": "new task"}]),
    ]
    assert all(message["method"] != "thread/resume" for _, message in messages)


def test_refused_model_turn_preserves_the_selected_native_session_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages: list[dict[str, Any]] = []
    turns = 0

    async def endpoint(websocket: WebSocket) -> None:
        nonlocal turns
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                messages.append(message)
                method = message["method"]
                if method == "initialized":
                    continue
                if method == "turn/start":
                    turns += 1
                    if turns == 1:
                        # The protocol declares no typed model-vs-thread absence
                        # distinction. A refusal must not be reinterpreted as
                        # native session loss or permission to choose a model.
                        await websocket.send_json(
                            {
                                "id": message["id"],
                                "error": {
                                    "code": -32000,
                                    "message": "PRIVATE_PROVIDER_REFUSAL",
                                    "data": {"private": "PRIVATE_NATIVE_CONTEXT"},
                                },
                            }
                        )
                        continue
                    result: dict[str, Any] = {"turn": {"id": "explicit-later-turn"}}
                elif method == "initialize":
                    result = _INITIAL
                else:
                    assert method in {"thread/start", "thread/resume"}
                    result = {
                        "thread": {"id": "original-thread"},
                        "activePermissionProfile": {"id": "original-profile"},
                    }
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "model.sock", experimental_api=True) as client:
            await client.start_thread(
                cwd=tmp_path,
                permissions="original-profile",
                model="original-model",
                developer_instructions="original frozen task, Skill and evidence",
            )
            with pytest.raises(AgentProviderError, match="refused the request") as refused:
                await client.start_turn(
                    "original-thread", "original task", permissions="original-profile"
                )
            assert "PRIVATE" not in str(refused.value)
            assert not isinstance(refused.value, NativeCodexRequestUncertain)
            assert turns == 1
            # Explicit native resume confirms the session still exists. The
            # original context must survive the prior model request refusal.
            await client.resume_thread("original-thread", permissions="original-profile")
            accepted = await client.start_turn(
                "original-thread", "explicit later task", permissions="original-profile"
            )
            assert accepted["turn"]["id"] == "explicit-later-turn"
            assert client.metadata["execution_admitted"] is False
        before = len(messages)
        async with CodexServiceClient(tmp_path / "cold.sock", experimental_api=True) as cold:
            connected = len(messages)
            with pytest.raises(AgentProviderError, match="original frozen instructions"):
                await cold.resume_thread("original-thread", permissions="original-profile")
            assert len(messages) == connected
        assert len(messages) == before + 2  # Only initialization, no native resume.

    asyncio.run(check())
    assert [message["method"] for message in messages] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
        "thread/resume",
        "turn/start",
        "initialize",
        "initialized",
    ]
    start = messages[2]["params"]
    assert start["model"] == "original-model" and start["allowProviderModelFallback"] is False
    assert messages[4]["params"]["developerInstructions"] == start["developerInstructions"]


@pytest.mark.parametrize(
    "invalid", [Path("relative.sock"), Path("/tmp/../private.sock"), Path("/PRIVATE\n.sock")]
)
def test_socket_selection_is_explicit_and_lexical(invalid: Path) -> None:
    with pytest.raises(AgentProviderError):
        CodexServiceClient(invalid)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux AF_UNIX pathname transport")
@pytest.mark.parametrize("name", ["a" * 107, "é" * 53 + "a"])
def test_linux_socket_rejects_oversized_encoded_path_before_transport(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_transport(**_kwargs: Any) -> ASGIWebSocketTransport:
        pytest.fail("an inadmissible socket must not create a transport")

    monkeypatch.setattr(
        "oh_goon.integrations.agent_codex_service.httpx2.AsyncHTTPTransport",
        unexpected_transport,
    )
    with pytest.raises(AgentProviderError, match="107-byte") as failure:
        CodexServiceClient(Path("/" + name))
    assert len(str(failure.value)) < 200
    assert name not in str(failure.value)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux AF_UNIX pathname transport")
@pytest.mark.parametrize("name", ["a" * 106, "é" * 53])
def test_linux_socket_accepts_exact_encoded_path_limit(name: str) -> None:
    client = CodexServiceClient(Path("/" + name))
    assert client.metadata["status"] == "not_connected"


def test_socket_byte_limit_does_not_restrict_native_home_or_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_home = Path("/" + "h" * 200)
    working_directory = Path("/" + "w" * 200)

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            request = await websocket.receive_json()
            await websocket.send_json(
                {"id": request["id"], "result": {**_INITIAL, "codexHome": str(native_home)}}
            )
            assert (await websocket.receive_json())["method"] == "initialized"
            request = await websocket.receive_json()
            assert request["params"]["cwd"] == str(working_directory)
            await websocket.send_json({"id": request["id"], "result": {"data": []}})
            await websocket.receive_json()
        except WebSocketDisconnect:
            pass

    endpoint_transport(monkeypatch, endpoint)

    async def check() -> None:
        async with CodexServiceClient(Path("/tmp/owned.sock"), experimental_api=True) as client:
            client.require_native_home_separation((working_directory,), expected_home=native_home)
            assert (await client.list_permission_profiles(cwd=working_directory))["data"] == []

    asyncio.run(check())


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
    blocked_commands: list[dict[str, Any]] = []

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
        message = json.loads(data)
        if message["method"] == "command/exec":
            blocked_commands.append(message)
            await asyncio.Event().wait()
        else:
            await original_send(session, data)

    monkeypatch.setattr(AsyncWebSocketSession, "send_text", bounded_send)

    async def check() -> None:
        async with CodexServiceClient(
            tmp_path / "explicit.sock", experimental_api=True, timeout_seconds=1
        ) as client:
            async with asyncio.timeout(3):
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
    assert len(blocked_commands) == 1
    assert blocked_commands[0]["params"]["timeoutMs"] == 20


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
        client = CodexServiceClient(tmp_path / "explicit.sock")
        started: float | None = None
        fault_entered = False
        caught: BaseException | None = None
        try:
            async with client:
                assert client._stack is not None
                original_close = client._stack.aclose

                async def failed_close() -> None:
                    nonlocal fault_entered
                    await original_close()
                    fault_entered = True
                    if failure == "error":
                        raise OSError("PRIVATE_CLOSE_CANARY")
                    with anyio.CancelScope(shield=True):
                        await asyncio.Event().wait()

                monkeypatch.setattr(client._stack, "aclose", failed_close)
                monkeypatch.setattr(client, "_timeout", 0.03)
                started = monotonic()
                if primary == "safe":
                    raise AgentProviderError("safe primary failure")
                if primary == "cancel":
                    raise asyncio.CancelledError()
        except BaseException as error:
            caught = error
        assert started is not None and fault_entered
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
