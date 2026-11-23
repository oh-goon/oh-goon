"""RPC relay pipe behavior and selected installed Pi ephemeral transport."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import Event
from typing import Literal

import pytest

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.core.models.fleet_plan_step_primitives import PLAN_CONTRACT_RETURN_CODE
from oh_goon.engine.command import (
    TERMINATION_GRACE_SECONDS,
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandResult,
    run_command,
)
from oh_goon.integrations.agent_native_execution import (
    NativeAgentEventParser,
    native_executable_sha256,
)
from oh_goon.integrations.agent_pi_rpc import PiRpcControl
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
)
from oh_goon.runtime.termination import TerminationRequest

pytestmark = pytest.mark.no_resume


@pytest.mark.process
@pytest.mark.parametrize("invalid", [False, True])
def test_rpc_relay_filters_controls_and_preserves_native_prefix_before_refusal(
    tmp_path: Path, invalid: bool
) -> None:
    prompt = "literal task\nwith Unicode\u2028and private source"
    frozen = tmp_path / "prompt.txt"
    frozen.write_text(prompt, encoding="utf-8")
    marker = tmp_path / "native-eof"
    program = tmp_path / "native.py"
    program.write_text(
        r"""import json,os,sys
from pathlib import Path

invalid,marker,expected=sys.argv[1:]
def read():
    return json.loads(sys.stdin.buffer.readline())
def wire(value):
    return (json.dumps(value,ensure_ascii=False)+'\n').encode()
def reply(request,data):
    os.write(1,wire({'type':'response','id':request['id'],'command':request['type'],'success':True,'data':data}))
state,entries=read(),read()
assert state['type']=='get_state' and entries['type']=='get_entries'
reply(entries,{'entries':['PRIVATE_CONTROL_HISTORY'],'leafId':None})
reply(state,{'sessionId':'native-fixture','sessionFile':None})
prompt=read()
assert prompt['type']=='prompt' and prompt['message']==expected
event={'type':'message_end','message':{'role':'assistant','content':'native-prefix','stopReason':'stop'}}
if invalid=='True':
    bad={'type':'response','id':'unowned','command':'prompt','success':True,'data':{}}
    os.write(1,wire(event)+wire(bad))
else:
    reply(prompt,{'disposition':'started'})
    os.write(1,wire(event)+wire({'type':'agent_end'})+wire({'type':'agent_settled','aborted':False}))
    final=read()
    assert final['type']=='get_state'
    reply(final,{'sessionId':'native-fixture','sessionFile':None})
