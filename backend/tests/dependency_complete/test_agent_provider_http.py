import json
import os
import traceback
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from tests.factories.agent_review import agent_review_case

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentMessage,
    AgentProviderError,
    AgentProvidersConfig,
    AgentResponseTruncated,
    ResolvedAgentModel,
)
from oh_goon.engine.command import run_command
from oh_goon.integrations.agent_provider_client import AgentProviderClient
from oh_goon.integrations.agent_review_execution import execute_provider_agent


def selection(
    api: str = "openai-chat-completions",
    *,
    parameters: dict[str, object] | None = None,
    provider: dict[str, object] | None = None,
    model: dict[str, object] | None = None,
) -> ResolvedAgentModel:
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


def response_for(api: str) -> dict[str, object]:
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


def usage_response_for(api: str, *, input_tokens: object = 13) -> dict[str, Any]:
    payload: dict[str, Any] = response_for(api)
    if api == "ollama":
        payload.update(prompt_eval_count=input_tokens, eval_count=5)
    elif api == "openai-chat-completions":
        payload["usage"] = {
            "prompt_tokens": input_tokens,
            "completion_tokens": 5,
            "total_tokens": 18,
            "prompt_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 4},
        }
    elif api == "openai-responses":
        payload["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": 5,
            "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 3, "cache_write_tokens": 2},
            "output_tokens_details": {"reasoning_tokens": 4},
        }
    else:
        payload["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": 5,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 2,
        }
    return payload


@pytest.mark.parametrize(
    "api", ["ollama", "openai-chat-completions", "openai-responses", "anthropic-messages"]
)
def test_provider_usage_preserves_reported_quantities_and_distinct_inclusion_semantics(api):
    client = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=usage_response_for(api)))
    )
    answer = client.complete(selection(api), [AgentMessage("user", "Task")])
    assert answer.text == "hello"
    assert answer.usage is not None
    usage = answer.usage.observations[0]
    assert usage.source == api and usage.scope == "request"
    assert usage.requested_model == "org/native-model:revision"
    assert usage.reported_model == "actual-model"
    assert usage.implementation_identity is None and usage.cost is None
    assert usage.input_tokens == 13 and usage.output_tokens == 5
    assert usage.completeness == "reported" and usage.unit == "tokens"
    if api == "ollama":
        assert usage.total_tokens is usage.reasoning_tokens is None
        assert usage.cache_read_tokens is usage.cache_write_tokens is None
        assert usage.input_semantics == usage.output_semantics == "unknown"
    else:
        assert usage.cache_read_tokens == 3 and usage.cache_write_tokens == 2
        assert usage.output_semantics == "includes_reasoning"
        if api == "anthropic-messages":
            assert usage.input_semantics == "excludes_cache"
            assert usage.total_tokens is usage.reasoning_tokens is None
        else:
            assert usage.input_semantics == "includes_cache"
            assert usage.total_tokens == 18 and usage.reasoning_tokens == 4


@pytest.mark.parametrize(
    ("api", "value"),
    [
        (api, value)
        for api in ("ollama", "openai-chat-completions", "openai-responses", "anthropic-messages")
        for value in (0, None)
    ]
    + [("openai-responses", value) for value in (True, 1.0, "13")],
)
def test_optional_usage_never_changes_text_authority_and_keeps_unknown_distinct_from_zero(
    api, value
):
    client = AgentProviderClient(
        transport=httpx2.MockTransport(
            lambda _: httpx2.Response(200, json=usage_response_for(api, input_tokens=value))
        )
    )
    answer = client.complete(selection(api), [AgentMessage("user", "Task")])
    assert answer.text == "hello" and answer.usage is not None
    usage = answer.usage.observations[0]
    assert usage.input_tokens == (0 if type(value) is int and value == 0 else None)
    assert usage.output_tokens == 5
    assert usage.completeness == (
        "reported" if value is None or type(value) is int and value == 0 else "partial"
    )


@pytest.mark.parametrize("usage", [None, {}, [], "not-a-count", {"input_tokens": True}])
def test_absent_or_malformed_usage_does_not_invent_zero_or_reject_a_completed_response(usage):
    payload: dict[str, Any] = response_for("openai-responses")
    payload["usage"] = usage
    answer = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    ).complete(selection("openai-responses"), [AgentMessage("user", "Task")])
    assert answer.text == "hello"
    if usage is None:
        assert answer.usage is None
    else:
        assert answer.usage is not None
        observation = answer.usage.observations[0]
        assert observation.input_tokens is observation.output_tokens is None
        assert observation.total_tokens is None
        assert observation.completeness == ("unknown" if usage == {} else "malformed")


