"""Bounded local Responses service for installed Codex conformance.

Only the deterministic server side is simulated. The installed client retains
authentication, request chaining, tool execution and automatic compaction.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import socket
import ssl
import struct
from collections.abc import Callable
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Literal

_MAX_MESSAGE = 4 * 1024 * 1024


class CodexResponsesFixture(AbstractContextManager["CodexResponsesFixture"]):
    """Serve four inference stages and at most one native WebSocket warm-up."""

    def __init__(
        self,
        *,
        transport: Literal["http", "websocket"],
        prompt: Callable[[], bytes | None],
        auth_token: str,
        account_id: str | None = None,
        tls_certificate: Path | None = None,
        tls_key: Path | None = None,
    ) -> None:
        self.transport = transport
        self.prompt = prompt
        self.auth_token = auth_token
        self.account_id = account_id
        self.observations: list[dict[str, Any]] = []
        self.warmup_observations: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.request_count = 0
        self.warmup_count = 0
        self.websocket_connections = 0
        self.http_inference_requests = 0
        self.account_checks = 0
        self.get_requests = 0
        self.analytics_requests = 0
        self.routing_observations: list[dict[str, Any]] = []
        self._lock = Lock()
        self._connections: set[socket.socket] = set()
        self._chains: dict[str, dict[str, Any]] = {}
        self._last_response: str | None = None
        self._compact_item = {
            "type": "compaction",
            "id": "cmp_installed_context",
            "encrypted_content": "opaque-installed-context-compaction",
        }
        fixture = self

        class Endpoint(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)
                with fixture._lock:
                    fixture._connections.add(self.connection)

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    with fixture._lock:
                        fixture._connections.discard(self.connection)

            def do_GET(self) -> None:
                fixture.get_requests += 1
                if fixture.get_requests > 24:
                    fixture._error("GET count outside fixture bound")
                    self.send_error(429)
                    return
                if self.path == "/backend-api/wham/accounts/check" and account_id is not None:
                    fixture.account_checks += 1
                    if fixture.account_checks > 4:
                        fixture._error("Routing count outside fixture bound")
                        self.send_error(429)
                        return
                    fixture.routing_observations.append(
                        {
                            "path": self.path,
                            "host": self.headers.get("Host"),
                            "synthetic_auth": self.headers.get("Authorization")
                            == f"Bearer {fixture.auth_token}",
                            "account_id": self.headers.get("ChatGPT-Account-Id"),
                        }
                    )
                    self._json(
                        {
                            "accounts": [
                                {
                                    "id": account_id,
                                    "workspace_backend_origin": fixture.base_url,
                                    "account_routing_override": "NO_CONSTRAINT",
                                }
                            ],
                            "default_account_id": account_id,
                        }
                    )
                elif self.path == "/backend-api/wham/config/bundle" and account_id is not None:
                    self._json({"requirements_toml": {}})
                elif (
                    fixture.transport == "websocket"
                    and self.path.endswith("/responses")
                    and self.headers.get("Upgrade", "").lower() == "websocket"
                ):
                    self._websocket()
                else:
                    # An HTTP case exercises the installed client's own fallback.
                    self.send_error(404)

            def _json(self, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                if (
                    fixture.account_id is not None
                    and self.path == "/backend-api/codex/analytics-events/events"
                ):
                    fixture.analytics_requests += 1
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                        if fixture.analytics_requests > 8 or not 0 < length <= 64 * 1024:
                            raise ValueError("Analytics metadata outside fixture bound")
                        self._exact(length)
                        self._json({})
                    except (OSError, ValueError) as exc:
                        fixture._error(f"{type(exc).__name__}: {exc}"[:160])
                        self.close_connection = True
                    return
                fixture.http_inference_requests += 1
                if fixture.transport != "http" or not self.path.endswith("/responses"):
                    fixture._error("Unexpected HTTP inference: " + self.path[:120])
                    self.send_error(500)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= _MAX_MESSAGE:
                        raise ValueError("Request size outside fixture bound")
                    data = self._exact(length)
                    encoding = self.headers.get("Content-Encoding")
                    if encoding == "zstd":
                        zstd = import_module("compression.zstd")
                        decoder = zstd.ZstdDecompressor()
                        try:
                            data = decoder.decompress(data, max_length=_MAX_MESSAGE + 1)
                        except zstd.ZstdError as exc:
                            raise ValueError("Invalid compressed fixture request") from exc
                        if len(data) > _MAX_MESSAGE or not decoder.eof or decoder.unused_data:
                            raise ValueError("Compressed request outside fixture bound")
                    elif encoding is not None:
                        raise ValueError("Unsupported request encoding")
                    events = fixture._response(json.loads(data), self, "http")
                    data = "".join(
                        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except (
                    AssertionError,
                    ImportError,
                    KeyError,
                    OSError,
                    TypeError,
                    ValueError,
                ) as exc:
                    fixture._error(
                        f"ValueError: {exc}"[:160]
                        if type(exc) is ValueError
                        else type(exc).__name__
                    )
                finally:
                    self.close_connection = True

            def _exact(self, size: int) -> bytes:
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError("Truncated WebSocket or HTTP data")
                return data

            def _send_frame(self, data: bytes, opcode: int = 1) -> None:
                if len(data) < 126:
                    header = bytes((0x80 | opcode, len(data)))
                elif len(data) < 65536:
                    header = bytes((0x80 | opcode, 126)) + struct.pack("!H", len(data))
                else:
                    header = bytes((0x80 | opcode, 127)) + struct.pack("!Q", len(data))
                self.wfile.write(header + data)
                self.wfile.flush()

            def _websocket(self) -> None:
                fixture.websocket_connections += 1
                try:
                    if self.headers.get("Sec-WebSocket-Version") != "13":
                        raise ValueError("Unsupported WebSocket version")
                    key = self.headers["Sec-WebSocket-Key"]
                    if key is None or len(base64.b64decode(key, validate=True)) != 16:
                        raise ValueError("Invalid WebSocket key")
                    accepted = base64.b64encode(
                        hashlib.sha1(
                            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
                        ).digest()
                    ).decode()
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.send_header("Sec-WebSocket-Accept", accepted)
                    self.end_headers()
                    message = bytearray()
                    fragmented = False
                    for _ in range(64):
                        header = self.rfile.read(2)
                        if not header:
                            return
                        if len(header) != 2:
                            raise ValueError("Truncated WebSocket header")
                        first, second = header
                        final, opcode = bool(first & 0x80), first & 0x0F
                        if first & 0x70 or not second & 0x80:
                            raise ValueError("Unexpected WebSocket extension or unmasked frame")
                        size = second & 0x7F
                        if size == 126:
                            size = struct.unpack("!H", self._exact(2))[0]
                        elif size == 127:
                            size = struct.unpack("!Q", self._exact(8))[0]
                        control = bool(opcode & 0x08)
                        if control and (not final or size > 125):
                            raise ValueError("Invalid WebSocket control frame")
                        if size > _MAX_MESSAGE - (0 if control else len(message)):
                            raise ValueError("WebSocket message outside fixture bound")
                        mask = self._exact(4)
                        data = bytes(
                            value ^ mask[index % 4] for index, value in enumerate(self._exact(size))
                        )
                        if opcode == 8:
                            self._send_frame(data, 8)
                            return
                        if opcode == 9:
                            self._send_frame(data, 10)
                            continue
                        if opcode == 10:
                            continue
                        if (opcode == 1 and fragmented) or (opcode == 0 and not fragmented):
                            raise ValueError("Invalid WebSocket continuation")
                        if opcode not in (0, 1):
                            raise ValueError("Expected WebSocket text message")
                        message.extend(data)
                        fragmented = not final
                        if not final:
                            continue
                        payload = json.loads(message)
                        message.clear()
                        if payload.get("type") != "response.create":
                            raise ValueError("Expected native response.create")
                        for event in fixture._response(payload, self, "websocket"):
                            self._send_frame(json.dumps(event).encode())
                    raise ValueError("WebSocket frame count outside fixture bound")
                except (AssertionError, KeyError, OSError, TypeError, ValueError) as exc:
                    fixture._error(
                        f"ValueError: {exc}"[:160]
                        if type(exc) is ValueError
                        else type(exc).__name__
                    )
                finally:
                    self.close_connection = True

            def log_message(self, *_args: object) -> None:
                pass

        if (tls_certificate is None) != (tls_key is None):
            raise ValueError("Both TLS certificate and key are required")
        context = None
        if tls_certificate is not None and tls_key is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(tls_certificate, tls_key)

        class LocalServer(ThreadingHTTPServer):
            def get_request(self) -> tuple[socket.socket, Any]:
                connection, address = super().get_request()
                connection.settimeout(5)
                try:
                    if context is not None:
                        connection = context.wrap_socket(connection, server_side=True)
                    return connection, address
                except BaseException:
                    connection.close()
                    raise

        self._server = LocalServer(("127.0.0.1", 0), Endpoint)
        scheme = "https" if tls_certificate is not None else "http"
        self.base_url = f"{scheme}://127.0.0.1:{self._server.server_port}"
        self._worker = Thread(target=self._server.serve_forever, name="installed-codex-responses")

    def _response(
        self, body: dict[str, Any], endpoint: BaseHTTPRequestHandler, transport: str
    ) -> tuple[dict[str, Any], ...]:
        warmup = body.get("generate") is False
        if warmup:
            self.warmup_count += 1
            if self.warmup_count > 1:
                raise ValueError("Repeated native warm-up")
            sequence = 0
        else:
            with self._lock:
                self.request_count += 1
                sequence = self.request_count
            if sequence > 4:
                raise ValueError("Inference count outside fixture bound")
        prompt = self.prompt()
        assert prompt is not None
        original = prompt.decode("utf-8")
        messages = body["input"]
        privileged = [
            part["text"]
            for item in messages
            if item.get("type") == "message" and item.get("role") == "developer"
            for part in item.get("content", [])
            if part.get("type") == "input_text"
        ]
        user_texts = [
            part["text"]
            for item in messages
            if item.get("type") == "message" and item.get("role") == "user"
            for part in item.get("content", [])
            if part.get("type") == "input_text"
        ]
        outputs = {
            item["call_id"]: json.dumps(item.get("output", ""))
            for item in messages
            if item.get("type") == "custom_tool_call_output"
        }
        previous = body.get("previous_response_id")
        parent = self._chains.get(previous, {}) if isinstance(previous, str) else {}
        chain_valid = previous is None or (
            previous == self._last_response and previous in self._chains
        )
        if not chain_valid:
            raise ValueError("Unknown or stale native response chain")
        response_id = "resp_context_warmup" if warmup else f"resp_context_{sequence}"
        observation = {
            "privileged_exact_count": privileged.count(original),
            "privileged_in_chain": privileged.count(original) == 1
            or bool(parent.get("privileged_in_chain")),
            "user_duplicate": original in user_texts,
            "followup": "CONTEXT_FOLLOWUP_PROOF" in outputs.get("context_first", "")
            or bool(parent.get("followup")),
            "edit": "CONTEXT_EDIT_PROOF" in outputs.get("context_second", "")
            or bool(parent.get("edit")),
            "retained_tool_outputs": bool(outputs) or bool(parent.get("retained_tool_outputs")),
            "compaction_trigger": any(
                item.get("type") == "compaction_trigger" for item in messages
            ),
            "retained_compaction": self._compact_item in messages,
            "path": endpoint.path,
            "host": endpoint.headers.get("Host"),
            "transport": transport,
            "synthetic_auth": endpoint.headers.get("Authorization") == f"Bearer {self.auth_token}",
            "account_id": endpoint.headers.get("ChatGPT-Account-Id"),
            "request_encoding": endpoint.headers.get("Content-Encoding"),
            "previous_response_id": previous,
            "response_id": response_id,
            "chain_valid": chain_valid,
        }
        if warmup:
            self.warmup_observations.append(observation)
        else:
            self.observations.append(observation)
        self._chains[response_id] = observation
        self._last_response = response_id
        if warmup:
            return self._events(response_id, None, 0)
        if sequence in (1, 2):
            command = (
                "printf CONTEXT_FOLLOWUP_PROOF"
                if sequence == 1
                else "printf 'VALUE = 42\\n' > code.py; printf CONTEXT_EDIT_PROOF"
            )
            item: dict[str, Any] = {
                "type": "custom_tool_call",
                "id": f"ctc_context_{sequence}",
                "call_id": "context_first" if sequence == 1 else "context_second",
                "name": "exec",
                "namespace": "functions",
                "input": "text(await tools.exec_command(" + json.dumps({"cmd": command}) + "));",
                "status": "completed",
            }
        elif sequence == 3:
            item = self._compact_item
        else:
            item = {
                "type": "message",
                "id": "msg_context_complete",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Installed Codex context transport complete",
                        "annotations": [],
                    }
                ],
                "status": "completed",
            }
        return self._events(response_id, item, 2_000_000 if sequence == 2 else 1)

    @staticmethod
    def _events(
        response_id: str, item: dict[str, Any] | None, input_tokens: int
    ) -> tuple[dict[str, Any], ...]:
        response: dict[str, Any] = {
            "id": response_id,
            "object": "response",
            "created_at": 0,
            "model": "gpt-6-luna",
            "status": "completed",
            "output": [] if item is None else [item],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 0 if item is None else 1,
                "total_tokens": input_tokens + (0 if item is None else 1),
            },
        }
        events: list[dict[str, Any]] = [
            {
                "type": "response.created",
                "response": {**response, "status": "in_progress", "output": []},
            }
        ]
        if item is not None:
            events.append({"type": "response.output_item.done", "output_index": 0, "item": item})
        events.append({"type": "response.completed", "response": response})
        return tuple(events)

    def _error(self, message: str) -> None:
        if len(self.errors) < 8:
            self.errors.append(message)

    def __enter__(self) -> CodexResponsesFixture:
        self._worker.start()
        return self

    def __exit__(self, *_args: object) -> None:
        cleanup_errors: list[OSError] = []
        try:
            self._server.shutdown()
        finally:
            try:
                with self._lock:
                    connections = tuple(self._connections)
                for connection in connections:
                    try:
                        connection.shutdown(socket.SHUT_RDWR)
                    except OSError as exc:
                        if exc.errno not in (errno.ENOTCONN, errno.EBADF):
                            cleanup_errors.append(exc)
                    finally:
                        try:
                            connection.close()
                        except OSError as exc:
                            cleanup_errors.append(exc)
            finally:
                try:
                    self._server.server_close()
                finally:
                    self._worker.join(timeout=5)
                    if self._worker.is_alive():
                        raise RuntimeError("Codex fixture server did not stop")
        if cleanup_errors:
            raise ExceptionGroup("Codex fixture connection cleanup failed", cleanup_errors)
