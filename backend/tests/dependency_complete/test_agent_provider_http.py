import json
import traceback

import httpx2
import pytest

from oh_goon.core.models.agent_providers import (
    AgentMessage,
    AgentProviderError,
    AgentProvidersConfig,
)
from oh_goon.integrations.agent_provider_client import AgentProviderClient


def selection(api="openai-chat-completions", *, parameters=None, provider=None, model=None):
    config = AgentProvidersConfig.model_validate(
        {
            "default_model": "review",
            "providers": {
                "chosen": {
                    "api": api,
                    "base_url": "https://models.example/team/v1",
                    "api_key_env": "MODEL_KEY",
                    **(provider or {}),
                }
            },
            "models": {
                "review": {
                    "provider": "chosen",
                    "model": "org/native-model:revision",
                    "max_output_tokens": 17,
                    "parameters": parameters or {},
                    **(model or {}),
                }
            },
        }
    )
    return config.resolve(None, environment={"MODEL_KEY": "PRIVATE_KEY"})


def response_for(api):
    if api == "ollama":
        return {
            "model": "actual-model",
            "done": True,
            "done_reason": "stop",
            "message": {"role": "assistant", "content": "hello"},
        }
    if api == "openai-chat-completions":
        return {
            "model": "actual-model",
            "choices": [
                {"finish_reason": "stop", "message": {"content": "hello", "role": "assistant"}}
            ],
        }
    if api == "openai-responses":
        return {
            "model": "actual-model",
            "status": "completed",
            "output": [
                {"type": "reasoning", "summary": []},
                {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            ],
        }
    return {
        "model": "actual-model",
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": "hello"}],
    }


@pytest.mark.parametrize(
    ("api", "path"),
    [
        ("ollama", "/api/chat"),
        ("openai-chat-completions", "/chat/completions"),
        ("openai-responses", "/responses"),
        ("anthropic-messages", "/messages"),
    ],
)
def test_native_wire_protocol_authentication_model_and_parameters(api, path, monkeypatch):
    observed = []
    monkeypatch.setenv("HTTPS_PROXY", "http://unrequested.example:8888")
    monkeypatch.setenv("OPENAI_API_KEY", "UNSELECTED_KEY")
    selected = selection(
        api,
        parameters={"temperature": 0.2} if api != "ollama" else {"options": {"temperature": 0.2}},
    )

    def handler(request):
        observed.append(request)
        return httpx2.Response(200, json=response_for(api))

    reply = AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
        selected, [AgentMessage("system", "instructions"), AgentMessage("user", "explicit text")]
    )
    assert reply.text == "hello" and reply.response_model == "actual-model"
    assert reply.model == "org/native-model:revision"
    assert len(observed) == 1
    request = observed[0]
    assert str(request.url) == "https://models.example/team/v1" + path
    assert request.method == "POST"
    body = json.loads(request.content)
    assert body["model"] == selected.model and body["stream"] is False
    if api == "ollama":
        assert body["options"] == {"temperature": 0.2, "num_predict": 17}
        assert body["messages"][-1] == {"role": "user", "content": "explicit text"}
    elif api == "openai-chat-completions":
        assert body["max_tokens"] == 17 and body["temperature"] == 0.2
        assert body["messages"][-1]["content"] == "explicit text"
    elif api == "openai-responses":
        assert body["max_output_tokens"] == 17 and body["temperature"] == 0.2
        assert body["input"][-1]["content"] == "explicit text"
    else:
        assert body["system"] == "instructions" and body["max_tokens"] == 17
        assert body["messages"] == [{"role": "user", "content": "explicit text"}]
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["x-api-key"] == "PRIVATE_KEY"
        assert "authorization" not in request.headers
    if api != "anthropic-messages":
        assert request.headers["authorization"] == "Bearer PRIVATE_KEY"
    assert request.extensions["timeout"]["read"] == 60.0


