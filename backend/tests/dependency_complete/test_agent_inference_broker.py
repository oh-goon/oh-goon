from __future__ import annotations

import contextlib
import json
import socket
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx2
import pytest

from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    AgentProvidersConfig,
    ResolvedAgentModel,
)
from oh_goon.engine.command import run_command
from oh_goon.integrations.agent_inference_broker import approved_model_broker
from oh_goon.integrations.agent_inference_relay import model_inference_relay

EVIDENCE = "Exact Hashmarks packet: qualified=true; coverage=bounded; freshness=captured; π"
SKILL = "# Native Skill\nPreserve complete scope and report evidence."
STREAM = b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"edit","arguments":"{}"}}]}}]}\n\ndata: [DONE]\n\n'


@dataclass
class _Provider:
    requests: list[tuple[str, dict[str, str], bytes]] = field(default_factory=list)
    status: int = 200
    chunks: tuple[bytes, ...] = (STREAM,)
    headers: dict[str, str] = field(default_factory=lambda: {"Content-Type": "text/event-stream"})
    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    hold: bool = False
    hold_after_first: bool = False
    base_url: str = ""


@pytest.fixture
def provider() -> Iterator[_Provider]:
    state = _Provider()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            state.requests.append(
                (self.path, {k.lower(): v for k, v in self.headers.items()}, body)
            )
            state.started.set()
            if state.hold:
                state.release.wait(5)
            with contextlib.suppress(OSError):
                self.send_response(state.status)
                for name, value in state.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                for index, chunk in enumerate(state.chunks):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if index == 0 and state.hold_after_first:
                        state.release.wait(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        state.release.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def directory() -> Iterator[Path]:
    # Unix sockets have a native pathname bound; long pytest node directories
    # are intentionally not used as socket addresses.
    with tempfile.TemporaryDirectory(prefix="broker-test-") as path:
        yield Path(path)


def _selected(
    provider: _Provider, api: str = "openai-chat-completions", **limits: Any
) -> ResolvedAgentModel:
    return AgentProvidersConfig.model_validate(
        {
            "default_model": "selected",
            "providers": {
                "company": {
                    "api": api,
                    "base_url": provider.base_url
                    + ("/provider" if api == "ollama" else "/provider/v1"),
                    "api_key_env": "COMPANY_MODEL_KEY",
                    "headers": {"X-Owner": "frozen"},
                    "timeout_seconds": 5.0,
                    **limits,
                }
            },
            "models": {"selected": {"provider": "company", "model": "approved/model"}},
        }
    ).resolve(None, environment={"COMPANY_MODEL_KEY": "host-only-secret"})


def _body(api: str = "openai-chat-completions") -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "approved/model",
        "stream": True,
        "temperature": 0.37,
        "messages": [{"role": "user", "content": "Inspect https://example.test as literal text"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "edit",
                    "parameters": {
                        "type": "object",
                        "properties": {"image_url": {"type": "string"}},
                    },
                },
            }
        ],
    }
    if api == "openai-responses":
        body["input"] = body.pop("messages")
        body["tools"] = [{"type": "function", "name": "edit", "parameters": {"type": "object"}}]
    elif api == "anthropic-messages":
        body["system"] = [{"type": "text", "text": "Native agent system context"}]
        body["tools"] = [{"name": "edit", "input_schema": {"type": "object"}}]
    return body


def _client(path: Path) -> httpx2.Client:
    return httpx2.Client(
        transport=httpx2.HTTPTransport(uds=str(path)),
        base_url="http://localhost",
        trust_env=False,
        timeout=3,
    )


