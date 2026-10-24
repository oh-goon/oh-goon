"""Bounded native Codex JSON-RPC transport over one explicitly selected socket.

This client does not start a service, discover native state, approve effects, or
admit a workload. The original Run owner must establish profile qualification,
source authority and terminal tool closure before using native session methods.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from collections import deque
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path
from typing import Any, Self

import httpx2
from httpx2.websockets import AsyncWebSocketSession, HTTPXWSException

from oh_goon.core.models.agent_providers import AgentProviderError

_MAX_MESSAGE_BYTES = 256 * 1024
_MAX_REQUEST_BYTES = 4 * 1024 * 1024
_MAX_PENDING_NOTIFICATIONS = 32
_MAX_IGNORED_MESSAGES = 128
_MAX_FEATURE_PAGES = 16
_MAX_FEATURE_FLAGS = 512
_FEATURE_PAGE_SIZE = 64
_MAX_MCP_SERVERS = 64
_FEATURE_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9_.-]+/([0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?)\b")


def _identifier(value: object) -> str:
    if type(value) is not str or not value.strip() or len(value) > 256 or not value.isprintable():
        raise AgentProviderError("Native Codex service returned an invalid identifier")
    return value


def _socket_name(path: Path) -> str:
    value = str(path)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or not path.name
        or not value.isprintable()
        or len(value) > 4096
        or path.anchor == "//"
    ):
        raise AgentProviderError("Select an explicit normalized absolute native service socket")
    return value


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate native message field")
        result[name] = value
    return result


class NativeCodexRequestUncertain(AgentProviderError):
    """Native acceptance is unknown; the original owner must not replay automatically."""

    code = "NATIVE_CODEX_REQUEST_UNCERTAIN"
    retryable = False


class NativeCodexCleanupIncomplete(AgentProviderError):
    """The transport did not establish deterministic local resource closure."""

    code = "NATIVE_CODEX_CLEANUP_INCOMPLETE"


class CodexServiceClient(AbstractAsyncContextManager["CodexServiceClient"]):
    """Keep native WebSocket resources and requests with one async owner.

    Connection readiness is not authentication, effect admission or process
    quiescence. Closing this transport never asserts that native work stopped.
    """

    def __init__(
        self,
        socket_path: Path,
        *,
        timeout_seconds: float = 10.0,
        experimental_api: bool = False,
    ) -> None:
        self._socket = _socket_name(socket_path)
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 300
        ):
            raise AgentProviderError("Native service timeout must be within 300 seconds")
        self._timeout = timeout_seconds
        self._experimental = experimental_api
        self._stack: AsyncExitStack | None = None
        self._session: AsyncWebSocketSession | None = None
        self._owner: asyncio.Task[Any] | None = None
        self._next_id = 0
        self._thread: str | None = None
        self._profile: str | None = None
        self._instructions: str | None = None
        self._mcp_server_ids: tuple[str, ...] | None = None
        self._mcp_discovery_requested = False
        self._mcp_verification_requested = False
        self._notifications: deque[dict[str, Any]] = deque()
        self._facts: dict[str, object] = {}
        self._native_home: Path | None = None
        self._cleanup_status = "not_requested"

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_error: object) -> None:
        try:
            await self.close()
        except BaseException:
            if len(_error) > 1 and isinstance(_error[1], BaseException):
                _error[1].add_note("Native Codex transport cleanup is incomplete")
                return
            raise

    @property
    def metadata(self) -> dict[str, object]:
        return {
            "status": "connected_not_qualified" if self._session is not None else "not_connected",
            "agent": "codex",
            "execution_admitted": False,
            "native_authentication": "unverified",
            "phase_policy": "unverified",
            "private_store_exclusion": "unverified",
            "tool_quiescence": "unverified",
            "transport_cleanup": self._cleanup_status,
            **self._facts,
        }

    async def connect(self) -> None:
        if self._stack is not None:
            raise AgentProviderError("Native Codex service connection already exists")
        self._owner = asyncio.current_task()
        self._facts.clear()
        self._native_home = None
        stack = AsyncExitStack()
        self._stack = stack
        self._cleanup_status = "open"
        try:
            async with asyncio.timeout(self._timeout):
                await self._initialize(stack)
        except TimeoutError:
            timeout_error = (
                NativeCodexRequestUncertain("Native Codex initialization acceptance is unknown")
                if self._session is not None
                else AgentProviderError("Native Codex service connection is unavailable")
            )
            await self._close_after_failure(timeout_error)
            raise timeout_error from None
        except (HTTPXWSException, httpx2.HTTPError, OSError, ValueError):
            error = AgentProviderError("Native Codex service connection is unavailable")
            await self._close_after_failure(error)
            raise error from None
        except BaseException as error:
            await self._close_after_failure(error)
            raise

    async def _close_after_failure(self, primary: BaseException) -> None:
        try:
            await self.close()
        except BaseException:
            primary.add_note("Native Codex transport cleanup is incomplete")

    async def _initialize(self, stack: AsyncExitStack) -> None:
        transport = httpx2.AsyncHTTPTransport(uds=self._socket, trust_env=False, retries=0)
        client = await stack.enter_async_context(
            httpx2.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx2.Timeout(self._timeout),
            )
        )
        self._session = await stack.enter_async_context(
            client.websocket(
                "ws://localhost/",
                max_message_size_bytes=_MAX_MESSAGE_BYTES,
                queue_size=4,
                keepalive_ping_interval_seconds=None,
                keepalive_ping_timeout_seconds=None,
            )
        )
        result = await self._request(
            "initialize",
            {
                "clientInfo": {
                    "name": "oh-goon",
                    "title": "Oh-Goon",
                    "version": "service-client",
                },
                "capabilities": {"experimentalApi": self._experimental},
            },
        )
        native_home = result.get("codexHome")
        if isinstance(native_home, str):
            candidate = Path(native_home)
            try:
                _socket_name(candidate)
            except AgentProviderError:
                pass
            else:
                self._native_home = candidate
        user_agent = result.get("userAgent")
        version = (
            _VERSION.match(user_agent)
            if isinstance(user_agent, str) and len(user_agent) <= 1024
            else None
        )
        family, platform = result.get("platformFamily"), result.get("platformOs")
        self._facts = {
            "service_version": version[1] if version is not None else None,
            "platform_family": family
            if isinstance(family, str) and family in {"unix", "windows"}
            else "unknown",
            "platform_os": platform
            if isinstance(platform, str)
            and platform
            in {"linux", "macos", "windows", "freebsd", "openbsd", "netbsd", "android", "ios"}
            else "unknown",
        }
        await self._send({"method": "initialized"})

    async def close(self) -> None:
        if self._stack is None:
            return
        self._require_owner()
        stack, self._stack = self._stack, None
        self._session = None
        self._notifications.clear()
        self._thread = None
        self._profile = None
        self._instructions = None
        self._mcp_server_ids = None
        self._mcp_discovery_requested = False
        self._mcp_verification_requested = False
        if stack is not None:
            self._cleanup_status = "incomplete"
            try:
                async with asyncio.timeout(min(self._timeout, 5.0)):
                    await stack.aclose()
            except Exception:
                raise NativeCodexCleanupIncomplete(
                    "Native Codex transport cleanup is incomplete"
                ) from None
            self._cleanup_status = "complete"

    def _require_owner(self) -> None:
        if self._owner is not None and asyncio.current_task() is not self._owner:
            raise AgentProviderError("Native service connection must keep its original async owner")

    async def _send(self, message: dict[str, Any]) -> None:
        self._require_owner()
        if self._session is None:
            raise AgentProviderError("Native Codex service is not connected")
        try:
            data = json.dumps(message, ensure_ascii=False, allow_nan=False)
            if len(data.encode("utf-8")) > _MAX_REQUEST_BYTES:
                raise ValueError("native request exceeds byte bound")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise AgentProviderError("Native Codex service request is invalid") from None
        try:
            await self._session.send_text(data)
        except (HTTPXWSException, httpx2.HTTPError, OSError):
            raise NativeCodexRequestUncertain(
                "Native Codex request acceptance is unknown; automatic replay is unsafe"
            ) from None

    async def _receive(self, deadline: float) -> dict[str, Any]:
        self._require_owner()
        if self._session is None:
            raise AgentProviderError("Native Codex service is not connected")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise NativeCodexRequestUncertain("Native Codex response deadline expired")
        try:
            text = await self._session.receive_text(timeout=remaining)
            message = json.loads(text, object_pairs_hook=_unique_fields)
            if not isinstance(message, dict):
                raise ValueError("native message must be an object")
        except (
            HTTPXWSException,
            httpx2.HTTPError,
            OSError,
            ValueError,
            RecursionError,
            TimeoutError,
        ):
            raise NativeCodexRequestUncertain(
                "Native Codex response is unavailable or invalid; request acceptance is unknown"
            ) from None
        if "id" in message and "method" in message:
            await self._send(
                {
                    "id": message["id"],
                    "error": {"code": -32601, "message": "Client interaction is not supported"},
                }
            )
            raise NativeCodexRequestUncertain(
                "Native Codex requested unsupported client interaction; request acceptance is unknown"
            )
        return message

    def _queue_notification(self, message: dict[str, Any]) -> None:
        params = message.get("params")
        if (
            self._thread is None
            or not isinstance(params, dict)
            or params.get("threadId") != self._thread
        ):
            return
        if len(self._notifications) >= _MAX_PENDING_NOTIFICATIONS:
            raise AgentProviderError("Native Codex scoped notification bound exceeded")
        self._notifications.append(message)

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._require_owner()
        self._next_id += 1
        identifier = self._next_id
        deadline = time.monotonic() + self._timeout
        try:
            async with asyncio.timeout(self._timeout):
                await self._send({"id": identifier, "method": method, "params": params})
                for _ in range(_MAX_IGNORED_MESSAGES):
                    message = await self._receive(deadline)
                    if "id" in message:
                        if type(message["id"]) is not int or message["id"] != identifier:
                            raise NativeCodexRequestUncertain(
                                "Native Codex response differs from the current request; "
                                "request acceptance is unknown"
                            )
                        result = message.get("result")
                        if "error" in message:
                            raise AgentProviderError("Native Codex service refused the request")
                        if not isinstance(result, dict):
                            raise NativeCodexRequestUncertain(
                                "Native Codex response is invalid; request acceptance is unknown"
                            )
                        return result
                    self._queue_notification(message)
        except TimeoutError:
            raise NativeCodexRequestUncertain(
                "Native Codex request deadline expired; request acceptance is unknown"
            ) from None
        raise NativeCodexRequestUncertain(
            "Native Codex notification bound exceeded; request acceptance is unknown"
        )

    def _require_experimental(self) -> None:
        if not self._experimental:
            raise AgentProviderError(
                "Native permission profiles require explicit experimental protocol selection"
            )

    async def list_permission_profiles(
        self, *, cwd: Path, cursor: str | None = None
    ) -> dict[str, Any]:
        self._require_experimental()
        return await self._request(
            "permissionProfile/list", {"cwd": _socket_name(cwd), "cursor": cursor, "limit": 32}
        )

    def require_native_home_separation(
        self, read_roots: tuple[Path, ...], *, expected_home: Path | None = None
    ) -> None:
        """Reject tool grants that overlap native state without reading its files.

        Paths are the original execution owner's normalized grants. The native
        home is connection-private readiness metadata, never model/tool input.
        Resolve only these directory identities to reject filesystem aliases;
        native authentication/configuration file contents are never inspected.
        """
        self._require_owner()
        native_home = self._native_home
        if self._session is None or native_home is None:
            raise AgentProviderError("Native Codex state separation is unavailable")
        try:
            native_home = native_home.resolve()
        except (OSError, RuntimeError):
            raise AgentProviderError("Native Codex state separation is unavailable") from None
        if expected_home is not None:
            try:
                _socket_name(expected_home)
                selected_home = expected_home.resolve()
            except (AgentProviderError, OSError, RuntimeError):
                raise AgentProviderError("Native Codex state separation is unavailable") from None
            if native_home != selected_home:
                raise AgentProviderError("Native Codex state differs from the resolved selection")
        for root in read_roots:
            try:
                _socket_name(root)
                root = root.resolve()
            except (AgentProviderError, TypeError, AttributeError, OSError, RuntimeError):
                raise AgentProviderError("Native Codex state separation is unavailable") from None
            if root.is_relative_to(native_home) or native_home.is_relative_to(root):
                raise AgentProviderError("Native Codex state overlaps an agent-readable root")

    async def mcp_exclusion_configuration(self) -> dict[str, dict[str, dict[str, bool]]]:
        """Resolve opaque native IDs once without exporting native configuration.

        Native discovery may initialize configured servers under ordinary native
        startup authority. No model turn or captured task is submitted here.
        Only IDs survive this bounded snapshot, for the native per-thread disable
        override; the controller never resolves transports or credentials.
        """
        self._require_experimental()
        if self._thread is not None or self._mcp_discovery_requested:
            raise AgentProviderError("Native Codex MCP exclusions require one pre-thread snapshot")
        self._mcp_discovery_requested = True
        result = await self._request(
            "mcpServerStatus/list",
            {
                "threadId": None,
                "cursor": None,
                "limit": _MAX_MCP_SERVERS,
                "detail": "toolsAndAuthOnly",
            },
        )
        self._mcp_server_ids = self._mcp_snapshot_ids(result)
        return {"mcp_servers": {name: {"enabled": False} for name in self._mcp_server_ids}}

    @staticmethod
    def _mcp_snapshot_ids(
        result: dict[str, Any], *, require_disabled: bool = False
    ) -> tuple[str, ...]:
        data = result.get("data")
        if (
            type(data) is not list
            or len(data) > _MAX_MCP_SERVERS
            or "nextCursor" not in result
            or result["nextCursor"] is not None
        ):
            raise AgentProviderError(
                "Native Codex MCP inventory is incomplete or exceeds its bound"
            )
        identifiers: list[str] = []
        for entry in data:
            if type(entry) is not dict or "pluginId" not in entry or entry["pluginId"] is not None:
                raise AgentProviderError("Native Codex MCP registration cannot be excluded safely")
            name = _identifier(entry.get("name"))
            if name in identifiers:
                raise AgentProviderError("Native Codex MCP inventory has duplicate identifiers")
            if require_disabled and (
                entry.get("runtimeStatus") != "disabled"
                or entry.get("tools") != {}
                or entry.get("resources") != []
                or entry.get("resourceTemplates") != []
                or entry.get("toolsError") is not None
            ):
                raise AgentProviderError("Native Codex MCP tool exclusion was not established")
            identifiers.append(name)
        return tuple(identifiers)

    async def require_mcp_servers_excluded(self, thread_id: str) -> None:
        """Prove the sole workload thread has no usable registered capabilities."""
        self._require_experimental()
        self._require_thread(thread_id)
        if self._mcp_server_ids is None or self._mcp_verification_requested:
            raise AgentProviderError("Native Codex MCP exclusion requires its original snapshot")
        self._mcp_verification_requested = True
        result = await self._request(
            "mcpServerStatus/list",
            {
                "threadId": thread_id,
                "cursor": None,
                "limit": _MAX_MCP_SERVERS,
                "detail": "toolsAndAuthOnly",
            },
        )
        if set(self._mcp_snapshot_ids(result, require_disabled=True)) != set(self._mcp_server_ids):
            raise AgentProviderError("Native Codex MCP inventory changed after exclusion")

    async def feature_flags(self, thread_id: str) -> dict[str, bool]:
        """Return bounded effective booleans, excluding native configuration values."""
        self._require_experimental()
        self._require_thread(thread_id)
        flags: dict[str, bool] = {}
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(_MAX_FEATURE_PAGES):
            result = await self._request(
                "experimentalFeature/list",
                {"threadId": thread_id, "cursor": cursor, "limit": _FEATURE_PAGE_SIZE},
            )
            data = result.get("data")
            if not isinstance(data, list) or len(data) > _FEATURE_PAGE_SIZE:
                raise AgentProviderError("Native Codex feature capability metadata is invalid")
            for item in data:
                if not isinstance(item, dict):
                    raise AgentProviderError("Native Codex feature capability metadata is invalid")
                name, enabled = item.get("name"), item.get("enabled")
                if (
                    not isinstance(name, str)
                    or _FEATURE_NAME.fullmatch(name) is None
                    or type(enabled) is not bool
                    or name in flags
                    or len(flags) >= _MAX_FEATURE_FLAGS
                ):
                    raise AgentProviderError("Native Codex feature capability metadata is invalid")
                flags[name] = enabled
            next_cursor = result.get("nextCursor")
            if next_cursor is None:
                return flags
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or len(next_cursor) > 256
                or not next_cursor.isprintable()
                or next_cursor in seen_cursors
            ):
                raise AgentProviderError("Native Codex feature capability pagination is invalid")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        raise AgentProviderError("Native Codex feature capability pagination bound exceeded")

    async def command_exec(
        self,
        command: tuple[str, ...],
        *,
        cwd: Path,
        permissions: str,
        timeout_seconds: float = 10.0,
    ) -> dict[str, Any]:
        """Run the caller's explicit qualification through native command authority."""
        self._require_experimental()
        if not command or not 0 < timeout_seconds <= self._timeout:
            raise AgentProviderError("Native qualification requires bounded command authority")
        return await self._request(
            "command/exec",
            {
                "command": list(command),
                "cwd": _socket_name(cwd),
                "permissionProfile": _identifier(permissions),
                "timeoutMs": int(timeout_seconds * 1000),
                "outputBytesCap": 8192,
            },
        )

    async def start_thread(
        self,
        *,
        cwd: Path,
        permissions: str,
        model: str,
        developer_instructions: str,
        configuration: dict[str, Any] | None = None,
        ephemeral: bool = True,
    ) -> dict[str, Any]:
        self._require_experimental()
        if self._thread is not None:
            raise AgentProviderError("A new phase requires a fresh native service connection")
        result = await self._request(
            "thread/start",
            {
                "cwd": _socket_name(cwd),
                "permissions": _identifier(permissions),
                "model": _identifier(model),
                "developerInstructions": developer_instructions,
                "config": configuration,
                "ephemeral": ephemeral,
                "approvalPolicy": "never",
                "allowProviderModelFallback": False,
                "environments": [
                    {
                        "environmentId": "local",
                        "cwd": _socket_name(cwd),
                        "runtimeWorkspaceRoots": [_socket_name(cwd)],
                    }
                ],
                "selectedCapabilityRoots": [],
                "runtimeWorkspaceRoots": [_socket_name(cwd)],
            },
        )
        selected = self._select_thread(result, permissions)
        self._instructions = developer_instructions
        return selected

    async def resume_thread(
        self,
        thread_id: str,
        *,
        permissions: str,
        configuration: dict[str, Any] | None = None,
        developer_instructions: str | None = None,
    ) -> dict[str, Any]:
        self._require_experimental()
        thread_id = _identifier(thread_id)
        permissions = _identifier(permissions)
        if self._thread is None and developer_instructions is None:
            raise AgentProviderError(
                "Native continuation requires its original frozen instructions"
            )
        if self._thread is not None and (
            thread_id != self._thread
            or permissions != self._profile
            or configuration is not None
            or (developer_instructions is not None and developer_instructions != self._instructions)
        ):
            raise AgentProviderError(
                "Native continuation cannot replace its selected thread or profile"
            )
        result = await self._request(
            "thread/resume",
            {
                "threadId": _identifier(thread_id),
                "permissions": _identifier(permissions),
                "config": configuration,
                "excludeTurns": True,
                "developerInstructions": developer_instructions
                if developer_instructions is not None
                else self._instructions,
            },
        )
        selected = self._select_thread(result, permissions, expected_id=thread_id)
        if developer_instructions is not None:
            self._instructions = developer_instructions
        return selected

    def _select_thread(
        self, result: dict[str, Any], permissions: str, *, expected_id: str | None = None
    ) -> dict[str, Any]:
        # The native response identifies a selected profile by id/extends only.
        # It does not attest its effective grants or current configuration generation.
        active = result.get("activePermissionProfile")
        thread = result.get("thread")
        if (
            not isinstance(active, dict)
            or active.get("id") != permissions
            or not isinstance(thread, dict)
        ):
            raise AgentProviderError(
                "Native Codex did not establish the explicitly selected permission profile"
            )
        identifier = _identifier(thread.get("id"))
        if expected_id is not None and identifier != expected_id:
            raise AgentProviderError("Native continuation returned a different thread")
        self._thread = identifier
        self._profile = permissions
        self._notifications.clear()
        return result

    def _require_thread(self, thread_id: str) -> None:
        if _identifier(thread_id) != self._thread:
            raise AgentProviderError(
                "Native continuation requires this connection's selected thread"
            )

    async def start_turn(
        self,
        thread_id: str,
        text: str,
        *,
        permissions: str,
        effort: str | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._require_experimental()
        self._require_thread(thread_id)
        if permissions != self._profile:
            raise AgentProviderError("Native continuation cannot change its selected profile")
        return await self._request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": text}],
                "permissions": _identifier(permissions),
                "effort": effort,
                "outputSchema": output_schema,
            },
        )

    async def compact_thread(self, thread_id: str) -> dict[str, Any]:
        self._require_thread(thread_id)
        return await self._request("thread/compact/start", {"threadId": thread_id})

    async def interrupt_turn(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        self._require_thread(thread_id)
        return await self._request(
            "turn/interrupt", {"threadId": thread_id, "turnId": _identifier(turn_id)}
        )

    async def next_notification(self, thread_id: str) -> dict[str, Any]:
        self._require_thread(thread_id)
        if self._notifications:
            return self._notifications.popleft()
        deadline = time.monotonic() + self._timeout
        for _ in range(_MAX_IGNORED_MESSAGES):
            message = await self._receive(deadline)
            if "id" in message:
                raise AgentProviderError("Native Codex sent an unexpected response")
            self._queue_notification(message)
            if self._notifications:
                return self._notifications.popleft()
        raise AgentProviderError("Native Codex notification bound exceeded")
