"""Installed Pi protocol observations separate from original product Runs.

The native extension supplies synthetic model replies without network or credentials.
Pi owns its session format, loading and persistence. Each process uses the original
Bubblewrap/command owners, separate disposable configuration and retained test data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sys
from collections import deque
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import Lock
from typing import Any, Literal

import pytest

from oh_goon.core.errors import PiSessionRefusalCode
from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedNativeAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.pi_session_storage import (
    PI_SESSION_NEW_FILE,
    PI_SESSION_SANDBOX_DIRECTORY,
    PiSessionDirectoryPin,
    PiSessionNativeObservation,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandInputSource,
    CommandOutputObserver,
    CommandResult,
    run_command,
)
from oh_goon.integrations.agent_native_execution import (
    native_executable_sha256,
    validate_native_agent_arguments,
)
from oh_goon.integrations.agent_pi_rpc import PiRpcContinuation, PiRpcControl
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.integrations.fleet_plan_process_isolation import (
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
)
from oh_goon.runtime.pi_session_storage import (
    observe_pi_session_file,
    pin_pi_session_directory,
)
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority

pytestmark = [
    pytest.mark.certification,
    pytest.mark.skipif(sys.platform != "linux", reason="Installed Linux Pi namespace proof"),
]

_PROVIDER = """
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
      const text = JSON.stringify({userCount: users.length,
        canarySeen: users.some(t => t.includes("WP00_CANARY"))});
      const output = {role: "assistant", content: [], api: model.api,
        provider: model.provider, model: model.id, stopReason: "pending",
        timestamp: Date.now(), usage: {input: 0, output: 0, cacheRead: 0,
          cacheWrite: 0, totalTokens: 0,
          cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0}}};
      stream.push({type: "start", partial: output});
      if (storageProbeCommand !== null && !context.messages.some(
          m => m.role === "toolResult" && m.toolCallId === "wp02_storage_probe")) {
        const toolCall = {type: "toolCall", id: "wp02_storage_probe", name: "bash",
          arguments: {command: storageProbeCommand, timeout: 4}};
        output.content.push(toolCall);
        stream.push({type: "toolcall_start", contentIndex: 0, partial: output});
        stream.push({type: "toolcall_end", contentIndex: 0, toolCall, partial: output});
        output.stopReason = "toolUse";
        stream.push({type: "done", reason: "toolUse", message: output});
        stream.end();
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
      output.stopReason = "stop";
      stream.push({type: "done", reason: "stop", message: output});
      stream.end();
      return stream;
    }
  });
}
"""


_TRUNCATE_INTERRUPTION = r"""
const fs = require("node:fs");
const { syncBuiltinESMExports } = require("node:module");
const selected = process.argv[process.argv.indexOf("--session") + 1];
const open = fs.openSync;
fs.openSync = (path, flags, ...args) => {
  const fd = open(path, flags, ...args);
  if (path === selected && flags === "w" && fs.fstatSync(fd).size === 0) {
    fs.writeSync(2, "synthetic Pi session rewrite truncated\n");
    process.kill(process.pid, "SIGKILL");
  }
  return fd;
};
syncBuiltinESMExports();
"""


class _PersistentStorageProbeInput:
    """Drive a selected installed storage probe through its original process input."""

    def __init__(self, prompt: str) -> None:
        self._lock = Lock()
        self._outgoing = deque([self._encode("start", "get_state")])
        self._prompt = self._encode("prompt", "prompt", message=prompt)
        self._partial = ""
        self._seen: set[str] = set()
        self._settled = False
        self._queried = False

    @staticmethod
    def _encode(identifier: str, kind: str, **fields: str) -> bytes:
        return json.dumps({"id": identifier, "type": kind, **fields}).encode("utf-8") + b"\n"

    def read_input(self, max_bytes: int) -> bytes | None:
        with self._lock:
            if self._outgoing:
                wire = self._outgoing.popleft()
                if len(wire) > max_bytes:
                    self._outgoing.appendleft(wire[max_bytes:])
                return wire[:max_bytes]
            return b"" if {"final", "entries"} <= self._seen else None

    def observe_stdout(self, text: str) -> None:
        with self._lock:
            records = (self._partial + text).split("\n")
            self._partial = records.pop()
            for line in records:
                if not line:
                    continue
                assert len(line) <= 65536
                record = json.loads(line)
                if record["type"] == "response":
                    assert record["success"] is True
                    identifier = record["id"]
                    assert identifier not in self._seen
                    self._seen.add(identifier)
                    if identifier == "start":
                        self._outgoing.append(self._prompt)
                elif record["type"] == "agent_settled":
                    assert record["aborted"] is False
                    assert not self._settled
                    self._settled = True
                if self._settled and "prompt" in self._seen and not self._queried:
                    self._queried = True
                    self._outgoing.extend(
                        (self._encode("final", "get_state"), self._encode("entries", "get_entries"))
                    )
            assert len(self._partial) <= 65536


@dataclass
class _PiProbe:
    root: Path
    program: Path
    public_runtime: Path
    backend: BubblewrapProcessIsolation
    executions: set[str] = field(default_factory=set)
    launches: int = 0

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    @property
    def session(self) -> Path:
        return self.workspace / "sessions" / "selected.jsonl"

    def launch(
        self,
        *,
        mode: Literal["json", "rpc"] = "json",
        prompt: str | None = None,
        session: Path | None = None,
        session_id: str | None = None,
        cwd: Path | None = None,
        cancel_on_stream: bool = False,
        interrupt_rewrite: bool = False,
        persistent_storage: PiSessionDirectoryPin | None = None,
        storage_probe_command: str | None = None,
    ) -> CommandResult:
        storage_turn = None
        if persistent_storage is not None:
            assert mode == "rpc" and prompt is not None and storage_probe_command is not None
            assert session is None and session_id is None and not cancel_on_stream
            storage_turn = _PersistentStorageProbeInput(prompt)
        else:
            assert storage_probe_command is None
        self.launches += 1
        config = self.root / f"config-{self.launches}"
        config.mkdir(mode=0o700)
        extension = config / "provider.mjs"
        extension.write_text(
            f"const storageProbeCommand = {json.dumps(storage_probe_command)};\n" + _PROVIDER,
            encoding="utf-8",
        )
        (config / "settings.json").write_text(
            json.dumps({"defaultProvider": "wp00", "defaultModel": "session-fixture"}),
            encoding="utf-8",
        )
        session_directory = (
            PI_SESSION_SANDBOX_DIRECTORY if persistent_storage is not None else self.session.parent
        )
        selected_session = (
            session_directory / "selected.jsonl"
            if persistent_storage is not None
            else session or self.session
        )
        workspace = (
            persistent_storage.workspace if persistent_storage is not None else self.workspace
        )
        launcher: tuple[str, ...] = (str(self.program),)
        if interrupt_rewrite:
            assert mode == "rpc" and prompt is None and session_id is None
            assert persistent_storage is None and not cancel_on_stream
            assert selected_session == self.session and self.session.is_file()
            node = shutil.which("node", path="/usr/bin:/bin")
            assert node is not None
            hook = config / "interrupt-rewrite.cjs"
            hook.write_text(_TRUNCATE_INTERRUPTION, encoding="utf-8")
            launcher = (node, "--require", str(hook), *launcher)
        argv = (
            *launcher,
            "--offline",
            "--mode",
            mode,
            "--no-extensions",
            "--extension",
            str(extension),
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            *(("--tools", "bash") if storage_turn is not None else ("--no-tools",)),
            "--provider",
            "wp00",
            "--model",
            "session-fixture",
            "--thinking",
            "off",
            "--session-dir",
            str(session_directory),
            *(
                ("--session-id", session_id)
                if session_id is not None
                else ("--session", str(selected_session))
            ),
            *((prompt,) if prompt is not None and storage_turn is None else ()),
        )
        streaming = False
        pending = ""

        def observe(_stream: Literal["stdout", "stderr"], text: str) -> None:
            nonlocal streaming, pending
            if _stream == "stdout" and storage_turn is not None:
                storage_turn.observe_stdout(text)
            if _stream != "stdout" or not cancel_on_stream:
                return
            pending += text
            assert len(pending) <= 65536, "Native protocol record exceeded probe bound"
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                event = json.loads(line)
                if (
                    event.get("type") == "message_start"
                    and event.get("message", {}).get("role") == "assistant"
                ):
                    streaming = True

        request = StandardIsolatedProcessRequest(
            workspace=workspace,
            cwd=cwd or (workspace if persistent_storage is not None else workspace / "project"),
            argv=argv,
            env={
                "PATH": "/usr/bin:/bin",
                "PI_CODING_AGENT_DIR": str(config),
                "PI_SKIP_VERSION_CHECK": "1",
                "NO_COLOR": "1",
            },
            backend_configuration_id=self.backend.configuration_identity(),
            require_workspace_current=lambda: None,
            read_only_mounts=(
                StandardReadOnlyMount(self.public_runtime, PurePosixPath(self.public_runtime)),
            ),
            private_configuration_directory=config,
            read_only_workspace=persistent_storage is not None,
            pi_session_directory=persistent_storage,
            input_source=storage_turn,
            input_data=(
                (
                    b'{"id":"state","type":"get_state"}\n{"id":"entries","type":"get_entries"}\n'
                    if storage_turn is None
                    else None
                )
                if mode == "rpc"
                else b""
            ),
        )
        try:
            result = self.backend.execute(
                request,
                timeout=20,
                cancel_requested=(lambda: streaming) if cancel_on_stream else None,
                stream_output=observe,
            )
            assert result.supervisor.final_process_group_quiescent
            assert not result.stdout_truncated
            assert result.physical_execution_id
            assert result.physical_execution_id not in self.executions
            self.executions.add(result.physical_execution_id)
            isolation = result.standard_isolation
            assert isolation is not None and isolation.filesystem_cleanup == "verified"
            assert isolation.private_configuration_overlay
            assert isolation.host_and_external_ip_networking == "denied"
            assert not isolation.broker_socket_mounted
            print(
                json.dumps(
                    {
                        "launch": self.launches,
                        "mode": mode,
                        "exit": result.returncode,
                        "physical_execution_id": result.physical_execution_id,
                        "supervisor": result.supervisor.receipt_identity,
                        "filesystem_cleanup": isolation.filesystem_cleanup,
                        "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
                    }
                )
            )
            return result
        except CommandCancelledError as error:
            assert streaming, "Cancellation did not reach the native assistant turn"
            assert error.physical_execution_id
            assert error.physical_execution_id not in self.executions
            self.executions.add(error.physical_execution_id)
            print(
                json.dumps(
                    {
                        "launch": self.launches,
                        "mode": mode,
                        "cancelled": True,
                        "physical_execution_id": error.physical_execution_id,
                        "supervisor": error.supervisor.receipt_identity,
                        "assistant_started": streaming,
                    }
                )
            )
            raise
        finally:
            shutil.rmtree(config)
            assert not config.exists()


@pytest.fixture
def pi_probe(tmp_path: Path) -> _PiProbe:
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
    assert version.returncode == 0 and version.stdout.strip() == "1.3.0", (
        "Requalify the installed protocol observations for a different Pi version"
    )
    print(
        json.dumps(
            {
                "pi_version": version.stdout.strip(),
                "entry_sha256": native_executable_sha256(program),
                "host": os.uname().release,
            }
        )
    )
    for name in ("project", "other-project", "sessions"):
        (tmp_path / "workspace" / name).mkdir(parents=True)
    bwrap = shutil.which("bwrap")
    assert bwrap, "Selected installed qualification requires native Bubblewrap"
    return _PiProbe(tmp_path, program, public_runtime, BubblewrapProcessIsolation(bwrap))


def _records(result: CommandResult) -> list[dict[str, Any]]:
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.split("\n") if line.strip()]


def _state(probe: _PiProbe, **options: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    responses = {item["id"]: item for item in _records(probe.launch(mode="rpc", **options))}
    assert responses.keys() == {"state", "entries"}
    assert all(item["success"] for item in responses.values())
    return responses["state"]["data"], responses["entries"]["data"]


def _assistant(records: list[dict[str, Any]]) -> dict[str, Any]:
    messages = [
        item["message"]
        for item in records
        if item["type"] == "message_end" and item["message"]["role"] == "assistant"
    ]
    assert messages[-1]["stopReason"] == "stop"
    content = json.loads(messages[-1]["content"][0]["text"])
    assert isinstance(content, dict)
    return content


def test_installed_pi_exact_reopen_retains_identity_leaf_and_conversation(
    pi_probe: _PiProbe,
) -> None:
    first = _records(pi_probe.launch(prompt="Remember WP00_CANARY\u2028with Unicode separator"))
    assert first[0]["type"] == "session" and "leafId" not in first[0]
    assert _assistant(first) == {"userCount": 1, "canarySeen": True}
    retained = pi_probe.session.read_bytes()
    state, entries = _state(pi_probe)
    assert state["sessionId"] == first[0]["id"]
    assert state["sessionFile"] == "/workspace/sessions/selected.jsonl"
    assert entries["leafId"] == entries["entries"][-1]["id"]
    assert pi_probe.session.read_bytes() == retained
    second = _records(pi_probe.launch(prompt="Report remembered context"))
    assert second[0]["id"] == first[0]["id"]
    assert _assistant(second) == {"userCount": 2, "canarySeen": True}
    after, branch = _state(pi_probe)
    assert after["sessionId"] == state["sessionId"]
    assert branch["leafId"] != entries["leafId"]
    assert pi_probe.session.read_bytes().startswith(retained)


def test_installed_pi_provider_repair_only_adds_an_error_placeholder(pi_probe: _PiProbe) -> None:
    transform = (
        pi_probe.public_runtime
        / "node_modules/@earendil-works/pi-ai/dist/api/transform-messages.js"
    )
    result = run_command(
        "/usr/bin/node",
        "--input-type=module",
        "-e",
        """