assert sys.stdin.buffer.read()==b''
Path(marker).write_text('native disposed after EOF')
""",
        encoding="utf-8",
    )
    result = run_command(
        sys.executable,
        "-I",
        "-m",
        "oh_goon.integrations.agent_inference_relay",
        "--socket",
        str(tmp_path / "unused-broker.sock"),
        "--port",
        "0",
        "--input-file",
        str(frozen),
        "--pi-rpc",
        "--",
        sys.executable,
        "-I",
        str(program),
        str(invalid),
        str(marker),
        prompt,
        timeout=10,
        input_data=b"",
        max_capture_bytes_per_stream=65536,
    )
    assert result.returncode == (PLAN_CONTRACT_RETURN_CODE if invalid else 0)
    assert result.physical_execution_id
    assert result.supervisor.final_process_group_quiescent
    assert marker.read_text() == "native disposed after EOF"
    records = [json.loads(line) for line in result.stdout.split("\n") if line]
    assert records[0]["type"] == "message_end"
    assert records[0]["message"]["content"] == "native-prefix"
    assert all(record["type"] != "response" for record in records)
    assert "PRIVATE_CONTROL_HISTORY" not in result.stdout + result.stderr
    assert prompt not in result.stdout + result.stderr
    if invalid:
        assert len(records) == 1 and "Pi RPC" in result.stderr
    else:
        assert records[-1] == {"type": "agent_settled", "aborted": False}


_RPC_PROVIDER = """
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";
export default function (pi) {
  pi.registerProvider("wp00", {
    api: "wp00", baseUrl: "http://synthetic.invalid", apiKey: "synthetic-only",
    models: [{id: "session-fixture", name: "WP-00 synthetic session fixture",
      reasoning: false, input: ["text"], contextWindow: 32768, maxTokens: 256,
      cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0}}],
    streamSimple(model, context, options) {
      const stream = createAssistantMessageEventStream();
      const users = context.messages.filter(m => m.role === "user").map(m =>
        typeof m.content === "string" ? m.content :
        m.content.filter(p => p.type === "text").map(p => p.text).join("\\n"));
      const task = users.at(-1) ?? "";
      let terminalError;
      if (task.includes("WP139_ERROR")) terminalError = "error";
      if (task.includes("WP139_ABORTED")) terminalError = "aborted";
      const text = terminalError ? "Completed successfully" :
        JSON.stringify({userCount: users.length,
        canarySeen: users.some(t => t.includes("WP00_CANARY"))});
      const output = {role: "assistant", content: [], api: model.api,
        provider: model.provider, model: model.id, stopReason: "pending",
        timestamp: Date.now(), usage: {input: 0, output: 0, cacheRead: 0,
          cacheWrite: 0, totalTokens: 0,
          cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}}};
      stream.push({type: "start", partial: output});
      if (users.at(-1)?.includes("WP139_CRASH")) {
        process.stderr.write("WP139_NATIVE_CHILD_CRASH\\n");
        setImmediate(() => process.kill(process.pid, "SIGKILL"));
        return stream;
      }
      if (users.at(-1)?.includes("WP00_HANG")) {
        const abort = () => {
          output.stopReason = "aborted";
          output.errorMessage = "Synthetic session-fixture abort";
          stream.push({type: "error", reason: "aborted", error: output});
          stream.end();
        };
        if (options?.signal?.aborted) abort();
        else options?.signal?.addEventListener("abort", abort, {once: true});
        return stream;
      }
      output.content.push({type: "text", text});
      stream.push({type: "text_start", contentIndex: 0, partial: output});
      stream.push({type: "text_delta", contentIndex: 0, delta: text, partial: output});
      stream.push({type: "text_end", contentIndex: 0, content: text, partial: output});
      if (terminalError) {
        output.stopReason = terminalError;
        output.errorMessage = "Synthetic terminal provider failure";
        stream.push({type: "error", reason: terminalError, error: output});
        stream.end();
        return stream;
      }
      output.stopReason = "stop";
      stream.push({type: "done", reason: "stop", message: output});
      stream.end();
      return stream;
    }
  });
}
"""


@dataclass
class _PiRpcProbe:
    workspace: Path
    configuration: Path
    program: Path
    public_runtime: Path
    backend: BubblewrapProcessIsolation
    control: PiRpcControl | None = None
    events: list[str] = field(default_factory=list)
    process_events: list[CommandProcessLifecycleEvent] = field(default_factory=list)

    def launch(self, prompt: bytes, *, cancel_on_stream: bool = False) -> CommandResult:
        self.control = control = PiRpcControl(prompt)
        assistant_started = Event()
        event_bytes = 0

        def observe(stream: Literal["stdout", "stderr"], text: str) -> None:
            nonlocal event_bytes
            if stream != "stdout":
                return
            for native in control.feed_stdout(text):
                event_bytes += len(native.encode("utf-8"))
                assert event_bytes <= 1024 * 1024, "Native events exceeded probe bound"
                self.events.append(native)
                record = json.loads(native)
                if record["type"] == "message_start" and record["message"]["role"] == "assistant":
                    assistant_started.set()

        request = StandardIsolatedProcessRequest(
            workspace=self.workspace,
            cwd=self.workspace,
            argv=(
                str(self.program),
                "--offline",
                "--mode",
                "rpc",
                "--no-session",
                "--no-extensions",
                "--extension",
                str(self.configuration / "provider.mjs"),
                "--no-skills",
                "--no-prompt-templates",
                "--no-themes",
                "--no-tools",
                "--provider",
                "wp00",
                "--model",
                "session-fixture",
                "--thinking",
                "off",
            ),
            env={
                "PATH": "/usr/bin:/bin",
                "PI_CODING_AGENT_DIR": str(self.configuration),
                "PI_SKIP_VERSION_CHECK": "1",
                "NO_COLOR": "1",
            },
            backend_configuration_id=self.backend.configuration_identity(),
            require_workspace_current=lambda: None,
            read_only_mounts=(
                StandardReadOnlyMount(self.public_runtime, PurePosixPath(self.public_runtime)),
            ),
            private_configuration_directory=self.configuration,
            input_source=control,
        )
        try:
            result = self.backend.execute(
                request,
                timeout=10,
                cancel_requested=assistant_started.is_set if cancel_on_stream else None,
                stream_output=observe,
                process_observer=self.process_events.append,
            )
        except CommandCancelledError as error:
            assert assistant_started.is_set()
            print(
                json.dumps(
                    {
                        "cancelled": True,
                        "physical_execution_id": error.physical_execution_id,
                        "supervisor": error.supervisor.receipt_identity,
                        "native_rpc_startup_identity": control.session_identity,
                    }
                )
            )
            raise
        if result.returncode == 0:
            control.finish()
        assert result.supervisor.final_process_group_quiescent
        assert not result.stdout_truncated
        isolation = result.standard_isolation
        assert isolation is not None and isolation.filesystem_cleanup == "verified"
        assert isolation.private_configuration_overlay
        assert isolation.host_and_external_ip_networking == "denied"
        assert not isolation.broker_socket_mounted
        print(
            json.dumps(
                {
                    "exit": result.returncode,
                    "physical_execution_id": result.physical_execution_id,
                    "supervisor": result.supervisor.receipt_identity,
                    "filesystem_cleanup": isolation.filesystem_cleanup,
                    "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                    "native_rpc_startup_identity": control.session_identity,
                }
            )
        )
        return result


@pytest.fixture
def pi_rpc_probe(tmp_path: Path) -> Generator[_PiRpcProbe, None, None]:
    if sys.platform != "linux":
        pytest.skip("Installed Linux Pi namespace proof")
    executable = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the public installed Pi entrypoint and complete runtime root")
    program = Path(executable).resolve(strict=True)
    public_runtime = Path(runtime_root).resolve(strict=True)
    assert program.is_file() and program.is_relative_to(public_runtime)
    version = run_command(
        str(program),
        "--version",
        timeout=10,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        max_capture_bytes_per_stream=4096,
    )
    assert version.returncode == 0 and version.stdout.strip() == "1.3.0"
    print(
        json.dumps(
            {
                "pi_version": version.stdout.strip(),
                "entry_sha256": native_executable_sha256(program),
                "host": os.uname().release,
            }
        )
    )
    bwrap = shutil.which("bwrap")
    assert bwrap, "Selected installed qualification requires native Bubblewrap"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    configuration = tmp_path / "configuration"
    configuration.mkdir(mode=0o700)
    try:
        (configuration / "provider.mjs").write_text(_RPC_PROVIDER, encoding="utf-8")
        (configuration / "settings.json").write_text(
            json.dumps({"defaultProvider": "wp00", "defaultModel": "session-fixture"}),
            encoding="utf-8",
        )
        yield _PiRpcProbe(
            workspace, configuration, program, public_runtime, BubblewrapProcessIsolation(bwrap)
        )
    finally:
        shutil.rmtree(configuration)
        assert not configuration.exists()


def _assert_rpc_process_lifetime(probe: _PiRpcProbe, physical_execution_id: str | None) -> None:
    assert physical_execution_id
    assert len(probe.process_events) <= 16
    assert {event.physical_execution_id for event in probe.process_events} == {
        physical_execution_id
    }
    kinds = [event.kind for event in probe.process_events]
    assert kinds[0] == "PROCESS_STARTING"
    assert kinds[-2:] == ["PROCESS_EXITED", "DESCENDANTS_QUIESCENT"]
    assert kinds.count("PROCESS_STARTED") == 1


@pytest.mark.certification
def test_installed_pi_ephemeral_rpc_duplex_settles_and_preserves_literal_prompt(
    pi_rpc_probe: _PiRpcProbe,
) -> None:
    prompt = 'WP00_CANARY literal\n"quoted" 🧪\u2028line\u2029paragraph'
    result = pi_rpc_probe.launch(prompt.encode("utf-8"))
    assert result.returncode == 0
    _assert_rpc_process_lifetime(pi_rpc_probe, result.physical_execution_id)
    records = [json.loads(event) for event in pi_rpc_probe.events]
    assert not any(event["type"] == "response" for event in records)
    messages = [event["message"] for event in records if event["type"] == "message_end"]
    assistants = [message for message in messages if message["role"] == "assistant"]
    assert assistants[-1]["stopReason"] == "stop"
    assert json.loads(assistants[-1]["content"][0]["text"]) == {"userCount": 1, "canarySeen": True}
    users = [message["content"] for message in messages if message["role"] == "user"]
    assert len(users) == 1 and users[0] in (prompt, [{"type": "text", "text": prompt}])
    assert any(event["type"] == "agent_settled" and event["aborted"] is False for event in records)
    assert pi_rpc_probe.control is not None and pi_rpc_probe.control.session_identity is not None
    assert pi_rpc_probe.control.read_input(1024) == b""
    assert list(pi_rpc_probe.workspace.glob("*.jsonl")) == []


@pytest.mark.certification
@pytest.mark.parametrize("reason", ["error", "aborted"])
def test_installed_pi_ephemeral_rpc_failed_assistant_refuses_zero_exit_success(
    pi_rpc_probe: _PiRpcProbe, reason: str
) -> None:
    result = pi_rpc_probe.launch(f"WP139_{reason.upper()}".encode())
    assert result.returncode == 0
    _assert_rpc_process_lifetime(pi_rpc_probe, result.physical_execution_id)
    parser = NativeAgentEventParser("pi")
    reasons: list[str] = []
    for event in pi_rpc_probe.events:
        record = json.loads(event)
        assert record["type"] != "response"
        if record["type"] == "message_end" and record["message"]["role"] == "assistant":
            reasons.append(record["message"]["stopReason"])
        parser.feed(event)
    assert reasons == [reason]
    events = parser.finish(returncode=result.returncode)
    assert events.final_text == "Completed successfully"
    assert events.failed and events.settled
    assert list(pi_rpc_probe.workspace.glob("*.jsonl")) == []


@pytest.mark.certification
def test_installed_pi_ephemeral_rpc_active_turn_cancellation_keeps_original_process_receipt(
    pi_rpc_probe: _PiRpcProbe,
) -> None:
    with pytest.raises(CommandCancelledError) as stopped:
        pi_rpc_probe.launch(b"WP00_CANARY WP00_HANG", cancel_on_stream=True)
    error = stopped.value
    _assert_rpc_process_lifetime(pi_rpc_probe, error.physical_execution_id)
    assert error.supervisor.cleanup_reason == "cancel"
    assert error.supervisor.final_process_group_quiescent
    termination = error.supervisor.termination_receipt
    assert termination is not None and termination.quiescent
    assert (
        termination.request_identity
        == TerminationRequest(
            execution_id=error.physical_execution_id or "",
            generation=1,
            cause="cancel",
            reason="cancellation_requested",
            grace_seconds=TERMINATION_GRACE_SECONDS,
        ).request_identity
    )
    assert any(event.kind == "TERMINATION_REQUESTED" for event in pi_rpc_probe.process_events)
    records = [json.loads(event) for event in pi_rpc_probe.events]
    assert any(
        event["type"] == "message_start" and event["message"]["role"] == "assistant"
        for event in records
    )
    assert not any(
        event["type"] == "message_end"
        and event["message"]["role"] == "assistant"
        and event["message"]["stopReason"] == "stop"
        for event in records
    )
    assert list(pi_rpc_probe.workspace.glob("*.jsonl")) == []


@pytest.mark.certification
def test_installed_pi_ephemeral_rpc_native_child_crash_refuses_unsettled_completion(
    pi_rpc_probe: _PiRpcProbe,
) -> None:
    result = pi_rpc_probe.launch(b"WP139_CRASH")
    assert result.returncode != 0
    assert "WP139_NATIVE_CHILD_CRASH" in result.stderr
    _assert_rpc_process_lifetime(pi_rpc_probe, result.physical_execution_id)
    assert pi_rpc_probe.control is not None
    with pytest.raises(AgentProviderError):
        pi_rpc_probe.control.finish()
    assert not any(json.loads(event)["type"] == "agent_settled" for event in pi_rpc_probe.events)
    assert list(pi_rpc_probe.workspace.glob("*.jsonl")) == []
