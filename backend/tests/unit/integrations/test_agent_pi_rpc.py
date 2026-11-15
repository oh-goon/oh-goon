"""Pi RPC transport preserves native event/result authority without exposing controls."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.integrations.agent_native_execution import NativeAgentEventParser
from oh_goon.integrations.agent_pi_rpc import PiRpcControl


@pytest.mark.parametrize(
    "record",
    [
        '{"type":"response","id":"oh-goon:state","command":"get_state","success":true,"data":{"sessionId":"one","sessionId":"two"}}\n',
        '{"type":"response","id":"oh-goon:state","command":"get_state","success":true,"data":{"sessionId":"native","other":NaN}}\n',
        '{"type":"response","id":"oh-goon:state","command":"get_state","success":true,"data":{"sessionId":"native","other":Infinity}}\n',
    ],
)
def test_rpc_rejects_ambiguous_or_nonfinite_native_json_before_prompt(record: str) -> None:
    control = PiRpcControl(b"task")
    with pytest.raises(AgentProviderError):
        control.feed_stdout(record)
    assert control.read_input(65536) == b""
    with pytest.raises(AgentProviderError):
        control.finish()


def test_rpc_missing_leaf_observation_is_not_an_observed_empty_leaf() -> None:
    control = PiRpcControl(b"prompt")
    control.feed_stdout(
        '{"type":"response","id":"oh-goon:state","command":"get_state",'
        '"success":true,"data":{"sessionId":"native","sessionFile":null}}\n'
    )
    with pytest.raises(AgentProviderError):
        control.feed_stdout(
            '{"type":"response","id":"oh-goon:entries","command":"get_entries",'
            '"success":true,"data":{"entries":[]}}\n'
        )
    assert control.read_input(65536) == b""


def _line(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"


def _response(identifier: str, command: str, data: object) -> str:
    return _line(
        {"id": identifier, "type": "response", "command": command, "success": True, "data": data}
    )


def _commands(control: PiRpcControl, *, maximum: int = 13) -> list[dict[str, Any]]:
    """Read the actual bounded pipe input, including records split across chunks."""
    wire = bytearray()
    while (chunk := control.read_input(maximum)) not in (None, b""):
        assert isinstance(chunk, bytes) and len(chunk) <= maximum
        wire.extend(chunk)
    assert not wire or wire.endswith(b"\n")
    return [json.loads(line) for line in bytes(wire).split(b"\n") if line]


def _startup(control: PiRpcControl, *, reverse: bool = False) -> dict[str, Any]:
    commands = _commands(control)
    assert len(commands) == 2
    assert {(item["id"], item["type"]) for item in commands} == {
        ("oh-goon:state", "get_state"),
        ("oh-goon:entries", "get_entries"),
    }
    responses = [
        _response("oh-goon:state", "get_state", {"sessionId": "fresh-native-session"}),
        _response(
            "oh-goon:entries",
            "get_entries",
            {"entries": [{"native-opaque-setup": "CONTROL_PRIVATE"}], "leafId": None},
        ),
    ]
    if reverse:
        responses.reverse()
    assert control.feed_stdout(responses[0]) == ()
    assert control.read_input(1024) is None
    assert control.feed_stdout(responses[1]) == ()
    [prompt] = _commands(control)
    assert prompt["id"] == "oh-goon:prompt" and prompt["type"] == "prompt"
    return prompt


def _ack(control: PiRpcControl, disposition: str = "started") -> None:
    assert (
        control.feed_stdout(_response("oh-goon:prompt", "prompt", {"disposition": disposition}))
        == ()
    )


def _final_identity(control: PiRpcControl) -> None:
    assert _commands(control) == [{"id": "oh-goon:final-state", "type": "get_state"}]
    assert (
        control.feed_stdout(
            _response(
                "oh-goon:final-state",
                "get_state",
                {"sessionId": "fresh-native-session", "sessionFile": None},
            )
        )
        == ()
    )
    assert control.read_input(1024) == b""
    control.finish()


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("disposition", ["started", "queued"])
def test_rpc_waits_for_correlated_startup_acceptance_settlement_and_final_identity(
    reverse: bool, disposition: str
) -> None:
    prompt = 'Literal /not-an-oh-goon-command @file\n"quoted" 🧪\u2028one\u2029two'
    control = PiRpcControl(prompt.encode())
    assert _startup(control, reverse=reverse) == {
        "id": "oh-goon:prompt",
        "type": "prompt",
        "message": prompt,
    }
    _ack(control, disposition)
    low_level_end = _line({"type": "agent_end", "messages": [], "willRetry": False})
    assert control.feed_stdout(low_level_end) == (low_level_end,)
    assert control.read_input(1024) is None
    settled = _line({"type": "agent_settled", "aborted": False})
    assert control.feed_stdout(settled) == (settled,)
    _final_identity(control)


def test_fast_native_settlement_waits_for_delayed_prompt_acceptance() -> None:
    control = PiRpcControl(b"prompt")
    _startup(control)
    settled = _line({"type": "agent_settled", "aborted": False})
    assert control.feed_stdout(settled) == (settled,)
    assert control.read_input(1024) is None
    _ack(control)
    _final_identity(control)


def test_rpc_frames_only_lf_and_forwards_native_events_without_control_transcripts() -> None:
    control = PiRpcControl(b"prompt")
    _startup(control)
    _ack(control)
    event = '{ "type": "message_update", "literal": "private 🧪\u2028A\u2029B" }'
    assert control.feed_stdout(event[:31]) == ()
    assert control.feed_stdout(event[31:] + "\r") == ()
    forwarded = control.feed_stdout("\n")
    assert len(forwarded) == 1
    assert forwarded[0].removesuffix("\n").removesuffix("\r") == event
    assert "CONTROL_PRIVATE" not in forwarded[0]
    assert control.read_input(1024) is None


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"sessionId": None},
        {"sessionId": 1},
        {"sessionId": ""},
        {"sessionId": "   "},
        {"sessionId": "fresh-native-session", "sessionFile": "/private/old.jsonl"},
    ],
)
def test_rpc_refuses_invalid_or_persistent_initial_identity(data: dict[str, object]) -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(_response("oh-goon:state", "get_state", data))
    assert control.read_input(1024) == b""
    with pytest.raises(AgentProviderError):
        control.finish()


@pytest.mark.parametrize("leaf", [1, False, [], {}, ""])
def test_rpc_refuses_invalid_native_leaf_identity(leaf: object) -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(
            _response("oh-goon:entries", "get_entries", {"entries": [], "leafId": leaf})
        )
    with pytest.raises(AgentProviderError):
        control.finish()


@pytest.mark.parametrize(
    "observation",
    [
        {"isStreaming": True},
        {"isCompacting": True},
        {"pendingMessageCount": 1},
        {"isStreaming": "false"},
        {"pendingMessageCount": False},
    ],
)
def test_rpc_refuses_unowned_initial_activity_before_submitting_prompt(
    observation: dict[str, object],
) -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(
            _response(
                "oh-goon:state",
                "get_state",
                {"sessionId": "fresh-native-session", **observation},
            )
        )
    assert control.read_input(1024) == b""


def test_rpc_retains_native_identity_as_provenance_without_interpreting_setup_entries() -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    assert control.session_identity is None
    assert (
        control.feed_stdout(
            _response(
                "oh-goon:state",
                "get_state",
                {
                    "sessionId": "fresh-native-session",
                    "isStreaming": False,
                    "isCompacting": False,
                    "pendingMessageCount": 0,
                },
            )
        )
        == ()
    )
    assert control.session_identity is None
    assert (
        control.feed_stdout(
            _response(
                "oh-goon:entries",
                "get_entries",
                {"leafId": "native-leaf", "entries": [{"opaque-setup": {"future-field": 1}}]},
            )
        )
        == ()
    )
    assert control.session_identity == ("fresh-native-session", "native-leaf")
    [prompt] = _commands(control)
    assert prompt["message"] == "prompt"


@pytest.mark.parametrize(
    "response",
    [
        {"id": "foreign", "type": "response", "command": "get_state", "success": True},
        {"type": "response", "command": "get_state", "success": True},
        {
            "id": "oh-goon:state",
            "type": "response",
            "command": "get_entries",
            "success": True,
            "data": {"sessionId": "fresh-native-session"},
        },
        {
            "id": "oh-goon:state",
            "type": "response",
            "command": "get_state",
            "success": False,
            "error": "native refusal",
        },
        {
            "id": "oh-goon:state",
            "type": "response",
            "command": "get_state",
            "success": 1,
            "data": {"sessionId": "fresh-native-session"},
        },
    ],
)
def test_rpc_refuses_uncorrelated_wrong_command_or_failed_response(
    response: dict[str, object],
) -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(_line(response))
    assert control.read_input(1024) == b""
    with pytest.raises(AgentProviderError):
        control.finish()


def test_rpc_refuses_duplicate_control_response() -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    response = _response("oh-goon:state", "get_state", {"sessionId": "fresh-native-session"})
    assert control.feed_stdout(response) == ()
    with pytest.raises(AgentProviderError):
        control.feed_stdout(response)
    with pytest.raises(AgentProviderError):
        control.finish()


@pytest.mark.parametrize("disposition", ["handled", "unknown", None, 1])
def test_rpc_prompt_handling_cannot_fabricate_a_completed_run(disposition: object) -> None:
    control = PiRpcControl(b"prompt")
    _startup(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(_response("oh-goon:prompt", "prompt", {"disposition": disposition}))
    with pytest.raises(AgentProviderError):
        control.finish()


@pytest.mark.parametrize("case", ["missing-startup", "missing-ack", "agent-end", "missing-final"])
def test_rpc_physical_eof_cannot_replace_required_native_observations(case: str) -> None:
    control = PiRpcControl(b"prompt")
    if case != "missing-startup":
        _startup(control)
    if case in {"agent-end", "missing-final"}:
        _ack(control)
    if case == "agent-end":
        control.feed_stdout(_line({"type": "agent_end", "messages": [], "willRetry": False}))
    if case == "missing-final":
        control.feed_stdout(_line({"type": "agent_settled", "aborted": False}))
        _commands(control)
    with pytest.raises(AgentProviderError):
        control.finish()


@pytest.mark.parametrize(
    "data",
    [
        {"sessionId": "different-session", "sessionFile": None},
        {"sessionId": "fresh-native-session", "sessionFile": "/private/new.jsonl"},
    ],
)
def test_rpc_final_readback_refuses_native_identity_or_persistence_switch(
    data: dict[str, object],
) -> None:
    control = PiRpcControl(b"prompt")
    _startup(control)
    _ack(control)
    control.feed_stdout(_line({"type": "agent_settled", "aborted": False}))
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(_response("oh-goon:final-state", "get_state", data))
    with pytest.raises(AgentProviderError):
        control.finish()


@pytest.mark.parametrize("record", ["not-json\n", "[]\n", "null\n", '{"type":'])
def test_rpc_rejects_malformed_or_unfinished_native_framing(record: str) -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout(record)
        control.finish()


@pytest.mark.parametrize(
    "prompt",
    [
        pytest.param(b"x" * (1024 * 1024 + 1), id="raw-prompt-over-budget"),
        pytest.param(b"\x00" * (200 * 1024), id="encoded-wire-over-budget"),
        pytest.param(b"\xff", id="invalid-utf8"),
    ],
)
def test_rpc_bounds_and_decodes_prompt_before_queuing_native_work(prompt: bytes) -> None:
    with pytest.raises(AgentProviderError):
        PiRpcControl(prompt)


def test_rpc_rejects_oversized_unframed_stdout_before_retaining_it() -> None:
    control = PiRpcControl(b"prompt")
    _commands(control)
    with pytest.raises(AgentProviderError):
        control.feed_stdout("x" * (1024 * 1024 + 1))
    assert control.read_input(1024) == b""
    with pytest.raises(AgentProviderError):
        control.finish()


def test_rpc_completion_keeps_native_error_authoritative_despite_completed_looking_text() -> None:
    control = PiRpcControl(b"prompt")
    _startup(control)
    _ack(control)
    parser = NativeAgentEventParser("pi")
    for record in (
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Completed successfully"}],
                "stopReason": "error",
            },
        },
        {"type": "agent_end", "messages": [], "willRetry": False},
        {"type": "agent_settled", "aborted": False},
    ):
        native = _line(record)
        assert control.feed_stdout(native) == (native,)
        parser.feed(native)
    _final_identity(control)
    result = parser.finish(returncode=0)
    assert result.final_text == "Completed successfully"
    assert result.settled and result.failed
