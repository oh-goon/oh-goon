import codecs
import json
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentName,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.integrations.agent_native_execution import (
    NativeAgentEventParser,
    codex_login_command,
    native_agent_command,
    parse_native_agent_events,
)


def selection(agent: NativeAgentName, *, arguments: tuple[str, ...] = ()) -> ResolvedNativeAgent:
    return ResolvedNativeAgent(
        agent=agent,
        profile_name="local",
        definition=NativeAgentDefinition(executable=f"/native/{agent}", arguments=arguments),
        profile=NativeAgentProfile(),
        executable_sha256="a" * 64,
    )


@pytest.mark.parametrize("alias", ["none", "home", "root"])
def test_codex_login_binds_native_model_instructions_and_tool_permissions(
    tmp_path: Path, alias: str
) -> None:
    home = tmp_path / "native-auth"
    capture = tmp_path / "capture"
    home.mkdir()
    capture.mkdir()
    if alias == "home":
        link = tmp_path / "home-link"
        link.symlink_to(home, target_is_directory=True)
        home = link
    elif alias == "root":
        link = tmp_path / "capture-link"
        link.symlink_to(capture, target_is_directory=True)
        capture = link
    selected = replace(
        selection("codex"),
        profile=NativeAgentProfile(
            codex_login=CodexLoginConnection(
                home=str(home),
                model="gpt-6-luna",
                reasoning_effort="low",
            )
        ),
    )
    prompt = tmp_path / "PRIVATE-prompt-🧪.txt"
    prompt.write_text("PRIVATE evidence and Skill")
    command = codex_login_command(
        selected,
        instructions=prompt,
        workspace=tmp_path / "repo",
        scratch=tmp_path / "scratch",
        evidence_state=tmp_path / "producer-state",
        read_roots=(capture,),
    )
    settings: dict[str, object] = {}
    for index, argument in enumerate(command.argv):
        if argument == "-c":
            settings.update(tomllib.loads(command.argv[index + 1]))
    assert settings["model"] == "gpt-6-luna"
    assert settings["model_reasoning_effort"] == "low"
    assert settings["model_instructions_file"] == str(prompt)
    assert settings["approval_policy"] == "never"
    permissions = settings["permissions"]
    assert isinstance(permissions, dict)
    assert permissions["oh_goon"]["network"]["enabled"] is False
    filesystem = permissions["oh_goon"]["filesystem"]
    assert filesystem[str(tmp_path / "repo")] == "write"
    assert filesystem[str(capture)] == "read"
    assert filesystem[str(tmp_path / "producer-state")] == "write"
    assert str(home) not in filesystem and ":root" not in filesystem
    assert dict(command.environment)["CODEX_HOME"] == str(home)
    assert "PRIVATE evidence" not in " ".join(command.argv)
    assert "--ignore-user-config" in command.argv and "--ignore-rules" in command.argv


@pytest.mark.parametrize("root_kind", ["read", "workspace", "scratch", "producer-state"])
@pytest.mark.parametrize("relation", ["equal", "home-in-root", "root-in-home"])
@pytest.mark.parametrize("alias", ["none", "home", "root"])
def test_codex_login_refuses_tool_root_that_exposes_native_authentication(
    tmp_path: Path,
    root_kind: str,
    relation: str,
    alias: str,
) -> None:
    home = tmp_path / "native-auth"
    root = {
        "equal": home,
        "home-in-root": tmp_path,
        "root-in-home": home / "public-runtime",
    }[relation]
    home.mkdir()
    root.mkdir(exist_ok=True)
    if alias == "home":
        link = tmp_path / "home-link"
        link.symlink_to(home, target_is_directory=True)
        home = link
    elif alias == "root":
        link = tmp_path / "root-link"
        link.symlink_to(root, target_is_directory=True)
        root = link
    selected = replace(
        selection("codex"),
        profile=NativeAgentProfile(
            codex_login=CodexLoginConnection(
                home=str(home),
                model="gpt-6-luna",
                reasoning_effort="low",
            )
        ),
    )
    with pytest.raises(AgentProviderError, match="authentication home"):
        codex_login_command(
            selected,
            instructions=tmp_path / "prompt",
            workspace=root if root_kind == "workspace" else tmp_path / "repo",
            scratch=root if root_kind == "scratch" else tmp_path / "scratch",
            read_roots=(root,) if root_kind == "read" else (),
            evidence_state=root if root_kind == "producer-state" else None,
        )