@pytest.mark.parametrize("detail", [0, False, "3", [], -1])
def test_malformed_nested_usage_detail_is_unknown_instead_of_a_numeric_cache_count(detail):
    payload = usage_response_for("openai-responses")
    payload["usage"]["input_tokens_details"] = detail
    answer = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    ).complete(selection("openai-responses"), [AgentMessage("user", "Task")])
    assert answer.usage is not None
    observation = answer.usage.observations[0]
    assert observation.input_tokens == 13 and observation.total_tokens == 18
    assert observation.cache_read_tokens is observation.cache_write_tokens is None
    assert observation.completeness == "partial"


@pytest.mark.parametrize(
    "api", ["ollama", "openai-chat-completions", "openai-responses", "anthropic-messages"]
)
def test_truncated_or_failed_provider_response_retains_usage_without_changing_failure(api):
    payload = usage_response_for(api)
    if api == "ollama":
        payload["done_reason"] = "length"
    elif api == "openai-chat-completions":
        payload["choices"][0]["finish_reason"] = "length"
    elif api == "openai-responses":
        payload.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    else:
        payload["stop_reason"] = "max_tokens"
    client = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )
    with pytest.raises(AgentResponseTruncated) as truncated:
        client.complete(selection(api), [AgentMessage("user", "Task")])
    assert truncated.value.usage is not None
    assert truncated.value.usage.observations[0].output_tokens == 5
    payload["error"] = {"private_transcript": "PRIVATE_ERROR_TEXT"}
    with pytest.raises(AgentProviderError) as failed:
        client.complete(selection(api), [AgentMessage("user", "Task")])
    assert not isinstance(failed.value, AgentResponseTruncated)
    assert failed.value.usage is not None
    assert failed.value.usage.observations[0].output_tokens == 5
    assert "PRIVATE_ERROR_TEXT" not in str(failed.value)
    assert "PRIVATE_ERROR_TEXT" not in failed.value.usage.model_dump_json()


def test_provider_usage_ids_are_request_owned_and_exclude_raw_provider_handles_and_private_fields():
    payload = usage_response_for("openai-responses")
    payload.update(id="PRIVATE_REPEATED_RESPONSE_HANDLE", private_transcript="PRIVATE_TEXT")
    payload["usage"]["credential"] = "PRIVATE_CREDENTIAL"
    client = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )
    first = client.complete(selection("openai-responses"), [AgentMessage("user", "Task")])
    second = client.complete(selection("openai-responses"), [AgentMessage("user", "Task")])
    assert first.usage is not None and second.usage is not None
    first_observation, second_observation = (
        first.usage.observations[0],
        second.usage.observations[0],
    )
    assert first_observation.scope_identity != second_observation.scope_identity
    assert first_observation.observation_id != second_observation.observation_id
    assert all(
        value not in first.usage.model_dump_json()
        for value in (
            "PRIVATE_REPEATED_RESPONSE_HANDLE",
            "PRIVATE_TEXT",
            "PRIVATE_CREDENTIAL",
            "PRIVATE_KEY",
        )
    )


@pytest.mark.parametrize("reported_model", [None, "", " spaced ", "nonprintable\x01", "m" * 257])
def test_optional_usage_identity_cannot_turn_a_completed_text_response_into_failure(reported_model):
    payload = usage_response_for("openai-responses")
    payload["model"] = reported_model
    answer = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    ).complete(selection("openai-responses"), [AgentMessage("user", "Task")])
    assert answer.text == "hello" and answer.response_model == reported_model
    assert answer.usage is not None
    assert answer.usage.observations[0].reported_model is None
    assert answer.usage.observations[0].output_tokens == 5


