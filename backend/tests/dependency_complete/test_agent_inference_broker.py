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
TASK = "HUG-36: inspect the admitted source, preserve α, and report the original task scope."
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
    provider: _Provider,
    api: str = "openai-chat-completions",
    *,
    allow_provider_history: bool = False,
    **limits: Any,
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
            "models": {
                "selected": {
                    "provider": "company",
                    "model": "approved/model",
                    "allow_provider_history": allow_provider_history,
                }
            },
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
        directory,
        _selected(provider, api),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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


@pytest.mark.parametrize(
    ("api", "route"),
    (
        ("openai-chat-completions", "/v1/chat/completions"),
        ("openai-responses", "/v1/responses"),
        ("anthropic-messages", "/v1/messages"),
        ("ollama", "/api/chat"),
    ),
)
def test_task_free_native_followup_and_compaction_keep_the_original_frozen_context(
    directory: Path, provider: _Provider, api: str, route: str
) -> None:
    initial = _body(api)
    content_key = "input" if api == "openai-responses" else "messages"
    initial[content_key] = [{"role": "user", "content": TASK}]
    followup = _body(api)
    if api == "openai-responses":
        followup[content_key] = [
            {"type": "function_call", "name": "inspect", "call_id": "call-1", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call-1", "output": "Source read."},
        ]
    elif api == "anthropic-messages":
        followup[content_key] = [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call-1", "name": "inspect", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call-1", "content": "Source read."}
                ],
            },
        ]
    else:
        followup[content_key] = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "inspect", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "Source read."},
        ]
    compaction = _body(api)
    compaction[content_key] = [
        {"role": "user", "content": "Summarize recent tool work into a native session checkpoint."}
    ]
    with approved_model_broker(
        directory,
        _selected(provider, api),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
    ) as broker:
        with _client(broker.socket_path) as client:
            assert client.post(route, json=initial).status_code == 200
            first = json.loads(provider.requests[-1][2])
            assert TASK in json.dumps(first, ensure_ascii=False)
            for phase, native in (("tool follow-up", followup), ("compaction", compaction)):
                assert TASK not in json.dumps(native, ensure_ascii=False)
                assert client.post(route, json=native).status_code == 200
                forwarded = json.loads(provider.requests[-1][2])
                context = (
                    forwarded["system"][0]["text"]
                    if api == "anthropic-messages"
                    else forwarded[content_key][0]["content"]
                )
                assert EVIDENCE in context and SKILL in context
                task_context = context.replace(EVIDENCE, "").replace(SKILL, "")
                assert TASK in task_context, f"Original admitted task was dropped during {phase}"
    assert len(provider.requests) == 3


@pytest.mark.parametrize("field", ("task", "repository_evidence", "skill_instructions"))
@pytest.mark.parametrize("invalid", ("", " \n", None))
def test_incomplete_frozen_context_refuses_before_socket_or_provider_work(
    directory: Path, provider: _Provider, field: str, invalid: object
) -> None:
    context: dict[str, Any] = {
        "task": TASK,
        "repository_evidence": EVIDENCE,
        "skill_instructions": SKILL,
    }
    context[field] = invalid
    unstarted = directory / "unstarted"
    with pytest.raises(AgentProviderError, match="complete frozen context"):
        with approved_model_broker(unstarted, _selected(provider), **context):
            pytest.fail("incomplete context created an inference channel")
    assert not unstarted.exists() and not provider.requests