@pytest.mark.parametrize(
    ("api", "route", "upstream"),
    [
        ("openai-chat-completions", "/v1/chat/completions", "/provider/v1/chat/completions"),
        ("openai-responses", "/v1/responses", "/provider/v1/responses"),
        ("anthropic-messages", "/v1/messages", "/provider/v1/messages"),
        ("ollama", "/api/chat", "/provider/api/chat"),
    ],
)
def test_exact_native_stream_tools_settings_and_frozen_context_reach_only_selected_provider(
    directory: Path,
    provider: _Provider,
    api: str,
    route: str,
    upstream: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    native = _body(api)
    if api == "openai-responses":
        native["background"] = False
    with approved_model_broker(
        directory, _selected(provider, api), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        assert stat.S_IMODE(broker.socket_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(broker.socket_path.stat().st_mode) == 0o600
        with _client(broker.socket_path) as client:
            for _ in range(2):
                response = client.post(
                    route,
                    json=native,
                    headers={
                        "Authorization": "Bearer oh-goon-local",
                        "x-api-key": "oh-goon-local",
                        "X-Owner": "untrusted override",
                    },
                )
                assert response.status_code == 200
                assert response.content == STREAM
    assert not broker.socket_path.exists()
    assert len(provider.requests) == 2
    for path, headers, raw in provider.requests:
        assert path == upstream
        assert headers["host"] == provider.base_url.removeprefix("http://")
        assert headers["x-owner"] == "frozen"
        key = "x-api-key" if api == "anthropic-messages" else "authorization"
        assert headers[key] == (
            "host-only-secret" if api == "anthropic-messages" else "Bearer host-only-secret"
        )
        assert "oh-goon-local" not in str(headers)
        forwarded = json.loads(raw)
        assert forwarded["temperature"] == native["temperature"]
        assert forwarded["tools"] == native["tools"]
        assert forwarded["model"] == native["model"] and forwarded["stream"] is True
        encoded = raw.decode()
        assert EVIDENCE in encoded and "qualified=true" in encoded
        # JSON encodes line breaks, but the decoded native context is exact.
        assert SKILL in str(forwarded).replace("\\n", "\n")
        if api == "openai-responses":
            assert forwarded["background"] is False
            assert forwarded["input"][0]["role"] == "developer"
            assert forwarded["input"][1:] == native["input"]
        elif api == "anthropic-messages":
            assert forwarded["system"][1:] == native["system"]
            assert forwarded["messages"] == native["messages"]
        else:
            assert forwarded["messages"][1:] == native["messages"]


def test_stream_is_delivered_before_provider_completion(
    directory: Path, provider: _Provider
) -> None:
    provider.chunks = (b'data: {"delta":"first"}\n\n', b"data: [DONE]\n\n")
    provider.hold_after_first = True
    with approved_model_broker(
        directory, _selected(provider), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            with client.stream("POST", "/v1/chat/completions", json=_body()) as response:
                iterator = response.iter_raw()
                assert next(iterator) == provider.chunks[0]
                assert not provider.release.is_set()
                provider.release.set()
                assert b"".join(iterator) == provider.chunks[1]


@pytest.mark.parametrize(
    "attack",
    [
        "model",
        "missing-model",
        "server-tool",
        "mcp",
        "remote-image",
        "remote-file",
        "duplicate",
        "nonfinite",
        "foreign-auth",
        "foreign-api-key",
        "host",
        "proxy",
        "query",
        "other-route",
        "absolute",
        "context-budget",
        "input-budget",
        "tool-choice",
    ],
)
def test_malformed_or_expanded_authority_never_reaches_provider(
    directory: Path,
    provider: _Provider,
    attack: str,
) -> None:
    body = _body()
    headers: dict[str, str] = {"Content-Type": "application/json"}
    route = "/v1/chat/completions"
    if attack == "model":
        body["model"] = "foreign/model"
    elif attack == "missing-model":
        body.pop("model")
    elif attack == "server-tool":
        body["tools"] = [{"type": "web_search_preview"}]
    elif attack == "tool-choice":
        body["tool_choice"] = {"type": "web_search_preview"}
    elif attack == "mcp":
        body["mcp_servers"] = [{"url": "https://external.test"}]
    elif attack == "remote-image":
        body["messages"][0]["content"] = [
            {"type": "image_url", "image_url": {"url": "https://external.test/image"}}
        ]
    elif attack == "remote-file":
        body["messages"][0]["content"] = [
            {"type": "input_file", "file_url": "https://external.test/file"}
        ]
    elif attack == "foreign-auth":
        headers["Authorization"] = "Bearer foreign-secret"
    elif attack == "foreign-api-key":
        headers["x-api-key"] = "foreign-secret"
    elif attack == "host":
        headers["Host"] = "external.test"
    elif attack == "proxy":
        headers["Proxy-Authorization"] = "Basic foreign-secret"
    elif attack == "query":
        route += "?url=https://external.test"
    elif attack == "other-route":
        route = "/v1/responses"
    elif attack == "absolute":
        route = "http://external.test/v1/chat/completions"
    elif attack == "context-budget":
        body["messages"][0]["content"] = "x" * 600
    elif attack == "input-budget":
        body["messages"][0]["content"] = "x" * 2000
    if attack == "nonfinite":
        body.pop("temperature")
    raw = json.dumps(body).encode()
    if attack == "context-budget":
        assert len(raw) <= 1024
    if attack == "duplicate":
        raw = raw[:-1] + b',"model":"approved/model"}'
    elif attack == "nonfinite":
        raw = raw[:-1] + b',"temperature":NaN}'
    selection = _selected(provider, max_request_bytes=1024)
    with approved_model_broker(
        directory, selection, repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post(route, content=raw, headers=headers)
            assert response.status_code >= 400
    assert provider.requests == []


@pytest.mark.parametrize("background", [True, None, 0, "false"])
def test_background_execution_rejects_before_provider_work(
    directory: Path, provider: _Provider, background: object
) -> None:
    body = _body("openai-responses")
    body["background"] = background
    with approved_model_broker(
        directory,
        _selected(provider, "openai-responses"),
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post("/v1/responses", json=body)
            assert response.status_code == 400
    assert provider.requests == []


def test_namespace_custom_tools_and_inline_images_remain_native_data(
    directory: Path, provider: _Provider
) -> None:
    body = _body()
    body["tools"] = [
        {
            "type": "namespace",
            "name": "coding",
            "tools": [{"type": "custom", "name": "apply_patch"}],
        }
    ]
    body["messages"][0]["content"] = [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}
    ]
    with approved_model_broker(
        directory, _selected(provider), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            assert client.post("/v1/chat/completions", json=body).status_code == 200
    assert json.loads(provider.requests[0][2])["tools"] == body["tools"]


@pytest.mark.parametrize("mode", ["redirect", "declared-bound", "stream-bound", "compressed"])
def test_provider_redirects_and_response_expansion_fail_closed(
    directory: Path, provider: _Provider, mode: str
) -> None:
    if mode == "redirect":
        provider.status = 307
        provider.headers["Location"] = provider.base_url + "/unauthorized"
    elif mode == "declared-bound":
        provider.headers["Content-Length"] = "4096"
    elif mode == "stream-bound":
        provider.chunks = (b"x" * 512, b"y" * 1024)
    else:
        provider.headers["Content-Encoding"] = "gzip"
    with approved_model_broker(
        directory,
        _selected(provider, max_response_bytes=1024),
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
    ) as broker:
        with _client(broker.socket_path) as client:
            if mode == "stream-bound":
                with pytest.raises(httpx2.RemoteProtocolError):
                    client.post("/v1/chat/completions", json=_body())
            else:
                response = client.post("/v1/chat/completions", json=_body())
                assert response.status_code == 502
                assert "host-only-secret" not in response.text
    assert len(provider.requests) == 1


def test_selected_model_catalog_and_raw_loopback_relay(
    directory: Path, provider: _Provider
) -> None:
    with approved_model_broker(
        directory, _selected(provider), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with model_inference_relay(broker.socket_path, port=0) as relay:
            with httpx2.Client(trust_env=False) as client:
                response = client.get(relay.base_url + "/v1/models")
                assert [item["id"] for item in response.json()["data"]] == ["approved/model"]
                assert (
                    client.post(relay.base_url + "/v1/chat/completions", json=_body()).content
                    == STREAM
                )
                assert client.post(relay.base_url + "/unrestricted", json={}).status_code == 404
            port = relay.port
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.1)
    assert len(provider.requests) == 1


def test_cancellation_closes_inflight_http_and_private_socket(
    directory: Path, provider: _Provider
) -> None:
    provider.hold = True
    cancelled = threading.Event()
    done = threading.Event()
    failures: list[Exception] = []
    with approved_model_broker(
        directory,
        _selected(provider),
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
        cancel_requested=cancelled.is_set,
    ) as broker:

        def request() -> None:
            try:
                with _client(broker.socket_path) as client:
                    client.post("/v1/chat/completions", json=_body())
            except Exception as exc:
                failures.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=request)
        thread.start()
        assert provider.started.wait(2)
        started = time.monotonic()
        cancelled.set()
        assert done.wait(2)
        thread.join()
        assert time.monotonic() - started < 2
    assert failures
    assert not broker.socket_path.exists()
    assert not any(thread.name.startswith("oh-goon-inference") for thread in threading.enumerate())


def test_partial_http_request_does_not_prevent_broker_cleanup(
    directory: Path, provider: _Provider
) -> None:
    with approved_model_broker(
        directory, _selected(provider), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(broker.socket_path))
        client.sendall(
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nContent-Length: 50\r\nContent-Type: application/json\r\n\r\n{"
        )
    client.close()
    assert not broker.socket_path.exists() and not provider.requests


def test_only_explicit_provider_proxy_is_used(directory: Path, provider: _Provider) -> None:
    selected = _selected(
        provider, base_url="http://provider.invalid/v1", proxy_url=provider.base_url
    )
    with approved_model_broker(
        directory, selected, repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            assert client.post("/v1/chat/completions", json=_body()).content == STREAM
    assert provider.requests[0][0] == "http://provider.invalid/v1/chat/completions"
    assert provider.requests[0][1]["host"] == "provider.invalid"


def test_cancellation_after_first_stream_chunk_reports_incomplete_transport(
    directory: Path, provider: _Provider
) -> None:
    provider.hold_after_first = True
    provider.chunks = (b"data: first\n\n", b"data: complete\n\n")
    cancelled = threading.Event()
    with approved_model_broker(
        directory,
        _selected(provider),
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
        cancel_requested=cancelled.is_set,
    ) as broker:
        with _client(broker.socket_path) as client:
            with client.stream("POST", "/v1/chat/completions", json=_body()) as response:
                iterator = response.iter_raw()
                assert next(iterator) == provider.chunks[0]
                cancelled.set()
                with pytest.raises(httpx2.RemoteProtocolError):
                    next(iterator)


def test_cancelled_or_oversized_context_rejects_before_socket_creation(
    directory: Path, provider: _Provider
) -> None:
    target = directory / "uncreated"
    selected = _selected(provider, max_request_bytes=1024)
    with pytest.raises(AgentProviderError):
        with approved_model_broker(
            target,
            selected,
            repository_evidence=EVIDENCE,
            skill_instructions=SKILL,
            cancel_requested=lambda: True,
        ):
            pytest.fail("cancelled broker became available")
    with pytest.raises(AgentProviderError):
        with approved_model_broker(
            target,
            selected,
            repository_evidence=EVIDENCE,
            skill_instructions="x" * 1025,
        ):
            pytest.fail("oversized context became available")
    assert not target.exists()


@pytest.mark.process
def test_relay_cli_preserves_native_stdin_response_and_exit_status(
    directory: Path, provider: _Provider
) -> None:
    input_file = directory / "native-input"
    input_file.write_bytes(b"exact native input\n")
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    script = (
        "import sys,json,urllib.request; assert sys.stdin.buffer.read()==b'exact native input\\n'; "
        "request=urllib.request.Request(sys.argv[1]+'/v1/chat/completions', "
        "data=json.dumps({'model':'approved/model','stream':True,'messages':[{'role':'user','content':'native request'}]}).encode(), "
        "headers={'Content-Type':'application/json','Authorization':'Bearer oh-goon-local'}); "
        "response=urllib.request.urlopen(request); sys.stdout.buffer.write(response.read()); "
        "sys.stdout.buffer.flush(); response.close(); sys.exit(7)"
    )
    with approved_model_broker(
        directory, _selected(provider), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        result = run_command(
            sys.executable,
            "-m",
            "oh_goon.integrations.agent_inference_relay",
            "--socket",
            str(broker.socket_path),
            "--port",
            str(port),
            "--input-file",
            str(input_file),
            "--",
            sys.executable,
            "-S",
            "-c",
            script,
            f"http://127.0.0.1:{port}",
            timeout=5,
        )
    assert result.returncode == 7
    assert result.stdout.encode() == STREAM.rstrip()
    assert len(provider.requests) == 1
