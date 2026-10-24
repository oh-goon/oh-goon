"""One explicit text request to a configured HTTP model provider.

Provider servers and SDKs are not dependencies. This client owns transport only;
model text does not launch tools, authorize disclosure, or certify execution.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Sequence
from typing import Any, NoReturn

from oh_goon.core.models.agent_providers import (
    AgentMessage,
    AgentProviderError,
    AgentReply,
    ResolvedAgentModel,
)


class AgentProviderClient:
    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport

    def complete(
        self, selected: ResolvedAgentModel, messages: Sequence[AgentMessage]
    ) -> AgentReply:
        """Use the selected snapshot once; no discovery, retries or fallback."""
        endpoint, body = _request(selected, messages)
        try:
            content = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError):
            raise AgentProviderError("Model request must contain valid finite UTF-8 JSON") from None
        if len(content) > selected.provider.max_request_bytes:
            raise AgentProviderError("Model request exceeds the configured byte limit")
        headers = {name: value.get_secret_value() for name, value in selected.headers}
        headers.update({"Content-Type": "application/json", "Accept-Encoding": "identity"})
        if selected.provider.api == "anthropic-messages" and not any(
            name.lower() == "anthropic-version" for name in headers
        ):
            headers["anthropic-version"] = "2023-06-01"

        # Keep the required general HTTP dependency at this effect boundary.
        import httpx2

        try:
            verify = (
                ssl.create_default_context(cafile=selected.provider.ca_file)
                if selected.provider.ca_file is not None
                else True
            )
            with httpx2.Client(
                timeout=selected.provider.timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                proxy=selected.provider.proxy_url,
                verify=verify,
                transport=self._transport,
            ) as client:
                with client.stream("POST", endpoint, headers=headers, content=content) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise AgentProviderError(
                            f"Selected provider returned HTTP {response.status_code}; "
                            "check endpoint, credentials and provider availability"
                        )
                    if response.headers.get("Content-Encoding", "identity").lower() not in {
                        "",
                        "identity",
                    }:
                        raise AgentProviderError(
                            "Provider must honor the requested identity content encoding"
                        )
                    declared_size = response.headers.get("Content-Length", "")
                    if (
                        declared_size.isdecimal()
                        and int(declared_size) > selected.provider.max_response_bytes
                    ):
                        raise AgentProviderError("Model response exceeds the configured byte limit")
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=65_536):
                        if len(data) + len(chunk) > selected.provider.max_response_bytes:
                            raise AgentProviderError(
                                "Model response exceeds the configured byte limit"
                            )
                        data.extend(chunk)
        except httpx2.TimeoutException:
            raise AgentProviderError("Selected provider request timed out") from None
        except (httpx2.HTTPError, OSError, ValueError):
            raise AgentProviderError("Cannot communicate with the selected provider") from None
        try:
            payload = json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=_nonfinite)
        except (ValueError, UnicodeError, RecursionError):
            raise AgentProviderError("Provider returned invalid JSON") from None
        return _reply(selected, payload)


def _request(
    selected: ResolvedAgentModel, messages: Sequence[AgentMessage]
) -> tuple[str, dict[str, object]]:
    if (
        not messages
        or len(messages) > 256
        or any(
            not isinstance(message, AgentMessage)
            or message.role not in {"system", "user", "assistant"}
            or type(message.content) is not str
            or not message.content
            for message in messages
        )
    ):
        raise AgentProviderError("A model request requires 1..256 non-empty text messages")
    if sum(len(message.content) for message in messages) > selected.provider.max_request_bytes:
        raise AgentProviderError("Model request exceeds the configured byte limit")
    body = json.loads(selected.parameters_json)
    body["model"] = selected.model
    body["stream"] = False
    wire_messages = [{"role": message.role, "content": message.content} for message in messages]
    api = selected.provider.api
    if api == "ollama":
        options = body.get("options", {})
        if not isinstance(options, dict) or "num_predict" in options:
            raise AgentProviderError(
                "Ollama options must be a mapping; max_output_tokens owns num_predict"
            )
        body["options"] = {**options, "num_predict": selected.max_output_tokens}
        body["messages"] = wire_messages
        path = "/api/chat"
    elif api == "openai-chat-completions":
        body["messages"] = wire_messages
        body[selected.chat_token_parameter] = selected.max_output_tokens
        path = "/chat/completions"
    elif api == "openai-responses":
        body["input"] = wire_messages
        body["max_output_tokens"] = selected.max_output_tokens
        path = "/responses"
    else:
        ordinary: list[dict[str, str]] = []
        system = []
        for message in wire_messages:
            if message["role"] == "system":
                if ordinary:
                    raise AgentProviderError(
                        "Anthropic system messages must precede conversation messages"
                    )
                system.append(message["content"])
            else:
                ordinary.append(message)
        if not ordinary:
            raise AgentProviderError("Anthropic requests require a conversation message")
        if system:
            if "system" in body:
                raise AgentProviderError(
                    "Supply Anthropic system instructions through parameters or messages, not both"
                )
            body["system"] = "\n\n".join(system)
        body["messages"] = ordinary
        body["max_tokens"] = selected.max_output_tokens
        path = "/messages"
    return selected.provider.base_url + path, body


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("duplicate JSON field")
        result[name] = value
    return result


def _nonfinite(value: str) -> NoReturn:
    raise ValueError("nonfinite JSON value")


def _reply(selected: ResolvedAgentModel, payload: object) -> AgentReply:
    try:
        if not isinstance(payload, dict) or payload.get("error") is not None:
            raise ValueError("provider error")
        api = selected.provider.api
        if api == "ollama":
            if payload.get("done") is not True or payload.get("done_reason", "stop") != "stop":
                raise ValueError("incomplete response")
            text = payload["message"]["content"]
            if payload["message"].get("tool_calls"):
                raise ValueError("tool request")
            finish = payload.get("done_reason", "stop")
        elif api == "openai-chat-completions":
            choices = payload["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("expected one choice")
            choice = choices[0]
            finish = choice["finish_reason"]
            if finish != "stop" or choice.get("error") or choice["message"].get("tool_calls"):
                raise ValueError("incomplete response")
            text = choice["message"]["content"]
        elif api == "openai-responses":
            if payload.get("status") != "completed":
                raise ValueError("incomplete response")
            texts = []
            for item in payload["output"]:
                if item["type"] == "reasoning":
                    continue
                if item["type"] != "message":
                    raise ValueError("non-text response")
                for part in item["content"]:
                    if part["type"] != "output_text" or not isinstance(part["text"], str):
                        raise ValueError("non-text response")
                    texts.append(part["text"])
            text = "".join(texts)
            finish = "completed"
        else:
            finish = payload["stop_reason"]
            if finish not in {"end_turn", "stop_sequence"}:
                raise ValueError("incomplete response")
            texts = []
            for part in payload["content"]:
                if part["type"] in {"thinking", "redacted_thinking"}:
                    continue
                if part["type"] != "text" or not isinstance(part["text"], str):
                    raise ValueError("non-text response")
                texts.append(part["text"])
            text = "".join(texts)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty response")
        response_model = payload.get("model")
        if response_model is not None and not isinstance(response_model, str):
            raise ValueError("invalid model identity")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise AgentProviderError(
            "Provider did not return one completed text response; "
            "check its protocol, model, token limit and text-only parameters"
        ) from exc
    return AgentReply(
        selected.profile, selected.provider_name, selected.model, text, finish, response_model
    )
