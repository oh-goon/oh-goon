"""Installed Pi protocol observations; persistent product Runs remain unsupported.

The native extension supplies synthetic model replies without network or credentials.
Pi owns its session format, loading and persistence. Each process uses the original
Bubblewrap/command owners, separate disposable configuration and retained test data.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import pytest

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.engine.command import CommandCancelledError, CommandResult, run_command
from oh_goon.integrations.agent_native_execution import (
    native_executable_sha256,
    validate_native_agent_arguments,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    StandardIsolatedProcessRequest,
    StandardReadOnlyMount,
)

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
    ) -> CommandResult:
        self.launches += 1
        config = self.root / f"config-{self.launches}"
        config.mkdir(mode=0o700)
        extension = config / "provider.mjs"
        extension.write_text(_PROVIDER, encoding="utf-8")
        (config / "settings.json").write_text(
            json.dumps({"defaultProvider": "wp00", "defaultModel": "session-fixture"}),
            encoding="utf-8",
        )
        argv = (
            str(self.program),
            "--offline",
            "--mode",
            mode,
            "--no-extensions",
            "--extension",
            str(extension),
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
            "--session-dir",
            str(self.session.parent),
            *(
                ("--session-id", session_id)
                if session_id is not None
                else ("--session", str(session or self.session))
            ),
            *((prompt,) if prompt is not None else ()),
        )
        streaming = False
        pending = ""

        def observe(_stream: Literal["stdout", "stderr"], text: str) -> None:
            nonlocal streaming, pending
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
            workspace=self.workspace,
            cwd=cwd or self.workspace / "project",
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
            input_data=(
                b'{"id":"state","type":"get_state"}\n{"id":"entries","type":"get_entries"}\n'
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
        if resource == "renamed":
            assert renamed.read_bytes() == good


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


def _native_arguments(probe: _PiProbe, fragments: Mapping[str, tuple[str, ...]]) -> dict[str, Any]:
    # This exact selected distribution owns argument interpretation. Importing
    # its parser does not enter Pi startup, load an extension or invoke a model.
    parser = probe.program.parent.parent / "cli" / "args.js"
    assert parser.is_file() and parser.is_relative_to(probe.public_runtime)
    node = shutil.which("node", path="/usr/bin:/bin")
    assert node, "Selected installed parser qualification requires native Node.js"
    script = """
      import { pathToFileURL } from "node:url";
      const { parseArgs } = await import(pathToFileURL(process.argv[1]).href);
      const fragments = JSON.parse(process.argv[2]);
      const observed = {};
      for (const [name, fragment] of Object.entries(fragments)) {
        const parsed = parseArgs([...fragment, "--mode", "json", "--no-session"]);
        observed[name] = {...parsed, unknownFlags: Object.fromEntries(parsed.unknownFlags)};
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
                json.dumps(fragments),
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
    print(
        json.dumps(
            {
                "native_parser_sha256": parser_sha256,
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
