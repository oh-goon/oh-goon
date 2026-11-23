"""Pi RPC transport preserves native event/result authority without exposing controls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from oh_goon.core.errors import PiSessionRefusalCode
from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.core.models.fleet_plan_step_primitives import PLAN_CONTRACT_RETURN_CODE
from oh_goon.core.models.pi_session_storage import (
    PI_SESSION_NEW_FILE,
    PI_SESSION_OBSERVATION_RECORD,
    PI_SESSION_REFUSAL_RECORD,
    PiSessionNativeObservation,
    PiSessionSaveObservation,
)
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.integrations import agent_inference_relay
from oh_goon.integrations.agent_native_execution import NativeAgentEventParser
from oh_goon.integrations.agent_pi_rpc import PiRpcContinuation, PiRpcControl


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


def _persistent_start() -> PiRpcControl:
    control = PiRpcControl(b"prompt", persistent=True)
    _commands(control)
    assert (
        control.feed_stdout(
            _response(
                "oh-goon:state",
                "get_state",
                {
                    "sessionId": "native-session",
                    "sessionFile": str(PI_SESSION_NEW_FILE),
                    "messageCount": 0,
                },
            )
        )
        == ()
    )
    return control


@pytest.mark.parametrize("reverse", [False, True])
def test_persistent_rpc_observes_same_file_and_final_leaf_without_retaining_entries(
    reverse: bool,
) -> None:
    control = _persistent_start()
    assert control.read_input(1024) is None
    assert (
        control.feed_stdout(
            _response(
                "oh-goon:entries",
                "get_entries",
                {
                    "leafId": "setup-leaf",
                    "entries": [{"id": "setup-leaf", "type": "thinking_level_change"}],
                },
            )
        )
        == ()
    )
    [prompt] = _commands(control)
    assert prompt["id"] == "oh-goon:prompt"
    _ack(control)
    terminal = _line({"type": "agent_settled", "aborted": True})
    assert control.feed_stdout(terminal) == (terminal,)
    assert _commands(control) == [
        {"id": "oh-goon:final-state", "type": "get_state"},
        {"id": "oh-goon:final-entries", "type": "get_entries"},
    ]
    replies = [
        _response(
            "oh-goon:final-state",
            "get_state",
            {"sessionId": "native-session", "sessionFile": str(PI_SESSION_NEW_FILE)},
        ),
        _response(
            "oh-goon:final-entries",
            "get_entries",
            {
                "leafId": "native-leaf",
                "entries": [
                    {"type": "thinking_level_change", "id": "setup-leaf"},
                    {"type": "message", "id": "native-leaf", "private": "PRIVATE_TRANSCRIPT 🧪"},
                ],
            },
        ),
    ]
    if reverse:
        replies.reverse()
    assert control.feed_stdout(replies[0]) == ()
    assert control.read_input(1024) is None
    assert control.feed_stdout(replies[1]) == ()
    assert control.read_input(1024) == b""
    assert control.finish() == PiSessionSaveObservation(
        session_id="native-session",
        leaf_id="native-leaf",
        entry_count=2,
        entries_sha256=hashlib.sha256(
            b'{"id":"setup-leaf","type":"thinking_level_change"}\n'
            b'{"id":"native-leaf","private":"PRIVATE_TRANSCRIPT \\ud83e\\uddea","type":"message"}\n'
        ).hexdigest(),
    )


def test_persistent_rpc_refuses_existing_branch_before_admitting_prompt() -> None:
    control = _persistent_start()
    with pytest.raises(AgentProviderError, match="prior message entries"):
        control.feed_stdout(
            _response(
                "oh-goon:entries",
                "get_entries",
                {"leafId": "prior-leaf", "entries": [{"id": "prior-leaf", "type": "message"}]},
            )
        )
    assert control.read_input(1024) == b""


@pytest.mark.parametrize("message_count", [1, False, None])
def test_persistent_rpc_requires_a_native_empty_conversation(message_count: object) -> None:
    control = PiRpcControl(b"prompt", persistent=True)
    _commands(control)
    with pytest.raises(AgentProviderError, match="conversation history"):
        control.feed_stdout(
            _response(
                "oh-goon:state",
                "get_state",
                {
                    "sessionId": "native-session",
                    "sessionFile": str(PI_SESSION_NEW_FILE),
                    "messageCount": message_count,
                },
            )
        )
    assert control.read_input(1024) == b""


@pytest.mark.parametrize(
    ("success", "data"),
    [
        (None, None),
        (False, None),
        (True, {"leafId": None, "entries": []}),
        (True, {"leafId": "leaf", "entries": []}),
        (True, {"leafId": "leaf", "entries": [{"id": "other", "type": "message"}]}),
        (True, {"leafId": "leaf", "entries": [{"type": "message"}]}),
        (True, {"leafId": "leaf", "entries": ["invalid", {"id": "leaf", "type": "message"}]}),
    ],
)
def test_persistent_rpc_preserves_settlement_when_save_evidence_is_unavailable(
    success: bool | None, data: object
) -> None:
    control = _persistent_start()
    control.feed_stdout(
        _response("oh-goon:entries", "get_entries", {"leafId": None, "entries": []})
    )
    _commands(control)
    _ack(control)
    terminal = _line({"type": "agent_settled", "aborted": False})
    assert control.feed_stdout(terminal) == (terminal,)
    _commands(control)
    control.feed_stdout(
        _response(
            "oh-goon:final-state",
            "get_state",
            {"sessionId": "native-session", "sessionFile": str(PI_SESSION_NEW_FILE)},
        )
    )
    if success is not None:
        assert (
            control.feed_stdout(
                _line(
                    {
                        "id": "oh-goon:final-entries",
                        "type": "response",
                        "command": "get_entries",
                        "success": success,
                        "data": data,
                    }
                )
            )
            == ()
        )
        assert control.read_input(1024) == b""
    assert control.finish() is None


def test_persistent_rpc_refuses_a_session_path_outside_the_original_selected_file() -> None:
    control = PiRpcControl(b"prompt", persistent=True)
    _commands(control)
    with pytest.raises(AgentProviderError, match="exact selected private session file"):
        control.feed_stdout(
            _response(
                "oh-goon:state",
                "get_state",
                {"sessionId": "native-session", "sessionFile": "/workspace/session.jsonl"},
            )
        )
    assert control.read_input(1024) == b""


def test_native_pi_child_cannot_emit_private_relay_observation() -> None:
    observation = PiSessionSaveObservation(
        session_id="native-session",
        leaf_id="native-leaf",
        entry_count=1,
        entries_sha256="a" * 64,
    )
    private_record = _line(
        {"type": PI_SESSION_OBSERVATION_RECORD, "data": observation.model_dump(mode="json")}
    )
    control = _persistent_start()
    with pytest.raises(AgentProviderError, match="relay-owned control record"):
        control.feed_stdout(private_record)
    parser = NativeAgentEventParser("pi", pi_session_persistent=True)
    parser.feed(private_record[:30])
    parser.feed(private_record[30:])
    events = parser.finish(returncode=0)
    assert parser.pi_session_observation == observation
    assert events.event_count == 0 and events.observations == () and not events.settled


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


def _reopen_state() -> dict[str, Any]:
    return {
        "sessionId": "prior-session",
        "sessionFile": str(PI_SESSION_NEW_FILE),
        "messageCount": 2,
        "isStreaming": False,
        "isCompacting": False,
        "pendingMessageCount": 0,
        "model": {
            "provider": "oh-goon",
            "id": "current-model",
            "api": "openai-completions",
            "baseUrl": "http://127.0.0.1:18743/v1",
        },
    }


def _reopen_control() -> PiRpcControl:
    return PiRpcControl(
        b"fresh authorized task",
        continuation=PiRpcContinuation(
            prior=PiSessionNativeObservation(session_id="prior-session", leaf_id="prior-leaf"),
            model="current-model",
            api="openai-completions",
            base_url="http://127.0.0.1:18743/v1",
        ),
    )


@pytest.mark.parametrize(
    ("state_override", "model_override", "error", "code"),
    [
        (
            {"sessionId": "fresh-session"},
            {},
            "admitted native session",
            PiSessionRefusalCode.IDENTITY_MISMATCH,
        ),
        (
            {"sessionFile": "/tmp/unowned"},
            {},
            "exact selected private session file",
            PiSessionRefusalCode.IDENTITY_MISMATCH,
        ),
        (
            {"messageCount": 0},
            {},
            "qualified retained conversation history",
            PiSessionRefusalCode.HISTORY_UNQUALIFIED,
        ),
        ({"isCompacting": True}, {}, "was idle", None),
        ({"pendingMessageCount": None}, {}, "was idle", None),
        (
            {},
            {"provider": "prior-provider"},
            "current managed inference model",
            PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH,
        ),
        (
            {},
            {"id": "prior-model"},
            "current managed inference model",
            PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH,
        ),
        (
            {},
            {"api": "openai-responses"},
            "current managed inference model",
            PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH,
        ),
        (
            {},
            {"baseUrl": "https://previous.invalid"},
            "current managed inference model",
            PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH,
        ),
    ],
)
def test_continuation_rejects_mismatched_native_authority_without_prompt(
    state_override: dict[str, object],
    model_override: dict[str, object],
    error: str,
    code: PiSessionRefusalCode | None,
) -> None:
    control = _reopen_control()
    _commands(control)
    state = _reopen_state()
    state["model"].update(model_override)
    state.update(state_override)
    with pytest.raises(AgentProviderError, match=error):
        control.feed_stdout(_response("oh-goon:state", "get_state", state))
    assert control.read_input(8192) == b""
    assert control.refusal_code is code


@pytest.mark.parametrize(
    "entries",
    [
        {"leafId": "another-leaf", "entries": []},
        {"leafId": "prior-leaf", "entries": [{"type": "message"}]},
    ],
)
def test_continuation_refuses_an_unproved_branch_without_prompt(
    entries: dict[str, object],
) -> None:
    control = _reopen_control()
    _commands(control)
    control.feed_stdout(_response("oh-goon:state", "get_state", _reopen_state()))
    with pytest.raises(AgentProviderError, match="admitted native leaf"):
        control.feed_stdout(_response("oh-goon:entries", "get_entries", entries))
    assert control.read_input(8192) == b""
    assert control.refusal_code is PiSessionRefusalCode.IDENTITY_MISMATCH


@pytest.mark.parametrize(
    ("command", "success", "code"),
    [
        ("get_entries", False, PiSessionRefusalCode.HISTORY_UNQUALIFIED),
        ("get_state", False, None),
        ("get_entries", 0, None),
    ],
)
def test_only_a_correlated_native_history_refusal_establishes_the_reason(
    command: str, success: object, code: PiSessionRefusalCode | None
) -> None:
    control = _reopen_control()
    _commands(control)
    control.feed_stdout(_response("oh-goon:state", "get_state", _reopen_state()))
    with pytest.raises(AgentProviderError, match="refused or mismatched") as refusal:
        control.feed_stdout(
            _line(
                {
                    "type": "response",
                    "id": "oh-goon:entries",
                    "command": command,
                    "success": success,
                    "error": "private native diagnostic",
                }
            )
        )
    assert control.read_input(8192) == b""
    assert control.session_identity is None
    assert control.refusal_code is code
    assert "private native diagnostic" not in str(refusal.value)


def test_pi_refusal_record_is_private_to_the_relay_and_contains_only_a_known_code() -> None:
    code = PiSessionRefusalCode.IDENTITY_MISMATCH
    refusal = {"type": PI_SESSION_REFUSAL_RECORD, "code": code}
    control = _reopen_control()
    with pytest.raises(AgentProviderError, match="relay-owned control record"):
        control.feed_stdout(_line(refusal))
    assert control.refusal_code is None

    persistent = NativeAgentEventParser("pi", pi_session_persistent=True)
    persistent.feed(_line(refusal))
    received = persistent.finish(returncode=PLAN_CONTRACT_RETURN_CODE)
    assert persistent.pi_session_refusal is code
    assert received.event_count == 0 and received.observations == ()
    for unsupported in (
        {**refusal, "code": "/private/path"},
        {**refusal, "raw": "credential-bearing native output"},
    ):
        with pytest.raises(AgentProviderError, match="Invalid native Pi relay refusal"):
            NativeAgentEventParser("pi", pi_session_persistent=True).feed(_line(unsupported))
    with pytest.raises(AgentProviderError, match="Unexpected native relay control record"):
        NativeAgentEventParser("pi").feed(_line(refusal))


@pytest.mark.parametrize("native_returncode", [0, 13])
def test_relay_preserves_proved_native_refusal_without_private_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_returncode: int
) -> None:
    task = tmp_path / "input"
    task.write_bytes(b"fresh authorized task")
    authority = tmp_path / "continuation"
    authority.write_text(
        PiRpcContinuation(
            prior=PiSessionNativeObservation(session_id="prior-session", leaf_id="prior-leaf"),
            model="current-model",
            api="openai-completions",
            base_url="http://127.0.0.1:18743/v1",
        ).model_dump_json(),
        encoding="utf-8",
    )
    output: list[bytes] = []

    def observe_command(*argv: str, **options: Any) -> CommandResult:
        input_source = options["input_source"]
        assert isinstance(input_source, PiRpcControl)
        _commands(input_source)
        state = _reopen_state()
        state["model"]["baseUrl"] = "https://private-retained-endpoint.invalid"
        options["stream_output"]("stdout", _response("oh-goon:state", "get_state", state))
        assert input_source.read_input(8192) == b""
        return CommandResult(
            argv=argv,
            returncode=native_returncode,
            stdout="",
            stderr="",
            supervisor=CommandSupervisorReceipt(final_process_group_quiescent=True),
        )

    monkeypatch.setattr(
        agent_inference_relay, "model_inference_relay", lambda *_args, **_kwargs: nullcontext()
    )
    monkeypatch.setattr(agent_inference_relay, "run_command", observe_command)
    monkeypatch.setattr(
        agent_inference_relay, "_write_all", lambda _fd, payload: output.append(payload)
    )
    assert agent_inference_relay.main(
        [
            "--socket",
            str(tmp_path / "relay"),
            "--input-file",
            str(task),
            "--pi-rpc",
            "--pi-session-continuation",
            str(authority),
            "--",
            "pi",
        ]
    ) == (native_returncode or PLAN_CONTRACT_RETURN_CODE)
    assert len(output) == 1
    parser = NativeAgentEventParser("pi", pi_session_persistent=True)
    parser.feed(output[0].decode("utf-8"))
    assert parser.pi_session_refusal is PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH
    assert b"private-retained-endpoint" not in output[0]


def test_continuation_correlates_old_leaf_and_returns_only_new_native_metadata() -> None:
    control = _reopen_control()
    assert _commands(control) == [
        {"id": "oh-goon:state", "type": "get_state"},
        {"id": "oh-goon:entries", "type": "get_entries", "since": "prior-leaf"},
    ]
    assert (
        control.feed_stdout(
            _response("oh-goon:entries", "get_entries", {"leafId": "prior-leaf", "entries": []})
        )
        == ()
    )
    assert control.read_input(8192) is None
    assert control.feed_stdout(_response("oh-goon:state", "get_state", _reopen_state())) == ()
    assert _commands(control) == [
        {"id": "oh-goon:prompt", "type": "prompt", "message": "fresh authorized task"},
    ]
    _ack(control)
    control.feed_stdout(_line({"type": "agent_settled", "aborted": False}))
    assert _commands(control) == [
        {"id": "oh-goon:final-state", "type": "get_state"},
        {"id": "oh-goon:final-entries", "type": "get_entries", "since": "prior-leaf"},
    ]
    assert control.feed_stdout(_response("oh-goon:final-state", "get_state", _reopen_state())) == ()
    assert (
        control.feed_stdout(
            _response(
                "oh-goon:final-entries",
                "get_entries",
                {
                    "leafId": "advanced-leaf",
                    "entries": [
                        {"type": "message", "id": "advanced-leaf", "private": "current turn text"}
                    ],
                },
            )
        )
        == ()
    )
    assert control.finish() == PiSessionSaveObservation(
        session_id="prior-session",
        leaf_id="advanced-leaf",
        entry_count=1,
        entries_sha256=hashlib.sha256(
            b'{"id":"advanced-leaf","private":"current turn text","type":"message"}\n'
        ).hexdigest(),
    )