def events(*records: dict[str, object]) -> str:
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n"


@pytest.mark.parametrize("agent", ["pi", "codex", "opencode"])
def test_headless_prompt_stays_file_backed_and_native_model_arguments_survive(
    tmp_path: Path, agent: NativeAgentName
) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("PRIVATE REPOSITORY EVIDENCE")
    command = native_agent_command(
        selection(agent, arguments=("--model", "user/native-model")),
        prompt_file=prompt,
        workspace=tmp_path,
        interactive=False,
    )
    assert command.input_file == prompt
    assert "PRIVATE" not in " ".join(command.argv)
    assert "user/native-model" in command.argv
    if agent == "opencode":
        assert "--standalone" in command.argv and "json" in command.argv
    elif agent == "codex":
        assert "--no-daemon" in command.argv and command.argv[-1] == "-"
    else:
        assert "--no-session" in command.argv and "json" in command.argv


@pytest.mark.parametrize("agent", ["opencode", "pi"])
def test_interactive_native_prompt_is_additive_file_input(
    tmp_path: Path, agent: NativeAgentName
) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("PRIVATE")
    command = native_agent_command(
        selection(agent), prompt_file=prompt, workspace=tmp_path, interactive=True
    )
    assert command.input_file is None
    assert "PRIVATE" not in " ".join(command.argv)
    if agent == "pi":
        assert command.argv[-2:] == ("--append-system-prompt", str(prompt))
    else:
        assert json.loads(dict(command.environment)["OPENCODE_CONFIG_CONTENT"]) == {
            "instructions": [str(prompt)]
        }


def test_interactive_codex_requires_managed_evidence_delivery(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt"
    prompt.write_text("context")
    with pytest.raises(AgentProviderError, match="managed producer"):
        native_agent_command(
            selection("codex"), prompt_file=prompt, workspace=tmp_path, interactive=True
        )


@pytest.mark.parametrize(
    "agent,argument",
    [("opencode", "--server=http://host"), ("pi", "--mode=rpc"), ("codex", "--remote")],
)
def test_arguments_cannot_move_the_native_agent_outside_its_owned_transport(
    tmp_path: Path, agent: NativeAgentName, argument: str
) -> None:
    prompt = tmp_path / "prompt"
    prompt.write_text("context")
    with pytest.raises(AgentProviderError, match="owned terminal"):
        native_agent_command(
            selection(agent, arguments=(argument,)),
            prompt_file=prompt,
            workspace=tmp_path,
            interactive=False,
        )


@pytest.mark.parametrize("arguments", [("--api-key", "PRIVATE-KEY"), ("--api-key=PRIVATE-KEY",)])
def test_pi_literal_credentials_are_rejected_without_echoing_values(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    prompt = tmp_path / "prompt"
    prompt.write_text("context")
    with pytest.raises(AgentProviderError, match="credentials") as captured:
        native_agent_command(
            selection("pi", arguments=arguments),
            prompt_file=prompt,
            workspace=tmp_path,
            interactive=False,
        )
    assert "PRIVATE-KEY" not in str(captured.value)


def test_pi_settled_is_required_after_agent_end_and_tool_errors_can_recover() -> None:
    prefix = events(
        {"type": "agent_start"},
        {"type": "tool_execution_end", "isError": True, "toolCallId": "call"},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "recovered\u2028text"}],
                "stopReason": "stop",
            },
        },
        {"type": "agent_end"},
    )
    assert not parse_native_agent_events("pi", prefix, returncode=0).settled
    result = parse_native_agent_events(
        "pi", prefix + events({"type": "agent_settled", "aborted": False}), returncode=0
    )
    assert result.settled and not result.failed
    assert result.final_text == "recovered\u2028text"
    assert any(observation.kind == "tool" for observation in result.observations)


@pytest.mark.parametrize("stop_reason", ["error", "aborted", "length"])
def test_pi_failed_assistant_or_aborted_native_loop_remains_failure(stop_reason: str) -> None:
    result = parse_native_agent_events(
        "pi",
        events(
            {
                "type": "message_end",
                "message": {"role": "assistant", "content": "failure", "stopReason": stop_reason},
            },
            {"type": "agent_settled", "aborted": False},
        ),
        returncode=0,
    )
    assert result.failed
    assert any(
        observation.summary == f"pi: stop_reason={stop_reason}"
        for observation in result.observations
    )


