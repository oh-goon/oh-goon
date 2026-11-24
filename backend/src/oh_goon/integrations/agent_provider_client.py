"""One explicit text request to a configured HTTP model provider.

Provider servers and SDKs are not dependencies. This client owns transport only;
model text does not launch tools, authorize disclosure, or certify execution.
"""

from __future__ import annotations

import json
import os
import ssl
import stat
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NoReturn

from pydantic import SecretStr

from oh_goon.core.models.agent_providers import (
    MAX_PROVIDER_HEADER_VALUE_BYTES,
    PROVIDER_HISTORY_AUTHORIZATION_MESSAGE,
    AgentConnection,
    AgentMessage,
    AgentProviderError,
    AgentReply,
    AgentResponseTruncated,
    ProviderHistoryAuthorizationRequired,
    ResolvedAgentModel,
    validate_provider_history_policy,
)
from oh_goon.core.models.model_usage import (
    ModelUsageObservation,
    ModelUsageReport,
    reported_token_values,
)
from oh_goon.core.product_identity import stable_id


def _provider_key_path(reference: str) -> Path:
    path = Path(reference)
    if not path.is_absolute() or ".." in path.parts:
        raise AgentProviderError("Provider API key file requires its resolved absolute path")
    return path


def _require_regular_key_file(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("not a unique regular credential file")


def _credential_file_metadata(path: Path) -> os.stat_result:
    if path.parent.resolve() != path.parent:
        raise ValueError("credential file parent no longer matches its resolved path")
    metadata = path.stat(follow_symlinks=False)
    _require_regular_key_file(metadata)
    return metadata


def require_provider_key_file_separation(
    connection: AgentConnection | None, read_roots: Sequence[Path]
) -> None:
    """Keep selected credentials out of captured source and native-readable roots."""
    if connection is None or connection.provider.api_key_file is None:
        return
    path = _provider_key_path(connection.provider.api_key_file)
    try:
        overlaps = any(path.is_relative_to(root.resolve()) for root in read_roots)
    except (OSError, RuntimeError):
        raise AgentProviderError("Cannot establish selected provider API key file scope") from None
    if overlaps:
        raise AgentProviderError("Provider API key file overlaps an Agent source or tool root")
    try:
        # A hardlink can put the same credential bytes in a different pathname.
        # An absent key stays valid configuration until actual request execution.
        _credential_file_metadata(path)
    except FileNotFoundError:
        return
    except (OSError, ValueError, RuntimeError):
        raise AgentProviderError("Cannot establish selected provider API key file scope") from None


def resolve_agent_connection(
    connection: AgentConnection, environment: Mapping[str, str]
) -> ResolvedAgentModel:
    """Read only the selected credential file, once, before HTTP transport."""
    key = None
    if connection.provider.api_key_file is not None:
        path = _provider_key_path(connection.provider.api_key_file)
        limit = MAX_PROVIDER_HEADER_VALUE_BYTES + 2  # optional CRLF
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            before = _credential_file_metadata(path)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as source:
                opened = os.fstat(source.fileno())
                _require_regular_key_file(opened)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("credential file changed before opening")
                raw = source.read(limit + 1)
            if len(raw) > limit:
                raise ValueError("credential file exceeds limit")
            if raw.endswith(b"\r\n"):
                raw = raw.removesuffix(b"\r\n")
            else:
                raw = raw.removesuffix(b"\n")
            key = SecretStr(raw.decode("ascii"))
        except (OSError, ValueError, RuntimeError):
            raise AgentProviderError("Cannot load selected provider API key file") from None
    return connection.resolve(environment, file_api_key=key)


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

        # The original client owns one request, including a provider that repeats
        # an external response ID. Raw provider handles are never usage identity.
        request_identity = stable_id("model-request", uuid.uuid4().hex)

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
        usage = _usage(selected, payload, request_identity=request_identity)
        try:
            reply = _reply(selected, payload)
        except AgentProviderError as exc:
            exc.usage = usage
            raise
        return replace(reply, usage=usage)


def _usage_model_identity(value: object, *, protected_values: tuple[str, ...]) -> str | None:
    if (
        isinstance(value, str)
        and 1 <= len(value) <= 256
        and value == value.strip()
        and value.isprintable()
        and not any(protected in value for protected in protected_values)
    ):
        return value
    return None


def _usage_detail(raw: dict[str, Any], name: str, field: str) -> object:
    detail = raw.get(name)
    if detail is None:
        return None
    # An invalid optional detail is unknown, without invalidating valid totals
    # or the text outcome. The shared count parser records that loss honestly.
    return detail.get(field) if isinstance(detail, dict) else object()


def _usage(
    selected: ResolvedAgentModel,
    payload: object,
    *,
    request_identity: str,
) -> ModelUsageReport | None:
    if not isinstance(payload, dict):
        return None
    api = selected.provider.api
    raw = payload if api == "ollama" else payload.get("usage")
    if raw is None or (
        api == "ollama" and not any(name in payload for name in ("prompt_eval_count", "eval_count"))
    ):
        return None
    values: dict[str, object] = {}
    if isinstance(raw, dict):
        if api == "ollama":
            values = {
                "input_tokens": raw.get("prompt_eval_count"),
                "output_tokens": raw.get("eval_count"),
            }
        elif api == "openai-chat-completions":
            values = {
                "input_tokens": raw.get("prompt_tokens"),
                "output_tokens": raw.get("completion_tokens"),
                "total_tokens": raw.get("total_tokens"),
                "cache_read_tokens": _usage_detail(raw, "prompt_tokens_details", "cached_tokens"),
                "cache_write_tokens": _usage_detail(
                    raw, "prompt_tokens_details", "cache_write_tokens"
                ),
                "reasoning_tokens": _usage_detail(
                    raw, "completion_tokens_details", "reasoning_tokens"
                ),
            }
        elif api == "openai-responses":
            values = {
                "input_tokens": raw.get("input_tokens"),
                "output_tokens": raw.get("output_tokens"),
                "total_tokens": raw.get("total_tokens"),
                "cache_read_tokens": _usage_detail(raw, "input_tokens_details", "cached_tokens"),
                "cache_write_tokens": _usage_detail(
                    raw, "input_tokens_details", "cache_write_tokens"
                ),
                "reasoning_tokens": _usage_detail(raw, "output_tokens_details", "reasoning_tokens"),
            }
        else:
            values = {
                "input_tokens": raw.get("input_tokens"),
                "output_tokens": raw.get("output_tokens"),
                "cache_read_tokens": raw.get("cache_read_input_tokens"),
                "cache_write_tokens": raw.get("cache_creation_input_tokens"),
            }
    counts, completeness = reported_token_values(values)
    if not isinstance(raw, dict):
        completeness = "malformed"
    # These are the exact values already resolved for this request, not another
    # environment/configuration lookup. A model-name claim may echo credential
    # material, including the raw token rather than the full Authorization value.
    protected = set()
    for name, secret in selected.headers:
        value = secret.get_secret_value()
        if value:
            protected.add(value)
            if value.strip():
                protected.add(value.strip())
        if name.lower() == "authorization":
            parts = value.split(None, 1)
            if len(parts) == 2 and parts[1]:
                protected.add(parts[1])
                if parts[1].strip():
                    protected.add(parts[1].strip())
    protected_values = tuple(protected)
    observation = ModelUsageObservation(
        observation_id=stable_id("model-usage", request_identity),
        source=api,
        scope="request",
        scope_identity=request_identity,
        requested_model=_usage_model_identity(selected.model, protected_values=protected_values),
        reported_model=_usage_model_identity(
            payload.get("model"), protected_values=protected_values
        ),
        input_semantics=(
            "excludes_cache"
            if api == "anthropic-messages"
            else "includes_cache"
            if api.startswith("openai-")
            else "unknown"
        ),
        output_semantics="unknown" if api == "ollama" else "includes_reasoning",
        completeness=completeness,
        **counts,
    )
    return ModelUsageReport(observations=(observation,))


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
    try:
        validate_provider_history_policy(
            body, allow_provider_history=selected.allow_provider_history
        )
    except ProviderHistoryAuthorizationRequired:
        raise AgentProviderError(PROVIDER_HISTORY_AUTHORIZATION_MESSAGE) from None
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
            if payload.get("done") is True and payload.get("done_reason") == "length":
                raise AgentResponseTruncated("Selected provider reached its output-token limit")
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
            if finish == "length":
                raise AgentResponseTruncated("Selected provider reached its output-token limit")
            if finish != "stop" or choice.get("error") or choice["message"].get("tool_calls"):
                raise ValueError("incomplete response")
            text = choice["message"]["content"]
        elif api == "openai-responses":
            if (
                payload.get("status") == "incomplete"
                and isinstance(payload.get("incomplete_details"), dict)
                and payload["incomplete_details"].get("reason") == "max_output_tokens"
            ):
                raise AgentResponseTruncated("Selected provider reached its output-token limit")
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
            if finish == "max_tokens":
                raise AgentResponseTruncated("Selected provider reached its output-token limit")
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