def test_no_authentication_is_invented_and_new_chat_token_field_is_explicit():
    selected = selection(
        provider={"api_key_env": None}, model={"chat_token_parameter": "max_completion_tokens"}
    )
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json=response_for("openai-chat-completions"))

    AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
        selected, [AgentMessage("user", "hello")]
    )
    assert "authorization" not in requests[0].headers
    body = json.loads(requests[0].content)
    assert body["max_completion_tokens"] == 17 and "max_tokens" not in body


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 429, 500])
def test_http_failures_are_not_retried_followed_or_echoed(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(
            status,
            headers={"Location": "https://another.example/steal"},
            text="PRIVATE_KEY PRIVATE_PROMPT",
        )

    with pytest.raises(AgentProviderError, match=f"HTTP {status}") as error:
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(), [AgentMessage("user", "PRIVATE_PROMPT")]
        )
    assert len(requests) == 1
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize("error_type", [httpx2.ConnectError, httpx2.ReadTimeout])
def test_network_and_timeout_errors_are_sanitized(error_type):
    def handler(request):
        raise error_type("PRIVATE_KEY PRIVATE_PROMPT", request=request)

    messages = [AgentMessage("user", "PRIVATE_PROMPT")]
    with pytest.raises(AgentProviderError) as error:
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(selection(), messages)
    assert "PRIVATE" not in "".join(traceback.format_exception(error.value))


class TrackingStream(httpx2.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.read_count = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    def close(self):
        self.closed = True


def test_oversized_response_stops_reading_and_closes_the_stream():
    stream = TrackingStream([b"x" * 65_536, b"must not read"])

    def handler(request):
        return httpx2.Response(200, stream=stream)

    with pytest.raises(AgentProviderError, match="response exceeds"):
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(provider={"max_response_bytes": 1024}), [AgentMessage("user", "hello")]
        )
    assert stream.closed and stream.read_count == 1


@pytest.mark.parametrize("headers", [{"Content-Length": "99999999"}, {"Content-Encoding": "gzip"}])
def test_ineligible_response_is_refused_before_body_decode_or_accumulation(headers):
    stream = TrackingStream([b"must not read"])

    def handler(request):
        return httpx2.Response(200, headers=headers, stream=stream)

    with pytest.raises(AgentProviderError):
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(), [AgentMessage("user", "hello")]
        )
    assert stream.closed and stream.read_count == 0


@pytest.mark.parametrize("length", [1024, 2048])
def test_oversized_request_fails_before_transport(length):
    def handler(request):
        pytest.fail("oversized prompt must not reach transport")

    with pytest.raises(AgentProviderError, match="request exceeds"):
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(provider={"max_request_bytes": 1024}), [AgentMessage("user", "x" * length)]
        )


@pytest.mark.parametrize(
    "payload",
    [b"not json", b'{"error":"PRIVATE_KEY"}', b'{"choices":[],"choices":[]}', b'{"x":NaN}'],
)
def test_malformed_or_error_payloads_are_not_success(payload):
    def handler(request):
        return httpx2.Response(200, content=payload)

    with pytest.raises(AgentProviderError) as error:
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(), [AgentMessage("user", "hello")]
        )
    assert "PRIVATE_KEY" not in str(error.value)


@pytest.mark.parametrize(
    ("api", "payload"),
    [
        ("ollama", {"done": False, "message": {"content": "partial"}}),
        ("ollama", {"done": True, "done_reason": "length", "message": {"content": "partial"}}),
        (
            "openai-chat-completions",
            {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
        ),
        (
            "openai-chat-completions",
            {
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {"content": None, "tool_calls": [{}]},
                    }
                ]
            },
        ),
        (
            "openai-chat-completions",
            {"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
        ),
        ("openai-responses", {"status": "incomplete", "output": []}),
        ("openai-responses", {"status": "completed", "output": [{"type": "function_call"}]}),
        (
            "anthropic-messages",
            {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "partial"}]},
        ),
    ],
)
def test_incomplete_tool_or_empty_responses_fail_explicitly(api, payload):
    def handler(request):
        return httpx2.Response(200, json=payload)

    with pytest.raises(AgentProviderError, match="completed text response"):
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(api), [AgentMessage("user", "hello")]
        )