import assert from 'node:assert/strict';
const { transformMessages } = await import(process.argv[1]);
const model = { id: 'fixture', provider: 'fixture', api: 'fixture', input: ['text'] };
const history = [{ role: 'assistant', provider: model.provider, api: model.api,
  model: model.id, stopReason: 'toolUse', content: [
    { type: 'toolCall', id: 'original-call', name: 'fixture-effect', arguments: {} },
  ] }];
const before = JSON.stringify(history);
const context = transformMessages(history, model);
assert.equal(JSON.stringify(history), before);
assert.equal(context.length, 2);
assert.deepEqual(context[0], history[0]);
const { timestamp, ...placeholder } = context[1];
assert.deepEqual(placeholder, { role: 'toolResult', toolCallId: 'original-call',
  toolName: 'fixture-effect', isError: true,
  content: [{ type: 'text', text: 'No result provided' }] });
""",
        transform.as_uri(),
        cwd=pi_probe.root,
        env={"PATH": "/usr/bin:/bin", "HOME": str(pi_probe.root)},
        timeout=10,
        max_capture_bytes_per_stream=4096,
        require_process_group_quiescence=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.supervisor.final_process_group_quiescent


_ISOLATED_SESSION_MODEL = "configured-session-fixture"
_ISOLATED_SESSION_ENDPOINT = "http://127.0.0.1:1/v1"


def _isolated_saved_session_command(
    pi_probe: _PiProbe,
    *,
    selected_file: Path | None,
    guest_path: PurePosixPath,
    session_directory: Path,
    guest_directory: PurePosixPath,
    command_input: CommandInputSource,
    observe: CommandOutputObserver,
) -> CommandResult:
    """Model-free installed component proof with a read-only selected descriptor."""
    config = pi_probe.root / "isolated-session-config"
    config.mkdir(mode=0o700)
    (config / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "oh-goon": {
                        "baseUrl": _ISOLATED_SESSION_ENDPOINT,
                        "api": "openai-completions",
                        "apiKey": "synthetic-only",
                        "models": [{"id": _ISOLATED_SESSION_MODEL}],
                    }
                }
            }
        )
    )
    bwrap = shutil.which("bwrap")
    assert bwrap is not None
    workspace = str(pi_probe.workspace / "project")
    argv = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for path in ("/usr", "/bin", "/lib", "/lib64", str(pi_probe.public_runtime), workspace):
        if Path(path).exists():
            argv.extend(("--ro-bind", path, path))
    with ExitStack() as owned:
        fds: list[int] = []
        mounts = ["--dir", str(guest_directory)]
        if selected_file is None or guest_path.parent != guest_directory:
            directory_fd = os.open(session_directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            owned.callback(os.close, directory_fd)
            fds.append(directory_fd)
            mounts = ["--bind-fd", str(directory_fd), str(guest_directory)]
        if selected_file is not None:
            file_fd = os.open(selected_file, os.O_RDONLY | os.O_NOFOLLOW)
            owned.callback(os.close, file_fd)
            fds.append(file_fd)
            if guest_path.parent != guest_directory:
                mounts.extend(("--dir", str(guest_path.parent)))
            mounts.extend(("--ro-bind-fd", str(file_fd), str(guest_path)))
        return run_command(
            *argv,
            "--ro-bind",
            workspace,
            "/workspace",
            "--ro-bind",
            str(config),
            "/config/native",
            *mounts,
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp/home",
            "--setenv",
            "PI_CODING_AGENT_DIR",
            "/config/native",
            "--chdir",
            workspace,
            "--",
            str(pi_probe.program),
            "--mode",
            "rpc",
            "--session-dir",
            str(guest_directory),
            "--session",
            str(guest_path),
            "--no-approve",
            "--no-extensions",
            "--no-mcp",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--provider",
            "oh-goon",
            "--model",
            _ISOLATED_SESSION_MODEL,
            "--tools",
            "read,grep,find,ls",
            timeout=15,
            pass_fds=tuple(fds),
            input_source=command_input,
            stream_output=observe,
            max_capture_bytes_per_stream=65536,
            require_process_group_quiescence=True,
        )


def test_installed_pi_clone_at_validated_tip_cannot_replace_explicit_leaf_fork(
    pi_probe: _PiProbe,
) -> None:
    """Component evidence for an unsupported product fork mode."""
    _records(pi_probe.launch(prompt="Remember WP00_CANARY in the original session"))
    prior, tree = _state(pi_probe)
    leaf = tree["leafId"]
    accepted = next(entry for entry in tree["entries"] if entry["id"] == leaf)
    assert accepted["type"] == "message" and accepted["message"]["role"] == "assistant"
    parent_before = pi_probe.session.stat()
    contents_before = pi_probe.session.read_bytes()
    assert len(contents_before) < 65536
    child_directory = pi_probe.root / "selected-clone"
    child_directory.mkdir(mode=0o700)
    parent_path = PurePosixPath("/run/selected-parent/session.jsonl")
    child_path = PurePosixPath("/run/selected-clone")
    command_names = ("get_state", "get_entries", "fork", "clone", "get_state", "get_entries")
    control_fields: tuple[dict[str, str], ...] = (
        {},
        {"since": leaf},
        {"entryId": leaf},
        {},
        {},
        {"since": leaf},
    )
    guard = Lock()
    outgoing: deque[bytes] = deque()
    seen: list[dict[str, Any]] = []
    pending = ""

    def queue_request(index: int) -> None:
        outgoing.append(
            json.dumps(
                {"id": f"controlled:{index}", "type": command_names[index], **control_fields[index]}
            ).encode()
            + b"\n"
        )

    class CloneInput:
        def read_input(self, max_bytes: int) -> bytes | None:
            with guard:
                if outgoing:
                    wire = outgoing.popleft()
                    if len(wire) > max_bytes:
                        outgoing.appendleft(wire[max_bytes:])
                    return wire[:max_bytes]
                return b"" if len(seen) == len(command_names) else None

    def observe(stream: str, text: str) -> None:
        nonlocal pending
        if stream != "stdout":
            return
        with guard:
            records = (pending + text).split("\n")
            pending = records.pop()
            for line in records:
                if not line:
                    continue
                assert len(line) <= 65536
                result = json.loads(line)
                index = len(seen)
                assert result["id"] == f"controlled:{index}"
                assert result["command"] == command_names[index]
                if index == 2:
                    assert result["success"] is False
                    assert "Invalid entry ID" in result["error"]
                    assert not tuple(child_directory.iterdir())
                else:
                    assert result["success"] is True
                    data = result["data"]
                    if index == 0:
                        assert data["sessionFile"] == str(parent_path)
                        assert data["sessionId"] == prior["sessionId"]
                        assert data["model"]["id"] == _ISOLATED_SESSION_MODEL
                        assert not data["isStreaming"] and not data["isCompacting"]
                    elif index in {1, 5}:
                        assert data == {"entries": [], "leafId": leaf}
                    elif index == 3:
                        assert data == {"cancelled": False}
                    elif index == 4:
                        assert data["sessionId"] != prior["sessionId"]
                        assert data["messageCount"] == prior["messageCount"]
                        assert data["model"]["id"] == _ISOLATED_SESSION_MODEL
                        assert PurePosixPath(data["sessionFile"]).parent == child_path
                seen.append(result)
                if len(seen) < len(command_names):
                    queue_request(len(seen))
            assert len(pending) <= 65536

    queue_request(0)
    result = _isolated_saved_session_command(
        pi_probe,
        selected_file=pi_probe.session,
        guest_path=parent_path,
        session_directory=child_directory,
        guest_directory=child_path,
        command_input=CloneInput(),
        observe=observe,
    )

    assert result.returncode == 0, result.stderr
    assert result.supervisor.final_process_group_quiescent
    assert len(seen) == len(command_names)
    new_session = seen[4]["data"]
    (generated,) = tuple(child_directory.iterdir())
    assert generated.name == PurePosixPath(new_session["sessionFile"]).name
    assert generated.name != "session.jsonl"
    assert generated.name.endswith("_" + new_session["sessionId"] + ".jsonl")
    with generated.open("rb") as stream:
        # These are synthetic bytes created by the installed fixture above.
        header = json.loads(stream.readline(4096))
    assert header["id"] == new_session["sessionId"] and header["parentSession"] == str(parent_path)
    assert pi_probe.session.read_bytes() == contents_before
    parent_after = pi_probe.session.stat()
    assert (parent_after.st_ino, parent_after.st_ctime_ns, parent_after.st_mtime_ns) == (
        parent_before.st_ino,
        parent_before.st_ctime_ns,
        parent_before.st_mtime_ns,
    )


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        ("foreign-session", PiSessionRefusalCode.IDENTITY_MISMATCH),
        ("older-leaf", PiSessionRefusalCode.IDENTITY_MISMATCH),
        ("unknown-leaf", PiSessionRefusalCode.HISTORY_UNQUALIFIED),
        ("missing-file", PiSessionRefusalCode.IDENTITY_MISMATCH),
        ("renamed-file", PiSessionRefusalCode.IDENTITY_MISMATCH),
        ("model", PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH),
        ("api", PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH),
        ("endpoint", PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH),
    ],
)
def test_installed_pi_pre_prompt_gate_refuses_unproved_reopen_authority(
    pi_probe: _PiProbe,
    resource: str,
    expected: PiSessionRefusalCode,
) -> None:
    _records(pi_probe.launch(prompt="Remember WP00_CANARY in the original session"))
    prior, prior_tree = _state(pi_probe)
    retained = pi_probe.session.read_bytes()
    selected_file: Path | None = pi_probe.session
    leaf = prior_tree["leafId"]
    actual_leaf = leaf
    actual_session = prior["sessionId"]
    renamed = pi_probe.session.with_name("moved.jsonl")
    if resource == "foreign-session":
        selected_file = pi_probe.session.with_name("foreign.jsonl")
        records = _records(
            pi_probe.launch(
                session=selected_file, prompt="This other synthetic session has its own identity"
            )
        )
        actual_session = records[0]["id"]
        assert actual_session != prior["sessionId"]
    elif resource == "older-leaf":
        _records(pi_probe.launch(prompt="Advance the original session to a later native leaf"))
        after, tree = _state(pi_probe)
        assert after["sessionId"] == prior["sessionId"]
        assert tree["leafId"] != leaf
        actual_leaf = tree["leafId"]
        retained = pi_probe.session.read_bytes()
    elif resource == "unknown-leaf":
        leaf = "nonexistent-native-leaf"
    elif resource == "missing-file":
        pi_probe.session.unlink()
        selected_file = None
    elif resource == "renamed-file":
        pi_probe.session.rename(renamed)
        selected_file = None

    control = PiRpcControl(
        b"Fresh task must stay in Oh-Goon until the native reopen authority is established",
        continuation=PiRpcContinuation(
            prior=PiSessionNativeObservation(session_id=prior["sessionId"], leaf_id=leaf),
            model="other-admitted-model" if resource == "model" else _ISOLATED_SESSION_MODEL,
            api="openai-responses" if resource == "api" else "openai-completions",
            base_url=(
                "http://127.0.0.1:2/v1" if resource == "endpoint" else _ISOLATED_SESSION_ENDPOINT
            ),
        ),
    )
    wire = bytearray()
    event_types: list[str] = []

    class ObservedInput:
        def read_input(self, max_bytes: int) -> bytes | None:
            data = control.read_input(max_bytes)
            if data:
                wire.extend(data)
            return data

    def observe(stream: str, text: str) -> None:
        if stream == "stdout":
            try:
                forwarded = control.feed_stdout(text)
            except AgentProviderError:
                # The production relay similarly lets the latched refusal close
                # input and waits for the original native process to dispose.
                return
            event_types.extend(json.loads(line)["type"] for line in forwarded)

    native_directory = pi_probe.root / "isolated-write-scope"
    native_directory.mkdir(mode=0o700)
    result = _isolated_saved_session_command(
        pi_probe,
        selected_file=selected_file,
        guest_path=PI_SESSION_NEW_FILE,
        session_directory=native_directory,
        guest_directory=PI_SESSION_SANDBOX_DIRECTORY,
        command_input=ObservedInput(),
        observe=observe,
    )
    assert result.supervisor.final_process_group_quiescent
    assert not result.stdout_truncated and not event_types
    records = _records(result)
    assert all(message["type"] == "response" for message in records)
    responses = {message["id"]: message for message in records}
    state_response = responses["oh-goon:state"]
    assert state_response["command"] == "get_state" and state_response["success"] is True
    state = state_response["data"]
    if selected_file is not None:
        assert state["sessionId"] == actual_session
    else:
        assert state["sessionId"] != prior["sessionId"] and state["messageCount"] == 0
    if resource in {"older-leaf", "unknown-leaf"}:
        entries = responses["oh-goon:entries"]
        if resource == "older-leaf":
            assert entries["success"] is True and entries["data"]["leafId"] == actual_leaf
            assert entries["data"]["entries"]
        else:
            assert entries["success"] is False and entries["command"] == "get_entries"
    outbound = [json.loads(line)["type"] for line in wire.splitlines()]
    assert outbound in (["get_state"], ["get_state", "get_entries"])
    if expected is PiSessionRefusalCode.NATIVE_CONFIGURATION_MISMATCH:
        # Pi really loaded its configured model, while this Run expects different authority.
        # The control may send both read-only queries before the state reply arrives.
        assert state["sessionFile"] == str(PI_SESSION_NEW_FILE)
        assert state["messageCount"] == prior["messageCount"] > 0
        assert state["model"]["provider"] == "oh-goon"
        assert state["model"]["id"] == _ISOLATED_SESSION_MODEL
        assert state["model"]["api"] == "openai-completions"
        assert state["model"]["baseUrl"] == _ISOLATED_SESSION_ENDPOINT
        assert responses.keys() <= {"oh-goon:state", "oh-goon:entries"}
        if "oh-goon:entries" in responses:
            entries = responses["oh-goon:entries"]
            assert entries["success"] is True and entries["data"]["leafId"] == leaf
            assert entries["data"]["entries"] == []
        assert control.session_identity is None
    with pytest.raises(AgentProviderError):
        control.finish()
    assert control.refusal_code is expected
    assert not tuple(native_directory.iterdir())
    if resource == "missing-file":
        assert not pi_probe.session.exists()
    elif resource == "renamed-file":
        assert not pi_probe.session.exists() and renamed.read_bytes() == retained
    else:
        assert pi_probe.session.read_bytes() == retained


def test_installed_pi_new_identity_and_close_do_not_prove_file_birth(pi_probe: _PiProbe) -> None:
    state, entries = _state(pi_probe)
    assert state["sessionId"]
    assert not any(entry["type"] == "message" for entry in entries["entries"])
    assert not pi_probe.session.exists()


def test_installed_pi_session_id_selector_is_not_exact_open(pi_probe: _PiProbe) -> None:
    header = _records(pi_probe.launch(prompt="Remember WP00_CANARY"))[0]
    state, _ = _state(pi_probe, session_id=header["id"])
    assert state["sessionId"] == header["id"]
    missing_id = "00000000-0000-4000-8000-000000000039"
    missing, entries = _state(pi_probe, session_id=missing_id)
    assert missing["sessionId"] == missing_id
    assert not any(entry["type"] == "message" for entry in entries["entries"])
    assert len(tuple(pi_probe.session.parent.iterdir())) == 1


@pytest.mark.parametrize("resource", ("missing", "renamed", "empty", "malformed", "torn-tail"))
def test_installed_pi_exact_open_negative_boundaries(pi_probe: _PiProbe, resource: str) -> None:
    original = _records(pi_probe.launch(prompt="Remember WP00_CANARY"))[0]["id"]
    good = pi_probe.session.read_bytes()
    renamed = pi_probe.session.with_name("moved.jsonl")
    if resource == "missing":
        pi_probe.session.unlink()
    elif resource == "renamed":
        pi_probe.session.rename(renamed)
    elif resource == "empty":
        pi_probe.session.write_bytes(b"")
    elif resource == "malformed":
        pi_probe.session.write_bytes(b"not a native session\n")
    else:
        pi_probe.session.write_bytes(good + b'{"type":"message"')
    if resource == "malformed":
        result = pi_probe.launch(mode="rpc")
        assert result.returncode != 0
        assert pi_probe.session.read_bytes() == b"not a native session\n"
        return
    state, entries = _state(pi_probe)
    if resource == "torn-tail":
        assert state["sessionId"] == original and entries["leafId"]
        assert pi_probe.session.read_bytes() == good + b'{"type":"message"\n'
    else:
        assert state["sessionId"] != original
        assert not any(entry["type"] == "message" for entry in entries["entries"])
        assert pi_probe.session.exists() == (resource == "empty")
        if resource == "empty":
            header = json.loads(pi_probe.session.read_bytes().splitlines()[0])
            assert header["type"] == "session" and header["id"] == state["sessionId"]
        if resource == "renamed":
            assert renamed.read_bytes() == good


def _write_legacy_session(pi_probe: _PiProbe, version: Literal[1, 2]) -> bytes:
    timestamp = "2025-01-01T00:00:00.000Z"
    header = {
        "type": "session",
        "version": version,
        "id": "synthetic-legacy-session",
        "timestamp": timestamp,
        "cwd": str(pi_probe.workspace / "project"),
    }
    entries: list[dict[str, object]] = [
        {
            "type": "message",
            "timestamp": timestamp,
            "message": {
                "role": "user",
                "content": "synthetic legacy prompt",
                "timestamp": 1735689600000,
            },
        },
        {
            "type": "message",
            "timestamp": timestamp,
            "message": {
                "role": "hookMessage",
                "customType": "fixture",
                "content": "synthetic legacy hook",
                "display": False,
                "timestamp": 1735689600000,
            },
        },
    ]
    if version == 2:
        entries[0].update(id="12345678", parentId=None)
        entries[1].update(id="abcdef12", parentId="12345678")
    contents = "".join(json.dumps(entry) + "\n" for entry in [header, *entries]).encode()
    pi_probe.session.write_bytes(contents)
    return contents


@pytest.mark.parametrize("version", (1, 2))
def test_installed_pi_open_migrates_legacy_session_in_place(
    pi_probe: _PiProbe, version: Literal[1, 2]
) -> None:
    original = _write_legacy_session(pi_probe, version)
    original_inode = pi_probe.session.stat().st_ino
    state, tree = _state(pi_probe)

    migrated = pi_probe.session.read_bytes()
    assert migrated != original
    assert pi_probe.session.stat().st_ino == original_inode
    header, initial, history_tip, selection = map(json.loads, migrated.splitlines())
    assert header == {**json.loads(original.splitlines()[0]), "version": 3}
    assert state["sessionId"] == header["id"]
    assert tree["entries"] == [initial, history_tip, selection]
    assert initial["parentId"] is None and history_tip["parentId"] == initial["id"]
    assert initial["id"] and history_tip["id"] and initial["id"] != history_tip["id"]
    assert initial["message"]["content"] == "synthetic legacy prompt"
    assert history_tip["message"]["role"] == "custom"
    assert history_tip["message"]["content"] == "synthetic legacy hook"
    if version == 2:
        assert (initial["id"], history_tip["id"]) == ("12345678", "abcdef12")
    # Selecting the fixture's thinking mode also records a native entry at open.
    assert selection["type"] == "thinking_level_change" and selection["thinkingLevel"] == "off"
    assert selection["parentId"] == history_tip["id"] and tree["leafId"] == selection["id"]


def test_installed_pi_open_interrupted_after_legacy_truncate_leaves_empty_file(
    pi_probe: _PiProbe,
) -> None:
    _write_legacy_session(pi_probe, 2)
    original_inode = pi_probe.session.stat().st_ino

    result = pi_probe.launch(mode="rpc", interrupt_rewrite=True)

    assert result.returncode in (-signal.SIGKILL, 128 + signal.SIGKILL)
    assert "synthetic Pi session rewrite truncated" in result.stderr
    assert not result.stdout.strip()
    assert pi_probe.session.stat().st_ino == original_inode
    assert pi_probe.session.read_bytes() == b""


def test_installed_pi_foreign_project_selection_is_not_an_authorization(pi_probe: _PiProbe) -> None:
    original = _records(pi_probe.launch(prompt="Remember WP00_CANARY"))[0]
    retained = pi_probe.session.read_bytes()
    # Native Pi accepts this saved session from another requested cwd. This is
    # not proof that a new Oh-Goon Run may use another project's state.
    state, _ = _state(pi_probe, cwd=pi_probe.workspace / "other-project")
    assert state["sessionId"] == original["id"]
    assert pi_probe.session.read_bytes() == retained


def test_installed_pi_interrupted_turn_retains_prompt_without_success(pi_probe: _PiProbe) -> None:
    with pytest.raises(CommandCancelledError) as stopped:
        pi_probe.launch(prompt="WP00_CANARY WP00_HANG", cancel_on_stream=True)
    assert stopped.value.supervisor.final_process_group_quiescent
    assert stopped.value.supervisor.cleanup_reason == "cancel"
    state, entries = _state(pi_probe)
    assert state["sessionId"]
    users = [
        entry
        for entry in entries["entries"]
        if entry["type"] == "message" and entry["message"]["role"] == "user"
    ]
    assert users and "WP00_HANG" in json.dumps(users)
    assert not any(
        entry["type"] == "message"
        and entry["message"]["role"] == "assistant"
        and entry["message"].get("stopReason") == "stop"
        for entry in entries["entries"]
    )


def test_installed_pi_private_session_survives_original_command_and_config_cleanup(
    pi_probe: _PiProbe,
) -> None:
    workspace = pi_probe.workspace / "project"
    git = run_command("git", "init", "-q", str(workspace), timeout=10)
    assert git.returncode == 0, git.stderr
    source = workspace / "source.py"
    source.write_text("EXPECTED = 42\n", encoding="utf-8")

    state = pi_probe.root / "application-state"
    state.mkdir(mode=0o700)
    journal = state / "fixture-journal.sqlite"
    journal.write_bytes(b"synthetic application control canary")
    personal = pi_probe.root / "operator" / ".pi" / "agent" / "settings.json"
    personal.parent.mkdir(parents=True)
    personal.write_bytes(b"synthetic personal store canary")
    layout = ApplicationStatePathLayout.from_database(journal)
    pi_probe.backend.bind_application_state(
        private_paths=(journal, personal.parent, *layout.private_control_paths),
        read_only_paths=(layout.extensions, layout.plan_tools),
        pi_session_root=layout.pi_sessions,
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="installed-storage-probe",
        definition=NativeAgentDefinition(executable=str(pi_probe.program)),
        profile=NativeAgentProfile(backend="bubblewrap", allowed_agents=("pi",)),
        executable_sha256=native_executable_sha256(pi_probe.program),
        backend_configuration_id=pi_probe.backend.configuration_identity(),
    )
    step = ResolvedAgentStep(
        step_id="inspect-private-storage",
        source_step_id="inspect-private-storage",
        timeout_seconds=20,
        delivery_sha256=hashlib.sha256(b"installed storage capability probe").hexdigest(),
        handoff_identity="installed-storage-probe",
        workload=ResolvedNativeAgentWorkload(native_agent=selected, role="review"),
    )
    plan = ResolvedFleetPlan.create(
        name="installed-pi-private-storage",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(step,),
        native_agent=selected,
    )
    repository = RepositoryIdentity.model_validate({"path": workspace})
    admission = repository_plan_admission_evidence(repository, plan)
    with (
        RepositoryWorkspaceAuthority.pin_repository_subject(workspace) as require_workspace,
        pin_pi_session_directory(
            layout=layout,
            fleet_run_id="installed-pi-storage-probe",
            repository=repository,
            repository_admission=admission,
            step_id=step.step_id,
            attempt_number=1,
            workspace=workspace,
            require_current=require_workspace,
        ) as pin,
    ):
        sibling = layout.pi_sessions / "foreign-probe" / "foreign.jsonl"
        sibling.parent.mkdir(mode=0o700)
        sibling.write_bytes(b"synthetic unrelated session canary")
        native_file = PI_SESSION_SANDBOX_DIRECTORY / "selected.jsonl"
        # Pi's native shell can read its own selected session in this namespace.
        # Check only bounded visibility and an actual denied write to test-owned source.
        tool_probe = "\n".join(
            [
                "set -eu",
                f"test -s {shlex.quote(str(native_file))}",
                f"test -r {shlex.quote(str(native_file))}",
                "test -r /workspace/source.py",
                "test -r /workspace/.git/HEAD",
                f"test /workspace/source.py -ef {shlex.quote(str(source))}",
                f"test /workspace/.git -ef {shlex.quote(str(workspace / '.git'))}",
                *(
                    f"test ! -e {shlex.quote(str(path))}"
                    for path in (sibling, journal, personal, pin.path / "selected.jsonl")
                ),
                "test ! -e /run/oh-goon-pi-session/foreign.jsonl",
                "if (printf forbidden > /workspace/should-not-exist) 2>/dev/null; then exit 1; fi",
                "printf 'WP02_SELECTED_FILE_VISIBLE\n'",
            ]
        )
        result = pi_probe.launch(
            mode="rpc",
            prompt="Run the fixed private storage visibility probe.",
            persistent_storage=pin,
            storage_probe_command=tool_probe,
        )
        records = _records(result)
        replies = {row["id"]: row for row in records if row["type"] == "response"}
        assert set(replies) == {"start", "prompt", "final", "entries"}
        assert replies["prompt"]["data"]["disposition"] == "started"
        before, after = (
            replies["start"]["data"],
            replies["final"]["data"],
        )
        assert before["sessionFile"] == after["sessionFile"] == str(native_file)
        assert before["sessionId"] == after["sessionId"] and before["sessionId"]
        entries = replies["entries"]["data"]
        assert entries["leafId"] == entries["entries"][-1]["id"]
        tool_results = [
            row["message"]
            for row in entries["entries"]
            if row["type"] == "message" and row["message"]["role"] == "toolResult"
        ]
        assert len(tool_results) == 1
        assert tool_results[0]["toolCallId"] == "wp02_storage_probe"
        assert tool_results[0]["isError"] is False
        assert tool_results[0]["content"] == [
            {"type": "text", "text": "WP02_SELECTED_FILE_VISIBLE\n"}
        ]

        receipt = observe_pi_session_file(pin, native_file.name)
        assert receipt.size > 0
        retained_file = pin.path / native_file.name
        assert tuple(pin.path.iterdir()) == (retained_file,)
        print(
            json.dumps(
                {
                    "scope": "installed native storage capability",
                    "session_id": after["sessionId"],
                    "leaf_id": entries["leafId"],
                    "file_bytes": receipt.size,
                    "own_session_visible_to_shell": True,
                    "foreign_and_control_paths_hidden": True,
                }
            )
        )
    assert not (pi_probe.root / "config-1").exists()
    metadata = retained_file.stat()
    assert (metadata.st_dev, metadata.st_ino, metadata.st_size) == (
        receipt.device,
        receipt.inode,
        receipt.size,
    )
    assert source.read_text(encoding="utf-8") == "EXPECTED = 42\n"
    assert not (workspace / "should-not-exist").exists()
    assert sibling.read_bytes() == b"synthetic unrelated session canary"
    assert journal.read_bytes() == b"synthetic application control canary"
    assert personal.read_bytes() == b"synthetic personal store canary"


def _native_arguments(
    probe: _PiProbe,
    fragments: Mapping[str, tuple[str, ...]],
    *,
    mode: Literal["json", "rpc"] = "json",
) -> dict[str, Any]:
    # This exact selected distribution owns argument interpretation. Importing
    # its parser does not enter Pi startup, load an extension or invoke a model.
    parser = probe.program.parent.parent / "cli" / "args.js"
    assert parser.is_file() and parser.is_relative_to(probe.public_runtime)
    # Trace the exact selected public bundle to the chunk that implements main.
    assert '"./cli-runtime.js"' in probe.program.read_text(encoding="utf-8")
    runtime = probe.program.with_name("cli-runtime.js")
    imported = re.search(
        r'import\{[^}]*\bmain\b[^}]*\}from"(\./chunks/[^"]+)"',
        runtime.read_text(encoding="utf-8"),
    )
    assert imported is not None
    bundled_parser = (runtime.parent / imported.group(1)).resolve(strict=True)
    assert bundled_parser.is_relative_to(probe.public_runtime)
    node = shutil.which("node", path="/usr/bin:/bin")
    assert node, "Selected installed parser qualification requires native Node.js"
    script = """
      import { deepStrictEqual } from "node:assert";
      import { pathToFileURL } from "node:url";
      const { parseArgs: shipped } = await import(pathToFileURL(process.argv[1]).href);
      const { parseArgs: effective } = await import(pathToFileURL(process.argv[2]).href);
      const fragments = JSON.parse(process.argv[3]);
      const mode = process.argv[4];
      const normalize = result => ({...result, unknownFlags: Object.fromEntries(result.unknownFlags)});
      const observed = {};
      for (const [name, fragment] of Object.entries(fragments)) {
        const argv = [...fragment, "--mode", mode, "--no-session"];
        observed[name] = normalize(effective(argv));
        deepStrictEqual(observed[name], normalize(shipped(argv)));
      }
      process.stdout.write(JSON.stringify(observed));
    """
    result = probe.backend.execute(
        StandardIsolatedProcessRequest(
            workspace=probe.workspace,
            cwd=probe.workspace / "project",
            argv=(
                node,
                "--input-type=module",
                "--eval",
                script,
                str(parser),
                str(bundled_parser),
                json.dumps(fragments),
                mode,
            ),
            env={"PATH": "/usr/bin:/bin", "PI_SKIP_VERSION_CHECK": "1", "NO_COLOR": "1"},
            backend_configuration_id=probe.backend.configuration_identity(),
            require_workspace_current=lambda: None,
            read_only_mounts=(
                StandardReadOnlyMount(probe.public_runtime, PurePosixPath(probe.public_runtime)),
            ),
            read_only_workspace=True,
            input_data=b"",
        ),
        timeout=10,
        cancel_requested=None,
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout_truncated
    assert result.supervisor.final_process_group_quiescent
    assert result.physical_execution_id
    isolation = result.standard_isolation
    assert isolation is not None and isolation.filesystem_cleanup == "verified"
    assert isolation.workspace_read_only
    assert isolation.host_and_external_ip_networking == "denied"
    assert not isolation.broker_socket_mounted
    assert not tuple(probe.session.parent.iterdir())
    with parser.open("rb") as stream:
        parser_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    with bundled_parser.open("rb") as stream:
        effective_parser_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    print(
        json.dumps(
            {
                "native_parser_sha256": parser_sha256,
                "effective_parser_sha256": effective_parser_sha256,
                "mode": mode,
                "physical_execution_id": result.physical_execution_id,
                "supervisor": result.supervisor.receipt_identity,
                "cases": sorted(fragments),
            }
        )
    )
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_installed_pi_terminator_and_positionals_change_owned_task_boundary(
    pi_probe: _PiProbe,
) -> None:
    fragments = {
        "terminator": ("--",),
        "theme-value-terminator": ("--use-theme", "--"),
        "tui-value-terminator": ("--tui-mode", "--"),
        "theme-value-owned-option": ("--use-theme", "--resume"),
        "tui-value-owned-option": ("--tui-mode", "--resume"),
        "positional-task": ("UNADMITTED_TASK",),
        "boolean-before-task": ("--offline", "UNADMITTED_TASK"),
        "file-input": ("@unadmitted-task.txt",),
        "boolean-before-file": ("--offline", "@unadmitted-task.txt"),
    }
    observed = _native_arguments(pi_probe, fragments)
    for name in ("terminator", "theme-value-terminator", "tui-value-terminator"):
        assert "mode" not in observed[name]
        assert "noSession" not in observed[name]
        assert observed[name]["messages"] == ["--mode", "json", "--no-session"]
    # These two native options decline flag-leading values and report fatal
    # diagnostics. This proves framing refusal, not reachable model work.
    for name in (
        "theme-value-terminator",
        "tui-value-terminator",
        "theme-value-owned-option",
        "tui-value-owned-option",
    ):
        assert any(item["type"] == "error" for item in observed[name]["diagnostics"])
    for name in ("theme-value-owned-option", "tui-value-owned-option"):
        assert observed[name]["resume"] is True
        assert observed[name]["mode"] == "json" and observed[name]["noSession"] is True
    assert observed["positional-task"]["messages"] == ["UNADMITTED_TASK"]
    assert observed["boolean-before-task"]["offline"] is True
    assert observed["boolean-before-task"]["messages"] == ["UNADMITTED_TASK"]
    assert observed["file-input"]["fileArgs"] == ["unadmitted-task.txt"]
    assert observed["boolean-before-file"]["offline"] is True
    assert observed["boolean-before-file"]["fileArgs"] == ["unadmitted-task.txt"]
    for fragment in fragments.values():
        with pytest.raises(AgentProviderError):
            validate_native_agent_arguments("pi", fragment)


def test_installed_pi_missing_option_values_consume_owned_transport(pi_probe: _PiProbe) -> None:
    options = (
        "--provider",
        "--model",
        "--thinking",
        "--models",
        "--system-prompt",
        "--append-system-prompt",
        "--extension",
        "-e",
        "--skill",
        "--prompt-template",
        "--theme",
        "--tools",
        "-t",
        "--exclude-tools",
        "-xt",
    )
    fragments = {option: (option,) for option in options}
    for option, parsed in _native_arguments(pi_probe, fragments).items():
        assert "mode" not in parsed, option
        assert parsed["messages"] == ["json"], option
        assert parsed["noSession"] is True, option
    for fragment in fragments.values():
        with pytest.raises(AgentProviderError):
            validate_native_agent_arguments("pi", fragment)


def test_installed_pi_preserves_exact_and_duplicate_native_session_options(
    pi_probe: _PiProbe,
) -> None:
    cases = {
        "path": (("--session", "selected.jsonl"), "session", "selected.jsonl"),
        "id": (("--session-id", "selected-id"), "sessionId", "selected-id"),
        "directory": (("--session-dir", "selected-dir"), "sessionDir", "selected-dir"),
        "continue": (("--continue",), "continue", True),
        "continue-short": (("-c",), "continue", True),
        "resume": (("--resume",), "resume", True),
        "resume-short": (("-r",), "resume", True),
        "fork": (("--fork", "selected.jsonl"), "fork", "selected.jsonl"),
        "print": (("--print", "extra-task"), "print", True),
        "print-short": (("-p",), "print", True),
        "duplicate-path": (
            ("--session", "first.jsonl", "--session", "last.jsonl"),
            "session",
            "last.jsonl",
        ),
    }
    observed = _native_arguments(pi_probe, {name: case[0] for name, case in cases.items()})
    for name, (fragment, native_field, value) in cases.items():
        assert observed[name][native_field] == value, name
        assert observed[name]["mode"] == "json" and observed[name]["noSession"] is True
        with pytest.raises(AgentProviderError, match="owned terminal/task/session transport"):
            validate_native_agent_arguments("pi", fragment)
    assert observed["print"]["messages"] == ["extra-task"]


def test_installed_pi_reserved_equals_options_reach_extension_flag_map(pi_probe: _PiProbe) -> None:
    values = {
        "mode": "rpc",
        "session": "foreign.jsonl",
        "session-id": "foreign-identity",
        "session-dir": "foreign-directory",
        "fork": "foreign.jsonl",
        "continue": "true",
        "resume": "true",
        "no-session": "false",
        "print": "true",
    }
    fragments = {name: (f"--{name}={value}",) for name, value in values.items()}
    for name, parsed in _native_arguments(pi_probe, fragments).items():
        assert parsed["unknownFlags"] == {name: values[name]}
        assert parsed["mode"] == "json" and parsed["noSession"] is True
        assert parsed["messages"] == [] and parsed["fileArgs"] == []
    for fragment in fragments.values():
        with pytest.raises(AgentProviderError):
            validate_native_agent_arguments("pi", fragment)


def test_installed_pi_explicit_native_settings_preserve_owned_transport(pi_probe: _PiProbe) -> None:
    arguments = (
        "--provider",
        "synthetic-provider",
        "--model",
        "synthetic-model",
        "--thinking",
        "low",
        "--extension",
        "./native-settings.mjs",
        "--skill",
        "./native-skill.md",
        "--theme",
        "./native-theme.json",
        "--tools",
        "read,bash",
        "--offline",
        "--no-extensions",
    )
    fragments = {
        "settings": arguments,
        "opaque-model-value": ("--model", "--session"),
        "opaque-extension-value": ("--extension", "--mode"),
        "opaque-system-prompt": ("--system-prompt", "--session"),
        "extension-flag-value": ("--custom-fixture", "opaque-value"),
    }
    for fragment in fragments.values():
        validate_native_agent_arguments("pi", fragment)
    observed = _native_arguments(pi_probe, fragments)
    for parsed in observed.values():
        assert parsed["mode"] == "json" and parsed["noSession"] is True
        assert parsed["messages"] == [] and parsed["fileArgs"] == []
        assert parsed["diagnostics"] == []
    assert observed["opaque-model-value"]["model"] == "--session"
    assert "session" not in observed["opaque-model-value"]
    assert observed["opaque-extension-value"]["extensions"] == ["--mode"]
    assert observed["opaque-system-prompt"]["systemPrompt"] == "--session"
    assert observed["extension-flag-value"]["unknownFlags"] == {"custom-fixture": "opaque-value"}
    parsed = observed["settings"]
    assert parsed["unknownFlags"] == {}
    assert parsed["provider"] == "synthetic-provider" and parsed["model"] == "synthetic-model"
    assert parsed["thinking"] == "low"
    assert parsed["extensions"] == ["./native-settings.mjs"]
    assert parsed["skills"] == ["./native-skill.md"]
    assert parsed["themes"] == ["./native-theme.json"]
    assert parsed["tools"] == ["read", "bash"]
    assert parsed["offline"] is True and parsed["noExtensions"] is True


def test_installed_pi_model_option_spelling_is_opaque_when_consumed_as_a_value(
    pi_probe: _PiProbe,
) -> None:
    fragments = {
        "literal-model": ("--system-prompt", "--model"),
        "literal-provider": ("--append-system-prompt", "--provider"),
        "literal-model-equals": ("--system-prompt", "--model=synthetic-model"),
        "literal-model-list": ("--extension", "--models"),
        "model-options": (
            "--provider",
            "synthetic-provider",
            "--model",
            "synthetic-model",
            "--models",
            "synthetic-model",
        ),
        "extension-model-name": ("--model=synthetic-model",),
    }
    for mode in ("json", "rpc"):
        observed = _native_arguments(pi_probe, fragments, mode=mode)
        for parsed in observed.values():
            assert parsed["mode"] == mode and parsed["noSession"] is True
            assert parsed["diagnostics"] == []
            assert parsed["messages"] == [] and parsed["fileArgs"] == []
        assert observed["literal-model"]["systemPrompt"] == "--model"
        assert observed["literal-provider"]["appendSystemPrompt"] == ["--provider"]
        assert observed["literal-model-equals"]["systemPrompt"] == "--model=synthetic-model"
        assert observed["literal-model-list"]["extensions"] == ["--models"]
        for name in (
            "literal-model",
            "literal-provider",
            "literal-model-equals",
            "literal-model-list",
        ):
            assert {"model", "provider", "models"}.isdisjoint(observed[name])
            assert observed[name]["unknownFlags"] == {}
            assert validate_native_agent_arguments("pi", fragments[name]) is False
        selected = observed["model-options"]
        assert selected["provider"] == "synthetic-provider"
        assert selected["model"] == "synthetic-model"
        assert selected["models"] == ["synthetic-model"]
        assert validate_native_agent_arguments("pi", fragments["model-options"]) is True
        assert observed["extension-model-name"]["unknownFlags"] == {"model": "synthetic-model"}
        assert validate_native_agent_arguments("pi", fragments["extension-model-name"]) is True
