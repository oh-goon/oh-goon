"""Native service capability metadata never grants ambient store or MCP access."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from httpx2.websockets import ASGIWebSocketTransport
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.integrations.agent_codex_service import CodexServiceClient


def native_capability_transport(
    monkeypatch: pytest.MonkeyPatch,
    *,
    native_home: object,
    capability: Callable[[dict[str, Any]], dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                messages.append(message)
                method = message["method"]
                if method == "initialized":
                    continue
                if method == "initialize":
                    result = {
                        "userAgent": "codex_cli_rs/0.163.0",
                        "codexHome": native_home,
                        "platformFamily": "unix",
                        "platformOs": "linux",
                    }
                elif method == "thread/start":
                    result = {
                        "thread": {"id": "owned-thread"},
                        "activePermissionProfile": {"id": "owned-profile"},
                    }
                else:
                    result = capability(message)
                await websocket.send_json({"id": message["id"], "result": result})
        except WebSocketDisconnect:
            pass

    app = Starlette(routes=[WebSocketRoute("/", endpoint)])
    monkeypatch.setattr(
        "oh_goon.integrations.agent_codex_service.httpx2.AsyncHTTPTransport",
        lambda **_kwargs: ASGIWebSocketTransport(app),
    )
    return messages


async def start_owned_thread(client: CodexServiceClient, cwd: Path) -> None:
    await client.start_thread(
        cwd=cwd,
        permissions="owned-profile",
        model="native-model",
        developer_instructions="Frozen repository and Skill context",
    )


def test_native_home_metadata_stays_private_and_separated_without_store_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_home = tmp_path / "PRIVATE_NATIVE_HOME"
    messages = native_capability_transport(
        monkeypatch, native_home=str(private_home), capability=lambda _: {}
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock") as client:
            client.require_native_home_separation(
                (tmp_path / "source", tmp_path / "public-runtime")
            )
            assert "PRIVATE_NATIVE_HOME" not in json.dumps(client.metadata)
            with pytest.raises(AgentProviderError, match="overlaps") as error:
                client.require_native_home_separation((private_home,))
            assert "PRIVATE_NATIVE_HOME" not in str(error.value)
        with pytest.raises(AgentProviderError, match="unavailable"):
            client.require_native_home_separation((tmp_path / "source",))

    asyncio.run(check())
    assert [item["method"] for item in messages] == ["initialize", "initialized"]
    assert not private_home.exists()


@pytest.mark.parametrize("relationship", ["same", "native_inside_grant", "grant_inside_native"])
def test_native_home_overlap_rejects_both_ancestor_directions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relationship: str
) -> None:
    native_home = tmp_path / "private"
    root = {
        "same": native_home,
        "native_inside_grant": tmp_path,
        "grant_inside_native": native_home / "public-runtime",
    }[relationship]
    native_capability_transport(monkeypatch, native_home=str(native_home), capability=lambda _: {})

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock") as client:
            with pytest.raises(AgentProviderError, match="overlaps"):
                client.require_native_home_separation((root,))

    asyncio.run(check())


@pytest.mark.parametrize("native_home", [None, 5, "relative/native-home", "/tmp/../private", ""])
def test_unusable_native_home_metadata_cannot_admit_separation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_home: object
) -> None:
    native_capability_transport(monkeypatch, native_home=native_home, capability=lambda _: {})

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock") as client:
            with pytest.raises(AgentProviderError, match="unavailable"):
                client.require_native_home_separation((tmp_path / "source",))
            assert client.metadata["execution_admitted"] is False

    asyncio.run(check())


def test_zero_mcp_gate_only_uses_owned_thread_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = native_capability_transport(
        monkeypatch,
        native_home=str(tmp_path / "native-home"),
        capability=lambda _: {"data": [], "nextCursor": None},
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock", experimental_api=True) as client:
            await start_owned_thread(client, tmp_path)
            await client.require_no_mcp_servers("owned-thread")
            with pytest.raises(AgentProviderError, match="selected thread"):
                await client.require_no_mcp_servers("foreign-thread")

    asyncio.run(check())
    inventory = [item for item in messages if item["method"] == "mcpServerStatus/list"]
    assert len(inventory) == 1
    assert inventory[0]["params"] == {
        "threadId": "owned-thread",
        "cursor": None,
        "limit": 1,
        "detail": "toolsAndAuthOnly",
    }


@pytest.mark.parametrize(
    "response",
    [
        {"data": [{"name": "PRIVATE_MCP_SERVER", "tools": []}]},
        {"data": [], "nextCursor": "PRIVATE_CURSOR"},
        {"data": [], "nextCursor": False},
        {"data": {}},
        {},
    ],
)
def test_nonempty_or_inconclusive_mcp_catalog_cannot_admit_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]
) -> None:
    native_capability_transport(
        monkeypatch, native_home=str(tmp_path / "native-home"), capability=lambda _: response
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock", experimental_api=True) as client:
            await start_owned_thread(client, tmp_path)
            with pytest.raises(AgentProviderError, match="empty MCP catalog") as error:
                await client.require_no_mcp_servers("owned-thread")
            assert "PRIVATE" not in str(error.value)
            assert "PRIVATE" not in json.dumps(client.metadata)
            assert client.metadata["execution_admitted"] is False

    asyncio.run(check())


def test_native_feature_pages_return_only_effective_booleans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def capability(message: dict[str, Any]) -> dict[str, Any]:
        assert message["method"] == "experimentalFeature/list"
        if message["params"]["cursor"] is None:
            return {
                "data": [{"name": "hooks", "enabled": False, "description": "PRIVATE_CONFIG"}],
                "nextCursor": "page-two",
            }
        return {"data": [{"name": "plugins", "enabled": False}], "nextCursor": None}

    messages = native_capability_transport(
        monkeypatch, native_home=str(tmp_path / "native-home"), capability=capability
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock", experimental_api=True) as client:
            await start_owned_thread(client, tmp_path)
            assert await client.feature_flags("owned-thread") == {"hooks": False, "plugins": False}
            with pytest.raises(AgentProviderError, match="selected thread"):
                await client.feature_flags("foreign-thread")

    asyncio.run(check())
    flags = [item for item in messages if item["method"] == "experimentalFeature/list"]
    assert [item["params"] for item in flags] == [
        {"threadId": "owned-thread", "cursor": None, "limit": 64},
        {"threadId": "owned-thread", "cursor": "page-two", "limit": 64},
    ]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"data": {}},
        {"data": [None]},
        {"data": [{"name": "hooks", "enabled": 0}]},
        {"data": [{"name": "INVALID_PRIVATE_FEATURE", "enabled": False}]},
        {"data": [{"name": "hooks", "enabled": False}] * 2},
        {"data": [{"name": "hooks", "enabled": False}] * 65},
        {"data": [], "nextCursor": False},
        {"data": [], "nextCursor": ""},
        {"data": [], "nextCursor": "x" * 257},
        {"data": [], "nextCursor": "\nPRIVATE_CURSOR"},
    ],
)
def test_invalid_feature_metadata_never_becomes_effective_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: dict[str, Any]
) -> None:
    native_capability_transport(
        monkeypatch, native_home=str(tmp_path / "native-home"), capability=lambda _: response
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock", experimental_api=True) as client:
            await start_owned_thread(client, tmp_path)
            with pytest.raises(AgentProviderError, match="capability") as error:
                await client.feature_flags("owned-thread")
            assert "PRIVATE" not in str(error.value)

    asyncio.run(check())


@pytest.mark.parametrize(
    "fault", ["repeated_cursor", "endless_pages", "duplicate_across_pages", "too_many_flags"]
)
def test_feature_pagination_cannot_repeat_or_exceed_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    calls = 0

    def capability(_message: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if fault == "duplicate_across_pages":
            return {"data": [{"name": "hooks", "enabled": False}], "nextCursor": str(calls)}
        if fault == "too_many_flags":
            return {
                "data": [
                    {"name": f"flag_{calls}_{index}", "enabled": False} for index in range(64)
                ],
                "nextCursor": str(calls),
            }
        return {"data": [], "nextCursor": "same" if fault == "repeated_cursor" else str(calls)}

    native_capability_transport(
        monkeypatch, native_home=str(tmp_path / "native-home"), capability=capability
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock", experimental_api=True) as client:
            await start_owned_thread(client, tmp_path)
            with pytest.raises(AgentProviderError, match="capability"):
                await client.feature_flags("owned-thread")

    asyncio.run(check())
    assert calls <= 16


@pytest.mark.parametrize("alias_kind", ["reported_native_home", "public_read_grant"])
def test_native_home_separation_rejects_filesystem_directory_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alias_kind: str
) -> None:
    native_home = tmp_path / "native-owned-home"
    native_home.mkdir()
    alias = tmp_path / "public-directory-alias"
    alias.symlink_to(native_home, target_is_directory=True)
    reported_home = alias if alias_kind == "reported_native_home" else native_home
    read_root = native_home / "runtime" if alias_kind == "reported_native_home" else alias
    native_capability_transport(
        monkeypatch, native_home=str(reported_home), capability=lambda _: {}
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock") as client:
            with pytest.raises(AgentProviderError, match="overlaps"):
                client.require_native_home_separation((read_root,))
            assert "native-owned-home" not in json.dumps(client.metadata)
            assert "public-directory-alias" not in json.dumps(client.metadata)

    asyncio.run(check())
    assert list(native_home.iterdir()) == []


def test_native_home_must_match_the_original_resolved_directory_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_capability_transport(
        monkeypatch, native_home=str(tmp_path / "reported-native-home"), capability=lambda _: {}
    )

    async def check() -> None:
        async with CodexServiceClient(tmp_path / "selected.sock") as client:
            with pytest.raises(AgentProviderError, match="differs from the resolved selection"):
                client.require_native_home_separation(
                    (tmp_path / "public-runtime",), expected_home=tmp_path / "selected-native-home"
                )
            assert "native-home" not in json.dumps(client.metadata)

    asyncio.run(check())