def test_opencode_requires_the_runner_exit_and_does_not_reuse_previous_step_text() -> None:
    output = events(
        {"type": "step_start"},
        {"type": "text", "part": {"text": "old", "time": {"end": 123}}},
        {"type": "step_finish"},
    )
    assert not parse_native_agent_events("opencode", output).settled
    assert parse_native_agent_events("opencode", output, returncode=0).settled
    failed = parse_native_agent_events("opencode", output, returncode=1)
    assert failed.failed and not failed.settled
    result = parse_native_agent_events(
        "opencode", output + events({"type": "step_start"}, {"type": "step_finish"}), returncode=0
    )
    assert result.final_text == ""


def test_codex_final_text_is_claim_and_nonzero_physical_exit_still_fails() -> None:
    result = parse_native_agent_events(
        "codex",
        events(
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "untrusted role payload"},
            },
            {"type": "turn.completed"},
        ),
        returncode=17,
    )
    assert result.settled and result.failed
    assert result.final_text == "untrusted role payload"


@pytest.mark.parametrize(
    "output",
    [
        "not json",
        '{"type":"a","type":"b"}',
        '{"type":"a","bad":NaN}',
        "[]",
        '{"type":"text","part":{"text":"\\ud800"}}',
    ],
)
def test_malformed_native_stdout_is_not_a_result(output: str) -> None:
    with pytest.raises(AgentProviderError):
        parse_native_agent_events("opencode", output, returncode=0)


def test_native_payloads_are_bounded_and_observation_omission_is_explicit() -> None:
    with pytest.raises(AgentProviderError, match="budget"):
        parse_native_agent_events("pi", " " * (1024 * 1024 + 1))
    with pytest.raises(AgentProviderError, match="role-record"):
        parse_native_agent_events(
            "pi",
            events(
                {"type": "message_end", "message": {"role": "assistant", "content": "x" * 65537}}
            ),
        )
    records: list[dict[str, object]] = [{"type": "tool_execution_start"} for _ in range(130)]
    result = parse_native_agent_events("pi", events(*records))
    assert len(result.observations) == 128
    assert result.omitted_observations == 2


def test_long_native_session_keeps_compaction_and_final_claim_without_a_session_cap() -> None:
    parser = NativeAgentEventParser("pi")
    parser.feed(events({"type": "agent_start"}, {"type": "auto_compaction_start"}))
    transferred = 0
    for index in range(6000):
        record = events(
            {"type": "tool_execution_end", "toolCallId": str(index), "result": "x" * 256}
        )
        transferred += len(record.encode("utf-8"))
        parser.feed(record)
    parser.feed(
        events(
            {"type": "auto_compaction_end"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": "last native claim after compaction",
                    "stopReason": "stop",
                },
            },
            {"type": "agent_settled", "aborted": False},
        )
    )
    result = parser.finish(returncode=0)
    assert transferred > 1024 * 1024
    assert result.settled and not result.failed
    assert result.final_text == "last native claim after compaction"
    assert result.event_count == 6005
    assert len(result.observations) == 128
    assert result.omitted_observations == 5876
    assert result.observations[1].kind == "context"


@pytest.mark.parametrize("agent", ["opencode", "pi", "codex"])
def test_incremental_native_records_preserve_unicode_and_eof_without_newline(
    agent: NativeAgentName,
) -> None:
    text = 'résumé\u2028line\u2029😀 "claim"'
    if agent == "pi":
        output = events(
            {"type": "agent_start"},
            {
                "type": "message_end",
                "message": {"role": "assistant", "content": text, "stopReason": "stop"},
            },
            {"type": "agent_settled"},
        )
    elif agent == "opencode":
        output = events(
            {"type": "step_start"},
            {"type": "text", "part": {"text": text, "time": {"end": 123}}},
        )
    else:
        output = events(
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {"type": "turn.completed"},
        )
    output = output.removesuffix("\n").replace("\n", "\r\n")
    parser = NativeAgentEventParser(agent)
    decoder = codecs.getincrementaldecoder("utf-8")()
    for byte in output.encode("utf-8"):
        parser.feed(decoder.decode(bytes((byte,))))
    parser.feed(decoder.decode(b"", final=True))
    assert parser.finish(returncode=0) == parse_native_agent_events(agent, output, returncode=0)


