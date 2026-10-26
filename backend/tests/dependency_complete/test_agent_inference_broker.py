from __future__ import annotations

import contextlib
import json
import socket
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
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
HOSTED_PROMPT_ID = "pmpt-fixture-private-identifier"
HOSTED_PROMPT_SECRET = "fixture-private-prompt-variable"


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
    respond: Callable[[bytes], tuple[int, tuple[bytes, ...]]] | None = None


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
            status, chunks = (
                state.respond(body) if state.respond is not None else (state.status, state.chunks)
            )
            with contextlib.suppress(OSError):
                self.send_response(status)
                for name, value in state.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                for index, chunk in enumerate(chunks):
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


@pytest.mark.parametrize("native_tools", [False, True], ids=["without-tools", "with-tools"])
@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"prompt": {"id": HOSTED_PROMPT_ID}}, id="id"),
        pytest.param(
            {
                "prompt": {
                    "id": HOSTED_PROMPT_ID,
                    "version": "7",
                    "variables": {"key": HOSTED_PROMPT_SECRET},
                }
            },
            id="version-and-variables",
        ),
        pytest.param({"prompt": None}, id="null"),
        pytest.param({"prompt": {}}, id="empty-object"),
        pytest.param({"prompt": {"version": "7"}}, id="missing-id"),
        pytest.param({"prompt": HOSTED_PROMPT_ID}, id="scalar"),
        pytest.param(
            {"prompt": {"id": HOSTED_PROMPT_ID}, "api": "openai-chat-completions"},
            id="request-api-spoof",
        ),
    ],
)
def test_responses_hosted_prompt_rejects_before_provider_work(
    directory: Path, provider: _Provider, native_tools: bool, fields: dict[str, Any]
) -> None:
    body = _body("openai-responses")
    body.update(fields)
    if not native_tools:
        body.pop("tools")
    with approved_model_broker(
        directory,
        _selected(provider, "openai-responses"),
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post("/v1/responses", json=body)
            assert response.status_code == 400
            assert HOSTED_PROMPT_ID not in response.text
            assert HOSTED_PROMPT_SECRET not in response.text
    assert provider.requests == []


@pytest.mark.parametrize(
    "causal_fields",
    [
        pytest.param({}, id="omitted"),
        pytest.param({"previous_response_id": None, "conversation": None}, id="null"),
        pytest.param({"previous_response_id": "resp-fixture-parent"}, id="previous-response"),
        pytest.param({"conversation": "conv-fixture-parent"}, id="conversation-string"),
        pytest.param({"conversation": {"id": "conv-fixture-parent"}}, id="conversation-object"),
    ],
)
def test_responses_prompt_data_and_causal_fields_remain_native_transport(
    directory: Path, provider: _Provider, causal_fields: dict[str, Any]
) -> None:
    """Transport preservation only; this provider fixture does not resolve causal state."""
    body = _body("openai-responses")
    body.update(causal_fields)
    body["instructions"] = 'Native instructions retain literal {"prompt": "ordinary data"}.'
    body["prompt_cache_key"] = "fixture:prompt:cache-key"
    body["input"][0]["content"] = [
        {"type": "input_text", "text": 'Inspect literal {"prompt": {"id": "ordinary-input-data"}}.'}
    ]
    body["tools"] = [
        {
            "type": "function",
            "name": "edit",
            "parameters": {
                "type": "object",
                "properties": {"prompt": {"type": "object"}},
                "examples": [{"prompt": {"id": "ordinary-schema-data"}}],
            },
        }
    ]
    body["input"].append(
        {
            "type": "function_call",
            "call_id": "edit-1",
            "name": "edit",
            "arguments": json.dumps(
                {"prompt": {"id": "ordinary-argument-data", "variables": {"prompt": "native data"}}}
            ),
        }
    )
    with approved_model_broker(
        directory,
        _selected(provider, "openai-responses"),
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post("/v1/responses", json=body)
            assert response.status_code == 200
            assert response.content == STREAM
    assert len(provider.requests) == 1
    forwarded = json.loads(provider.requests[0][2])
    assert forwarded["input"][0]["role"] == "developer"
    assert EVIDENCE in forwarded["input"][0]["content"]
    assert SKILL in forwarded["input"][0]["content"]
    assert forwarded["input"][1:] == body["input"]
    assert {key: value for key, value in forwarded.items() if key != "input"} == {
        key: value for key, value in body.items() if key != "input"
    }


@pytest.mark.parametrize(
    "reference_shape", ["previous-response", "conversation-string", "conversation-object"]
)
def test_optional_managed_history_remains_provider_owned_across_broker_lifetimes(
    directory: Path, provider: _Provider, reference_shape: str
) -> None:
    """Deliberate fixture state proves transport, not production history isolation.

    The local provider alone creates and resolves these fixture-owned handles.
    This does not exercise Conversations API creation or real authorization.
    """
    evidence_a = EVIDENCE + "; fixture-capture-A-canary"
    skill_a = SKILL + "\nfixture-Skill-A-canary"
    evidence_b = EVIDENCE + "; fixture-capture-B-canary"
    skill_b = SKILL + "\nfixture-Skill-B-canary"
    frozen_headers = {
        "OpenAI-Project": "fixture-project",
        "OpenAI-Organization": "fixture-organization",
    }
    selected = _selected(provider, "openai-responses", api_key_env=None, headers=frozen_headers)
    provider_history: dict[str, str] = {}
    retrievals: list[tuple[str | None, str | None]] = []
    replies: list[bytes] = []

    def reference(handle: str) -> dict[str, Any]:
        if reference_shape == "previous-response":
            return {"previous_response_id": handle}
        return {
            "conversation": {"id": handle} if reference_shape == "conversation-object" else handle
        }

    def stateful_reply(raw: bytes) -> tuple[int, tuple[bytes, ...]]:
        assert len(provider.requests) <= 8
        body = json.loads(raw)
        handle = (
            body.get("previous_response_id")
            if reference_shape == "previous-response"
            else body.get("conversation")
        )
        if isinstance(handle, dict):
            handle = handle["id"]
        assert handle is None or isinstance(handle, str)
        retrieved = provider_history.get(handle) if handle is not None else None
        retrievals.append((handle, retrieved))
        if handle is not None and handle not in provider_history:
            reply = b"fixture-private-provider-refusal"
            replies.append(reply)
            return 403, (reply,)
        next_handle = f"{'resp' if reference_shape == 'previous-response' else 'conv'}-fixture-{len(provider.requests)}"
        provider_history[next_handle] = body["input"][0]["content"]
        reply = (
            b"data: "
            + json.dumps(
                {"fixture_reference": next_handle, "fixture_retrieved_context": retrieved}
            ).encode()
            + b"\n\n"
        )
        replies.append(reply)
        return 200, (reply,)

    provider.respond = stateful_reply

    def send(
        client: httpx2.Client,
        causal: dict[str, Any],
        evidence: str,
        skill: str,
        *,
        rejected: bool = False,
    ) -> str:
        body = _body("openai-responses")
        body.update(causal)
        count = len(provider.requests)
        response = client.post(
            "/v1/responses",
            json=body,
            headers={
                "OpenAI-Project": "untrusted-project",
                "OpenAI-Organization": "untrusted-organization",
            },
        )
        assert len(provider.requests) == count + 1
        path, headers, raw = provider.requests[-1]
        assert path == "/provider/v1/responses"
        assert "authorization" not in headers
        assert all(headers[name.lower()] == value for name, value in frozen_headers.items())
        forwarded = json.loads(raw)
        assert forwarded["input"][0]["role"] == "developer"
        assert evidence in forwarded["input"][0]["content"]
        assert skill in forwarded["input"][0]["content"]
        assert forwarded["input"][1:] == body["input"]
        assert {key: value for key, value in forwarded.items() if key != "input"} == {
            key: value for key, value in body.items() if key != "input"
        }
        if evidence == evidence_b:
            assert b"fixture-capture-A-canary" not in raw
            assert b"fixture-Skill-A-canary" not in raw
        if rejected:
            assert response.status_code == 502
            assert "fixture-private-provider-refusal" not in response.text
            return ""
        assert response.status_code == 200 and response.content == replies[-1]
        return str(json.loads(response.content.removeprefix(b"data: "))["fixture_reference"])

    prior_threads = set(threading.enumerate())
    with approved_model_broker(
        directory,
        selected,
        repository_evidence=evidence_a,
        skill_instructions=skill_a,
        cancel_requested=lambda: False,
    ) as broker_a:
        workers_a = set(threading.enumerate()) - prior_threads
        assert workers_a
        with _client(broker_a.socket_path) as client:
            initial = send(client, {}, evidence_a, skill_a)
            captured_a = provider_history[initial]
            followup = send(client, reference(initial), evidence_a, skill_a)
            assert retrievals[-1:] == [(initial, captured_a)]
            fork = send(client, reference(initial), evidence_a, skill_a)
            assert len({initial, followup, fork}) == 3
            assert retrievals[-1:] == [(initial, captured_a)]
            send(client, {}, evidence_a, skill_a)
            assert retrievals[-1:] == [(None, None)]
            send(client, {"previous_response_id": None, "conversation": None}, evidence_a, skill_a)
            assert retrievals[-1:] == [(None, None)]
    assert not broker_a.socket_path.exists()
    assert all(not worker.is_alive() for worker in workers_a)
    with _client(broker_a.socket_path) as revoked:
        with pytest.raises(httpx2.ConnectError):
            revoked.post("/v1/responses", json=_body("openai-responses"))
    assert len(provider.requests) == 5

    prior_threads = set(threading.enumerate())
    with approved_model_broker(
        directory,
        selected,
        repository_evidence=evidence_b,
        skill_instructions=skill_b,
        cancel_requested=lambda: False,
    ) as broker_b:
        workers_b = set(threading.enumerate()) - prior_threads
        assert workers_b and broker_b.socket_path != broker_a.socket_path
        with _client(broker_b.socket_path) as client:
            send(client, {}, evidence_b, skill_b)
            assert retrievals[-1:] == [(None, None)]
            send(client, reference(initial), evidence_b, skill_b)
            assert retrievals[-1:] == [(initial, captured_a)]
            assert (
                json.loads(replies[-1].removeprefix(b"data: "))["fixture_retrieved_context"]
                == captured_a
            )
            send(
                client,
                reference("fixture-provider-rejected-handle"),
                evidence_b,
                skill_b,
                rejected=True,
            )
            assert retrievals[-1:] == [("fixture-provider-rejected-handle", None)]
    assert len(provider.requests) == len(retrievals) == 8
    assert not broker_b.socket_path.exists()
    assert all(not worker.is_alive() for worker in workers_b)


@pytest.mark.parametrize(
    ("api", "route"),
    [
        ("openai-chat-completions", "/v1/chat/completions"),
        ("anthropic-messages", "/v1/messages"),
        ("ollama", "/api/chat"),
    ],
)
def test_other_protocol_prompt_data_does_not_override_the_selected_api(
    directory: Path, provider: _Provider, api: str, route: str
) -> None:
    """The mock proves API-scoped transport, not provider support for an extension field."""
    body = _body(api)
    body["prompt"] = {"id": "ordinary-protocol-data"}
    body["api"] = "openai-responses"
    with approved_model_broker(
        directory, _selected(provider, api), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post(route, json=body)
            assert response.status_code == 200
            assert response.content == STREAM
    assert len(provider.requests) == 1
    forwarded = json.loads(provider.requests[0][2])
    assert forwarded["prompt"] == body["prompt"]
    assert forwarded["api"] == body["api"]


@pytest.mark.parametrize(
    ("api", "route", "attachment", "root_type"),
    [
        pytest.param(
            "openai-responses",
            "/v1/responses",
            {"type": "input_file", "file_id": "file-provider-owned"},
            None,
            id="responses-file",
        ),
        pytest.param(
            "openai-responses",
            "/v1/responses",
            {"type": "input_image", "file_id": "file-provider-owned"},
            None,
            id="responses-image",
        ),
        pytest.param(
            "openai-responses",
            "/v1/responses",
            {"type": "input_file", "file_id": "file-provider-owned"},
            "tool_use",
            id="responses-root-tool-use-spoof",
        ),
        pytest.param(
            "openai-chat-completions",
            "/v1/chat/completions",
            {"type": "file", "file": {"file_id": "file-provider-owned"}},
            None,
            id="chat-nested-file",
        ),
        pytest.param(
            "openai-chat-completions",
            "/v1/chat/completions",
            {"type": "file", "file_id": "file-provider-owned"},
            None,
            id="chat-typed-file",
        ),
        pytest.param(
            "anthropic-messages",
            "/v1/messages",
            {"type": "document", "source": {"type": "file", "file_id": "file-provider-owned"}},
            None,
            id="anthropic-document",
        ),
    ],
)
def test_provider_attachment_ids_reject_before_provider_work(
    directory: Path,
    provider: _Provider,
    api: str,
    route: str,
    attachment: dict[str, Any],
    root_type: str | None,
) -> None:
    body = _body(api)
    if root_type is not None:
        body["type"] = root_type
    content_key = "input" if api == "openai-responses" else "messages"
    body[content_key][0]["content"] = [
        {"type": "input_text" if api == "openai-responses" else "text", "text": "Inspect"},
        attachment,
    ]
    with approved_model_broker(
        directory, _selected(provider, api), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post(route, json=body)
            assert response.status_code == 400
    assert provider.requests == []


@pytest.mark.parametrize("native_tools", [False, True])
@pytest.mark.parametrize("options", [{}, None, False, {"search_context_size": "low"}])
def test_top_level_web_search_options_reject_before_provider_work(
    directory: Path, provider: _Provider, native_tools: bool, options: object
) -> None:
    body = _body()
    body["web_search_options"] = options
    if not native_tools:
        body.pop("tools")
    with approved_model_broker(
        directory, _selected(provider), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post("/v1/chat/completions", json=body)
            assert response.status_code == 400
    assert provider.requests == []


@pytest.mark.parametrize(
    ("api", "route"),
    [
        ("openai-chat-completions", "/v1/chat/completions"),
        ("openai-responses", "/v1/responses"),
        ("anthropic-messages", "/v1/messages"),
    ],
)
def test_inline_attachments_and_native_function_data_keep_stream_and_frozen_context(
    directory: Path, provider: _Provider, api: str, route: str
) -> None:
    body = _body(api)
    schema = {
        "type": "object",
        "properties": {"file_id": {"type": "string"}, "web_search_options": {"type": "object"}},
        "examples": [{"type": "input_file", "file_id": "ordinary-schema-data"}],
    }
    function = {"name": "edit", "parameters": schema}
    argument_data = {
        "type": "input_file",
        "file_id": "ordinary-argument-data",
        "image_url": "https://example.test/ordinary-argument-data",
        "web_search_options": {},
        "nested": [
            {"type": "document", "source": {"type": "file", "file_id": "ordinary-nested-data"}}
        ],
    }
    arguments = json.dumps(argument_data)
    if api == "openai-responses":
        content_key = "input"
        body["tools"] = [{"type": "function", **function}]
        content: list[dict[str, Any]] = [
            {
                "type": "input_file",
                "filename": "source.txt",
                "file_data": "data:text/plain;base64,YQ==",
            },
            {"type": "input_image", "image_url": "data:image/png;base64,YQ=="},
        ]
        invocation: dict[str, Any] = {
            "type": "function_call",
            "call_id": "edit-1",
            "name": "edit",
            "arguments": arguments,
        }
    elif api == "anthropic-messages":
        content_key = "messages"
        body["tools"] = [{"name": "edit", "input_schema": schema}]
        content = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "text/plain", "data": "YQ=="},
            },
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": "YQ=="},
            },
        ]
        invocation = {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "edit-1", "name": "edit", "input": argument_data}
            ],
        }
    else:
        content_key = "messages"
        body["tools"] = [{"type": "function", "function": function}]
        content = [
            {
                "type": "file",
                "file": {"filename": "source.txt", "file_data": "data:text/plain;base64,YQ=="},
            },
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}},
        ]
        invocation = {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "edit-1",
                    "type": "function",
                    "function": {"name": "edit", "arguments": arguments},
                }
            ],
        }
    body[content_key][0]["content"] = content
    body[content_key].append(invocation)
    with approved_model_broker(
        directory, _selected(provider, api), repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            response = client.post(route, json=body)
            assert response.status_code == 200
            assert response.content == STREAM
    assert len(provider.requests) == 1
    forwarded = json.loads(provider.requests[0][2])
    assert forwarded["tools"] == body["tools"]
    if api == "anthropic-messages":
        assert forwarded[content_key] == body[content_key]
        assert forwarded["system"][1:] == body["system"]
        context = forwarded["system"][0]["text"]
    else:
        assert forwarded[content_key][1:] == body[content_key]
        context = forwarded[content_key][0]["content"]
    assert EVIDENCE in context
    assert SKILL in context


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


def test_relay_preserves_response_after_peer_refuses_remaining_request_body(
    directory: Path,
) -> None:
    path = directory / "early-response.sock"
    headers = b"POST /unrestricted HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\n"
    expected = b"HTTP/1.1 404 Not Found\r\nContent-Length: 8\r\nConnection: close\r\n\r\nrejected"
    peer_ready = threading.Event()
    body_sent = threading.Event()
    failures: list[Exception] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(5)

        def respond() -> None:
            try:
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(5)
                    received = bytearray()
                    while not received.endswith(b"\r\n\r\n"):
                        chunk = connection.recv(len(headers) - len(received))
                        assert chunk
                        received.extend(chunk)
                    assert received == headers
                    connection.shutdown(socket.SHUT_RD)
                    peer_ready.set()
                    assert body_sent.wait(5)
                    connection.sendall(expected)
            except Exception as exc:
                failures.append(exc)

        worker = threading.Thread(target=respond)
        worker.start()
        try:
            with model_inference_relay(path, port=0) as relay:
                with socket.create_connection(("127.0.0.1", relay.port), timeout=5) as client:
                    client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    client.sendall(headers)
                    assert peer_ready.wait(5)
                    try:
                        client.sendall(b"{}")
                    finally:
                        body_sent.set()
                    received = bytearray()
                    while len(received) <= len(expected):
                        chunk = client.recv(len(expected) + 1 - len(received))
                        if not chunk:
                            break
                        received.extend(chunk)
                    assert received == expected
        finally:
            body_sent.set()
            worker.join(timeout=6)
    assert not worker.is_alive()
    assert not failures


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