@pytest.mark.parametrize("api", ["openai-chat-completions", "anthropic-messages"])
@pytest.mark.parametrize("outcome", ["completed", "truncated", "failed"])
def test_usage_model_identity_omits_resolved_credentials_without_changing_original_result(
    api, outcome
):
    selected = selection(api, model={"model": "requested/PRIVATE_KEY:model"})
    payload = usage_response_for(api)
    payload["model"] = "reported/PRIVATE_KEY:model"
    if outcome == "truncated":
        if api == "openai-chat-completions":
            payload["choices"][0]["finish_reason"] = "length"
        else:
            payload["stop_reason"] = "max_tokens"
    elif outcome == "failed":
        payload["error"] = {"private_text": "PRIVATE_FAILED_BODY"}
    client = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )
    if outcome == "completed":
        reply = client.complete(selected, [AgentMessage("user", "Task")])
        assert reply.text == "hello" and reply.response_model == payload["model"]
        usage = reply.usage
    else:
        expected = AgentResponseTruncated if outcome == "truncated" else AgentProviderError
        with pytest.raises(expected) as failure:
            client.complete(selected, [AgentMessage("user", "Task")])
        usage = failure.value.usage
    assert usage is not None
    observation = usage.observations[0]
    assert observation.requested_model is observation.reported_model is None
    assert observation.input_tokens == 13 and observation.output_tokens == 5
    assert "PRIVATE_KEY" not in usage.model_dump_json()
    assert "PRIVATE_FAILED_BODY" not in usage.model_dump_json()


def test_non_success_http_or_invalid_json_has_unknown_usage_and_retains_transport_refusal():
    for response in (
        httpx2.Response(403, json=usage_response_for("openai-responses")),
        httpx2.Response(200, content=b'{"usage":'),
    ):
        client = AgentProviderClient(
            transport=httpx2.MockTransport(lambda _, response=response: response)
        )
        with pytest.raises(AgentProviderError) as failure:
            client.complete(selection("openai-responses"), [AgentMessage("user", "Task")])
        assert failure.value.usage is None


@pytest.mark.parametrize(
    "api",
    ["ollama", "openai-chat-completions", "openai-responses", "anthropic-messages"],
)
def test_provider_output_limit_remains_a_typed_refusal(api: str) -> None:
    payload: dict[str, Any] = response_for(api)

    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    client = AgentProviderClient(transport=httpx2.MockTransport(handler))
    messages = [AgentMessage("user", "Selected task")]
    assert client.complete(selection(api), messages).text == "hello"
    if api == "ollama":
        payload["done_reason"] = "length"
    elif api == "openai-chat-completions":
        payload["choices"][0]["finish_reason"] = "length"
    elif api == "openai-responses":
        payload.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    else:
        payload["stop_reason"] = "max_tokens"
    with pytest.raises(AgentResponseTruncated):
        client.complete(selection(api), messages)


def test_unknown_incomplete_response_is_not_labeled_a_token_limit() -> None:
    payload = {"status": "incomplete", "incomplete_details": {"reason": "unknown"}}
    client = AgentProviderClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json=payload))
    )
    with pytest.raises(AgentProviderError) as error:
        client.complete(selection("openai-responses"), [AgentMessage("user", "Task")])
    assert not isinstance(error.value, AgentResponseTruncated)


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
    assert reply.usage is None
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