def test_json_unicode_escape_pair_may_cross_transport_chunks() -> None:
    parser = NativeAgentEventParser("pi")
    parser.feed('{"type":"message_end","message":{"role":"assistant","content":"\\ud83d')
    parser.feed('\\ude00"}}\n{"type":"agent_settled"}')
    assert parser.finish(returncode=0).final_text == "😀"


@pytest.mark.parametrize(
    "invalid",
    ["PRIVATE-PAYLOAD", '{"type":"x","type":"y"}', '{"type":"x","value":NaN}'],
)
def test_stream_parser_latches_malformed_record_and_cannot_accept_later_completion(
    invalid: str,
) -> None:
    parser = NativeAgentEventParser("pi")
    with pytest.raises(AgentProviderError) as captured:
        parser.feed(events({"type": "agent_start"}) + invalid + "\n")
    assert "PRIVATE-PAYLOAD" not in str(captured.value)
    assert len(str(captured.value)) < 200
    with pytest.raises(AgentProviderError, match="valid bounded JSONL"):
        parser.feed(events({"type": "agent_settled"}))
    with pytest.raises(AgentProviderError, match="valid bounded JSONL"):
        parser.finish(returncode=0)


def test_stream_parser_bounds_pending_line_by_utf8_bytes_across_chunks() -> None:
    parser = NativeAgentEventParser("pi")
    parser.feed("é" * (128 * 1024))
    with pytest.raises(AgentProviderError, match="byte bound"):
        parser.feed("é")
    with pytest.raises(AgentProviderError, match="byte bound"):
        parser.finish(returncode=0)


def test_stream_parser_rejects_oversized_complete_record_and_truncated_eof() -> None:
    parser = NativeAgentEventParser("pi")
    with pytest.raises(AgentProviderError, match="byte bound"):
        parser.feed(events({"type": "message_update", "data": "x" * (256 * 1024)}))
    parser = NativeAgentEventParser("pi")
    parser.feed('{"type":')
    with pytest.raises(AgentProviderError, match="valid bounded JSONL"):
        parser.finish(returncode=0)


def test_stream_final_role_limit_uses_utf8_bytes() -> None:
    parser = NativeAgentEventParser("pi")
    with pytest.raises(AgentProviderError, match="role-record"):
        parser.feed(
            events(
                {
                    "type": "message_end",
                    "message": {"role": "assistant", "content": "é" * 32769},
                }
            )
        )


def test_opencode_v2_reconciled_completed_text_settles_only_after_zero_process_exit() -> None:
    # v2.1.2 run/noninteractive.ts: session.wait() then reconcile() can emit
    # completed text and abort its event stream before step_finish is printed.
    output = events(
        {"type": "step_start", "part": {"type": "step-start"}},
        {
            "type": "text",
            "part": {
                "id": "prt_message_text-0",
                "messageID": "msg_message",
                "type": "text",
                "text": "native report, not verification evidence",
                "time": {"start": 120, "end": 123},
            },
        },
    )
    assert parse_native_agent_events("opencode", output, returncode=0).settled
    assert not parse_native_agent_events("opencode", output).settled
    failed = parse_native_agent_events("opencode", output, returncode=7)
    assert failed.failed and not failed.settled
    for suffix in (
        events({"type": "step_start"}),
        events({"type": "error"}),
        events({"type": "tool_use", "part": {"id": "tool", "state": {"status": "running"}}}),
    ):
        assert not parse_native_agent_events("opencode", output + suffix, returncode=0).settled


@pytest.mark.parametrize(
    "part",
    [
        {"text": "partial"},
        {"text": "partial", "time": {"start": 123}},
        {"text": "partial", "time": {"end": True}},
        {"text": "partial", "time": {"end": "123"}},
        {"text": " ", "time": {"end": 123}},
    ],
)
def test_opencode_unfinalized_or_blank_text_and_zero_exit_are_incomplete(
    part: dict[str, object],
) -> None:
    assert not parse_native_agent_events("opencode", "", returncode=0).settled
    assert not parse_native_agent_events(
        "opencode", events({"type": "step_start"}, {"type": "text", "part": part}), returncode=0
    ).settled