def test_stream_is_delivered_before_provider_completion(
    directory: Path, provider: _Provider
) -> None:
    provider.chunks = (b'data: {"delta":"first"}\n\n', b"data: [DONE]\n\n")
    provider.hold_after_first = True
    with approved_model_broker(
        directory,
        _selected(provider),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        directory, selection, task=TASK, repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            fresh_body = _body()
            baseline = client.post("/v1/chat/completions", json=fresh_body)
            assert baseline.status_code == 200 and baseline.content == STREAM
            assert len(provider.requests) == 1
            provider.requests.clear()
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
        task=TASK,
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
        task=TASK,
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
    ],
)
def test_inline_responses_data_and_fresh_reset_fields_remain_supported(
    directory: Path, provider: _Provider, causal_fields: dict[str, Any]
) -> None:
    """Ordinary inline inputs remain usable without history authorization."""
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
                {
                    "prompt": {
                        "id": "ordinary-argument-data",
                        "variables": {"prompt": "native data"},
                    },
                    "conversation": {"id": "literal-function-data"},
                    "input": [{"type": "item_reference", "id": "literal-function-data"}],
                }
            ),
        }
    )
    with approved_model_broker(
        directory,
        _selected(provider, "openai-responses"),
        task=TASK,
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


_HISTORY_REFERENCE_SHAPES = (
    "previous-response",
    "conversation-string",
    "conversation-object",
    "typed-item",
    "bare-item",
    "null-type-item",
    "reasoning-id",
)


def _history_reference(shape: str, handle: str) -> dict[str, Any]:
    if shape == "previous-response":
        return {"previous_response_id": handle}
    if shape == "typed-item":
        return {"input": [{"type": "item_reference", "id": handle}]}
    if shape == "bare-item":
        return {"input": [{"id": handle}]}
    if shape == "null-type-item":
        return {"input": [{"type": None, "id": handle}]}
    if shape == "reasoning-id":
        return {"input": [{"type": "reasoning", "id": handle, "summary": []}]}
    if shape == "missing-item-id":
        return {"input": [{"type": "item_reference"}]}
    return {"conversation": {"id": handle} if shape == "conversation-object" else handle}


def _stateful_history_provider(provider: _Provider) -> dict[str, tuple[tuple[Any, ...], str]]:
    """The fixture provider alone owns reference creation, lookup and scope."""
    history: dict[str, tuple[tuple[Any, ...], str]] = {}

    def respond(raw: bytes) -> tuple[int, tuple[bytes, ...]]:
        assert len(provider.requests) <= 4
        path, headers, received = provider.requests[-1]
        assert received == raw
        scope = (path, headers.get("authorization"), headers.get("openai-project"))
        body = json.loads(raw)
        assert body["input"][0]["role"] == "developer"
        handle = body.get("previous_response_id") or body.get("conversation")
        if isinstance(handle, dict):
            handle = handle["id"]
        if handle is None:
            handle = next(
                (item["id"] for item in body["input"][1:] if item.get("id") is not None), None
            )
        if handle is None:
            handle = f"opaque-provider-reference-{len(provider.requests)}"
            history[handle] = scope, body["input"][0]["content"]
            retrieved = None
        else:
            stored = history.get(handle)
            if stored is None or stored[0] != scope:
                return 403, (b"PRIVATE_PROVIDER_HISTORY_REFUSAL host-only-secret",)
            retrieved = stored[1]
        return 200, (json.dumps({"id": handle, "history": retrieved}).encode(),)

    provider.respond = respond
    return history


def _fresh_history_request(
    directory: Path,
    selected: ResolvedAgentModel,
    *,
    label: str,
    causal: dict[str, Any] | None = None,
) -> httpx2.Response:
    with approved_model_broker(
        directory,
        selected,
        task=TASK + "; " + label,
        repository_evidence=EVIDENCE + "; " + label,
        skill_instructions=SKILL + "\n" + label,
    ) as broker:
        with _client(broker.socket_path) as client:
            return client.post(
                "/v1/responses", json={**_body("openai-responses"), **(causal or {})}
            )


@pytest.mark.parametrize("reset", [{}, {"previous_response_id": None, "conversation": None}])
def test_default_fresh_broker_does_not_automatically_inherit_provider_history(
    directory: Path, provider: _Provider, reset: dict[str, Any]
) -> None:
    history = _stateful_history_provider(provider)
    first = _fresh_history_request(
        directory,
        _selected(provider, "openai-responses", allow_provider_history=True),
        label="Run A",
    )
    selected = _selected(provider, "openai-responses")
    assert selected.allow_provider_history is False
    second = _fresh_history_request(directory, selected, label="Run B", causal=reset)
    assert first.status_code == second.status_code == 200
    initial = first.json()["id"]
    assert first.json()["history"] is None and second.json()["history"] is None
    assert initial != second.json()["id"]
    captured_a = history[initial][1]
    captured_b = history[second.json()["id"]][1]
    assert TASK + "; Run A" in captured_a
    assert TASK + "; Run B" in captured_b
    assert EVIDENCE + "; Run B" in captured_b and SKILL + "\nRun B" in captured_b
    assert captured_a not in captured_b and "allow_provider_history" not in captured_b
    assert len(provider.requests) == 2


@pytest.mark.parametrize("shape", (*_HISTORY_REFERENCE_SHAPES, "missing-item-id"))
def test_default_policy_denies_explicit_provider_references_before_any_history_lookup(
    directory: Path, provider: _Provider, shape: str
) -> None:
    history = _stateful_history_provider(provider)
    selected = _selected(provider, "openai-responses")
    with approved_model_broker(
        directory, selected, task=TASK, repository_evidence=EVIDENCE, skill_instructions=SKILL
    ) as broker:
        with _client(broker.socket_path) as client:
            seed = client.post("/v1/responses", json=_body("openai-responses"))
            assert seed.status_code == 200
            handle = seed.json()["id"]
            assert handle in history
            causal = _history_reference(shape, handle)
            same_run = client.post("/v1/responses", json={**_body("openai-responses"), **causal})
            assert same_run.status_code == 400
            assert same_run.json()["error"]["type"] == "inference_boundary"
            assert "allow_provider_history" in same_run.json()["error"]["message"]
            assert handle not in same_run.text
    for policy_override in ({}, {"allow_provider_history": True}):
        response = _fresh_history_request(
            directory, selected, label="Run B", causal={**causal, **policy_override}
        )
        assert response.status_code == 400
        assert "allow_provider_history" in response.json()["error"]["message"]
        assert history[handle][1] not in response.text
        assert "host-only-secret" not in response.text
    # The known A reference would retrieve provider history if it crossed the
    # broker. Both same-Run and foreign-Run uses fail without an ID registry.
    assert len(provider.requests) == 1


@pytest.mark.parametrize("shape", _HISTORY_REFERENCE_SHAPES)
def test_selected_execution_policy_can_explicitly_authorize_provider_history(
    directory: Path, provider: _Provider, shape: str
) -> None:
    history = _stateful_history_provider(provider)
    seed = _fresh_history_request(directory, _selected(provider, "openai-responses"), label="Run A")
    assert seed.status_code == 200
    handle = seed.json()["id"]
    allowed = _selected(provider, "openai-responses", allow_provider_history=True)
    response = _fresh_history_request(
        directory, allowed, label="Run B", causal=_history_reference(shape, handle)
    )
    assert response.status_code == 200
    assert response.json()["history"] == history[handle][1]
    forwarded = json.loads(provider.requests[-1][2])
    for key, value in _history_reference(shape, handle).items():
        assert (forwarded[key][1:] if key == "input" else forwarded[key]) == value
    current = forwarded["input"][0]["content"]
    assert TASK + "; Run B" in current
    assert EVIDENCE + "; Run B" in current and SKILL + "\nRun B" in current
    assert history[handle][1] not in current
    assert "allow_provider_history" not in forwarded
    for policy_override in (False, True):
        refused = _fresh_history_request(
            directory,
            allowed,
            label="Run B",
            causal={**_history_reference(shape, handle), "allow_provider_history": policy_override},
        )
        assert refused.status_code == 400
    assert len(provider.requests) == 2


@pytest.mark.parametrize("shape", _HISTORY_REFERENCE_SHAPES)
def test_authorized_history_does_not_override_a_provider_scope_refusal(
    directory: Path, provider: _Provider, shape: str
) -> None:
    history = _stateful_history_provider(provider)
    seed = _fresh_history_request(directory, _selected(provider, "openai-responses"), label="Run A")
    assert seed.status_code == 200
    handle = seed.json()["id"]
    selected = _selected(
        provider,
        "openai-responses",
        allow_provider_history=True,
        headers={"OpenAI-Project": "different-project"},
    )
    response = _fresh_history_request(
        directory, selected, label="Run B", causal=_history_reference(shape, handle)
    )
    assert response.status_code == 502
    assert all(
        private not in response.text
        for private in (history[handle][1], "PRIVATE_PROVIDER_HISTORY_REFUSAL", "host-only-secret")
    )
    assert len(provider.requests) == 2
    assert provider.requests[-1][1]["openai-project"] == "different-project"


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
        directory,
        _selected(provider, api),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        directory,
        _selected(provider, api),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        directory,
        _selected(provider),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        directory,
        _selected(provider, api),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        directory,
        _selected(provider),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        task=TASK,
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
        directory,
        _selected(provider),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        task=TASK,
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
        directory,
        _selected(provider),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
        directory, selected, task=TASK, repository_evidence=EVIDENCE, skill_instructions=SKILL
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
        task=TASK,
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
            task=TASK,
            repository_evidence=EVIDENCE,
            skill_instructions=SKILL,
            cancel_requested=lambda: True,
        ):
            pytest.fail("cancelled broker became available")
    with pytest.raises(AgentProviderError):
        with approved_model_broker(
            target,
            selected,
            task=TASK,
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
        directory,
        _selected(provider),
        task=TASK,
        repository_evidence=EVIDENCE,
        skill_instructions=SKILL,
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