@pytest.mark.parametrize(
    "reference",
    [
        {"previous_response_id": "PRIVATE_HISTORY_REFERENCE"},
        {"conversation": "PRIVATE_HISTORY_REFERENCE"},
        {"conversation": {"id": "PRIVATE_HISTORY_REFERENCE"}},
    ],
)
def test_default_direct_client_cannot_send_history_from_a_forged_resolved_body(
    reference: dict[str, object],
) -> None:
    selected = replace(
        selection("openai-responses"), parameters_json=json.dumps(reference).encode()
    )

    def forbidden(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("Unauthorized provider history reached HTTP transport")

    with pytest.raises(AgentProviderError, match="allow_provider_history") as refused:
        AgentProviderClient(transport=httpx2.MockTransport(forbidden)).complete(
            selected, [AgentMessage("user", "PRIVATE_PROMPT")]
        )
    assert "PRIVATE_HISTORY_REFERENCE" not in str(refused.value)
    assert "PRIVATE_PROMPT" not in str(refused.value)


@pytest.mark.parametrize("permission", [False, True])
@pytest.mark.parametrize("wire_override", [False, True])
def test_direct_request_data_cannot_grant_or_override_selected_history_policy(
    permission: bool, wire_override: bool
) -> None:
    selected = replace(
        selection("openai-responses", model={"allow_provider_history": permission}),
        parameters_json=json.dumps({"allow_provider_history": wire_override}).encode(),
    )

    def forbidden(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("A request-side policy override reached HTTP transport")

    with pytest.raises(AgentProviderError, match="allow_provider_history"):
        AgentProviderClient(transport=httpx2.MockTransport(forbidden)).complete(
            selected, [AgentMessage("user", "current inline input")]
        )


@pytest.mark.parametrize(
    "reference",
    [
        {"previous_response_id": "authorized-reference"},
        {"conversation": "authorized-reference"},
        {"conversation": {"id": "authorized-reference"}},
    ],
)
@pytest.mark.parametrize("status", [200, 403])
def test_explicit_direct_history_permission_preserves_provider_decision(
    reference: dict[str, object], status: int
) -> None:
    selected = selection(
        "openai-responses", parameters=reference, model={"allow_provider_history": True}
    )
    observed: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return (
            httpx2.Response(200, json=response_for("openai-responses"))
            if status == 200
            else httpx2.Response(403, text="PRIVATE_KEY PRIVATE_PROVIDER_HISTORY")
        )

    client = AgentProviderClient(transport=httpx2.MockTransport(handler))
    messages = [AgentMessage("user", "current inline input")]
    if status == 200:
        assert client.complete(selected, messages).text == "hello"
    else:
        with pytest.raises(AgentProviderError, match="HTTP 403") as refused:
            client.complete(selected, messages)
        assert "PRIVATE" not in str(refused.value)
    assert len(observed) == 1
    body = json.loads(observed[0].content)
    assert all(body[key] == value for key, value in reference.items())
    assert "allow_provider_history" not in body
    assert body["input"] == [{"role": "user", "content": "current inline input"}]


@pytest.mark.process
def test_authorized_history_reaches_native_provider_review_worker_once(tmp_path: Path) -> None:
    observed: list[dict[str, object]] = []

    class RefusingProvider(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            observed.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = b"PRIVATE_PROVIDER_HISTORY_REFUSAL"
            self.send_response(403)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    provider = ThreadingHTTPServer(("127.0.0.1", 0), RefusingProvider)
    serving = Thread(target=provider.serve_forever, daemon=True)
    serving.start()
    try:
        # The fixture is shared with the existing typed phase acceptance tests.
        resolver, source, _, options = agent_review_case(tmp_path)
        connection = AgentConnection.select(
            AgentProvidersConfig.model_validate(
                {
                    "default_model": "review",
                    "providers": {
                        "chosen": {
                            "api": "openai-responses",
                            "base_url": f"http://127.0.0.1:{provider.server_port}",
                        }
                    },
                    "models": {
                        "review": {
                            "provider": "chosen",
                            "model": "synthetic/reviewer",
                            "allow_provider_history": True,
                            "parameters": {"previous_response_id": "authorized-reference"},
                        }
                    },
                }
            )
        )
        plan, deliveries, _ = resolver.resolve_frozen_agents(
            source, **{**options, "connection": connection}
        )
        step = plan.steps[0]
        scratch = tmp_path / "worker-scratch"
        scratch.mkdir()
        process: Any = SimpleNamespace(
            host_environment={},
            environment=dict(os.environ),
            paths=SimpleNamespace(tmp=scratch),
            command_runner=run_command,
        )
        runtime: Any = SimpleNamespace(cancel_requested=None, runtime_supervision=None)
        outcome = execute_provider_agent(
            step,
            deliveries[step.step_id],
            process,
            runtime,
            timeout=15,
            repository_evidence="Producer-qualified synthetic evidence",
        )
        assert outcome.returncode == 2
        assert "HTTP 403" in outcome.stderr
        assert "PRIVATE_PROVIDER_HISTORY_REFUSAL" not in outcome.stderr
        assert len(observed) == 1
        assert observed[0]["previous_response_id"] == "authorized-reference"
        assert "allow_provider_history" not in observed[0]
        assert not (scratch / "agent-provider-request.json").exists()
    finally:
        provider.shutdown()
        serving.join(timeout=5)
        provider.server_close()
        assert not serving.is_alive()


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

    with pytest.raises(AgentProviderError):
        AgentProviderClient(transport=httpx2.MockTransport(handler)).complete(
            selection(api), [AgentMessage("user", "hello")]
        )
