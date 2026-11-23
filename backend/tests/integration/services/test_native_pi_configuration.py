"""Selected native Pi configuration and tools through the original isolated Run."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.core.models.agent_providers import PiSessionIntent, PiSessionUnavailable
from oh_goon.core.models.run_events import (
    ProcessExitedEvent,
    ProcessStartedEvent,
    StepAttemptCreatedEvent,
)
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from tests.fakes.native_lifetime import NativeDescendant, bind_native_descendant

_TASK = "Review the synthetic repository with the selected native tools."
_SKILL = "# Pi configuration fixture\nReview the selected source without changing it.\n"
_BROKER_KEY = "synthetic-local-native-pi-key"
_APPROVED_EXTENSION = "fixture_inspect"
_DENIED_EXTENSION = "fixture_denied"
_APPROVED_MCP = "mcp__approved__probe"
_DENIED_MCP = "mcp__denied__probe"
_MCP_CHILD = "oh-goon-approved-mcp-fixture"
_CONFIG_CANARY = "Selected public Pi configuration marker."
_NATIVE_SKILL_CANARY = "Selected public Pi native skill marker."
_PROJECT_CONTEXT_CANARY = "Inactive project-native context marker."
_PROJECT_SKILL_CANARY = "Inactive project-native skill marker."

_MCP_SERVER = r"""
import json
import pathlib
import sys

label, marker = sys.argv[1:]
pathlib.Path(marker).write_text(label)
for line in sys.stdin:
    assert len(line) < 65536
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": label, "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{
            "name": "probe",
            "description": "Read the synthetic repository fixture",
            "inputSchema": {"type": "object", "properties": {}},
        }]}
    elif method == "tools/call":
        assert request["params"]["name"] == "probe"
        source_ok = pathlib.Path("source.py").read_text() == "VALUE = 42\n"
        try:
            pathlib.Path("mcp-denied-write").write_text("fixture")
        except OSError:
            denied = True
        else:
            denied = False
        result = {"content": [{"type": "text", "text": json.dumps({
            "fixture": "mcp", "source": source_ok, "workspace_write_denied": denied,
        })}]}
    else:
        assert method == "ping", method
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""

_APPROVED_EXTENSION_CONTENT = """
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
export default function(pi) {
  writeFileSync('/config/native/approved-extension.started', 'fixture');
  pi.registerTool({
    name: 'fixture_inspect',
    label: 'Fixture inspect',
    description: 'Check public configuration overlay and synthetic repository',
    parameters: { type: 'object', properties: {} },
    async execute() {
      let workspaceWriteDenied = false;
      try { writeFileSync('extension-denied-write', 'fixture'); }
      catch { workspaceWriteDenied = true; }
      return { content: [{ type: 'text', text: JSON.stringify({
        fixture: 'extension',
        source: readFileSync('source.py', 'utf8') === 'VALUE = 42\\n',
        workspace_write_denied: workspaceWriteDenied,
        overlay_writable: existsSync('/config/native/approved-extension.started'),
        denied_extension_absent: !existsSync('/config/native/denied-extension.started'),
        denied_mcp_absent: !existsSync('/config/native/denied-mcp.started'),
        unrelated_native_store_absent: privatePaths.every(path => !existsSync(path)),
        host_broker_key_absent: process.env.OH_GOON_PI_CONFIGURATION_TEST_KEY === undefined,
      }) }] };
    },
  });
}
"""

_DENIED_EXTENSION_CONTENT = """
import { writeFileSync } from 'node:fs';
export default function(pi) {
  writeFileSync('/config/native/denied-extension.started', 'fixture');
  pi.registerTool({
    name: 'fixture_denied',
    label: 'Fixture denied',
    description: 'Must not be exposed',
    parameters: { type: 'object', properties: {} },
    async execute() { return { content: [{type: 'text', text: 'unexpected'}] }; },
  });
}
"""


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(item["text"] for item in content if item.get("type") == "text")
    return ""


def _tool_call(name: str, ordinal: int, arguments: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {
                "index": 0,
                "id": f"call_config_{ordinal}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments or {})},
            }
        ],
    }


def _source_prompt(body: dict[str, Any], task: str = _TASK) -> dict[str, Any]:
    assert body["model"] == "pi-configuration-fixture"
    privileged = "\n".join(
        _text(message) for message in body["messages"] if message["role"] in {"system", "developer"}
    )
    assert task in privileged and _SKILL in privileged
    for message in body["messages"]:
        if message["role"] != "user":
            continue
        text = _text(message)
        start = text.find('{"source_authority":')
        if start >= 0:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
            if payload.get("task") != task:
                continue
            evidence = json.loads(payload["repository_evidence"])
            assert evidence["producer"] == "Hashmarks"
            assert (
                evidence["producer_implementation_identity"] == require_loaded_hashmarks_runtime()
            )
            assert evidence["capture_identity"] == payload["identity"]["candidate_identity"]
            assert payload["repository_evidence"] in privileged
            assert isinstance(payload, dict)
            return payload
    raise AssertionError("Current frozen source input missing")


def _review(payload: dict[str, Any]) -> dict[str, Any]:
    identity = payload["identity"]
    return {
        "role": "assistant",
        "content": json.dumps(
            {
                "schema": "oh-goon.agent-review/v1",
                **identity,
                "verdict": "CLEAN",
                "summary": "Synthetic native tool boundary verified.",
                "reviewed_paths": {"complete_scope": identity["scope_identity"]},
                "findings": [],
            }
        ),
    }


@contextmanager
def _server(
    respond: Callable[[dict[str, Any]], dict[str, Any]],
) -> Iterator[tuple[str, list[str]]]:
    errors: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                assert self.path == "/v1/chat/completions"
                assert self.headers["Authorization"] == f"Bearer {_BROKER_KEY}"
                size = int(self.headers["Content-Length"])
                assert 0 < size < 1024 * 1024
                delta = respond(json.loads(self.rfile.read(size)))
            except Exception as error:
                errors.append(repr(error))
                delta = {"role": "assistant", "content": "Synthetic provider refusal."}
            finish = "tool_calls" if "tool_calls" in delta else "stop"
            chunks = [
                {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
            ]
            data = (
                "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as endpoint:
        thread = Thread(target=endpoint.serve_forever)
        thread.start()
        try:
            yield f"http://127.0.0.1:{endpoint.server_port}/v1", errors
        finally:
            endpoint.shutdown()
            thread.join(timeout=5)
    assert not thread.is_alive()


def _catalog(
    repository: Path,
    program: Path,
    root: Path,
    url: str,
    native: Path | None = None,
    arguments: tuple[str, ...] = (),
) -> str:
    return json.dumps(
        {
            "providers": {
                "local": {
                    "api": "openai-chat-completions",
                    "base_url": url,
                    "api_key_env": "OH_GOON_PI_CONFIGURATION_TEST_KEY",
                }
            },
            "models": {"fixture": {"provider": "local", "model": "pi-configuration-fixture"}},
            "native_profiles": {
                "isolated": {
                    "backend": "bubblewrap",
                    "allowed_agents": ["pi"],
                    "workspace_roots": [str(repository)],
                    "inference_profile": "fixture",
                    "pi_rpc": True,
                    "timeout_seconds": 45,
                }
            },
            "native_agents": {
                "pi": {
                    "executable": str(program),
                    "runtime_roots": [str(root)],
                    "configuration_directory": str(native) if native is not None else None,
                    "arguments": list(arguments),
                }
            },
        }
    )


@pytest.fixture
def installed_pi_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path, Path]:
    executable = os.environ.get("OH_GOON_NATIVE_PI")
    runtime_root = os.environ.get("OH_GOON_NATIVE_PI_RUNTIME_ROOT")
    if not executable or not runtime_root:
        pytest.skip("Select the installed Pi executable and public runtime root")
    program, public_runtime = (
        Path(executable).resolve(strict=True),
        Path(runtime_root).resolve(strict=True),
    )
    assert program.is_relative_to(public_runtime) and shutil.which("bwrap")
    repository = tmp_path / "repos" / "app"
    repository.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(repository)), check=True, timeout=10)
    (repository / "source.py").write_text("VALUE = 42\n")
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(_SKILL)
    for name, path in {
        "HOME": tmp_path / "synthetic-home",
        "XDG_CONFIG_HOME": tmp_path / "config",
        "XDG_DATA_HOME": tmp_path / "data",
        "OH_GOON_DB": tmp_path / "state" / "app.sqlite",
    }.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("OH_GOON_PI_CONFIGURATION_TEST_KEY", _BROKER_KEY)
    return program, public_runtime, repository, skill


def _verification() -> tuple[tuple[str, ...], ...]:
    return (
        (
            "/usr/bin/python3",
            "-I",
            "-c",
            "from pathlib import Path; assert Path('source.py').read_text() == 'VALUE = 42\\n'",
        ),
    )


@pytest.mark.certification
def test_installed_pi_public_overlay_selects_actual_native_extension_and_mcp(
    tmp_path: Path,
    installed_pi_configuration: tuple[Path, Path, Path, Path],
) -> None:
    program, public_runtime, repository, skill = installed_pi_configuration
    native = tmp_path / "public-native"
    native.mkdir()
    private_home = tmp_path / "synthetic-home" / ".pi" / "agent"
    private_session = private_home / "sessions" / "unrelated.jsonl"
    private_session.parent.mkdir(parents=True)
    private_session.write_text("UNRELATED_SYNTHETIC_PI_SESSION")
    private_config = private_home / "settings.json"
    private_config.write_text("{}")
    (native / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "oh-goon": {
                        "baseUrl": "http://127.0.0.1:18743/v1",
                        "api": "openai-completions",
                        "apiKey": "oh-goon-local",
                        "models": [{"id": "pi-configuration-fixture", "reasoning": True}],
                    }
                }
            }
        )
    )
    (native / "settings.json").write_text(
        json.dumps(
            {
                "defaultProvider": "oh-goon",
                "defaultModel": "pi-configuration-fixture",
                "defaultThinkingLevel": "low",
                "extensions": ["/config/native/denied.mjs"],
            }
        )
    )
    (native / "allowed.mjs").write_text(
        f"const privatePaths = {json.dumps([str(private_config), str(private_session)])};\n"
        + _APPROVED_EXTENSION_CONTENT
    )
    (native / "denied.mjs").write_text(_DENIED_EXTENSION_CONTENT)
    (native / "server.py").write_text(_MCP_SERVER)
    (native / "APPEND_SYSTEM.md").write_text(_CONFIG_CANARY + "\n")
    (native / "fixture-skill.md").write_text(
        "---\nname: fixture-skill\ndescription: " + _NATIVE_SKILL_CANARY + "\n---\n"
        "# Fixture native skill\nRead the synthetic source.\n"
    )
    (native / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "approved": {
                        "command": "/usr/bin/python3",
                        "args": [
                            "-I",
                            "/config/native/server.py",
                            _MCP_CHILD,
                            "/config/native/approved-mcp.started",
                        ],
                        "exposure": "direct",
                        "timeout": 5,
                    },
                    "denied": {
                        "command": "/usr/bin/python3",
                        "args": [
                            "-I",
                            "/config/native/server.py",
                            "denied-probe",
                            "/config/native/denied-mcp.started",
                        ],
                        "exposure": "direct",
                        "enabled": False,
                    },
                }
            }
        )
    )
    original = {item.name: item.read_bytes() for item in native.iterdir()}
    tools = (
        _APPROVED_EXTENSION,
        _APPROVED_MCP,
        _DENIED_MCP,
        _DENIED_EXTENSION,
        _APPROVED_EXTENSION,
    )
    replies: list[str] = []
    run_ready = Event()
    bound: NativeDescendant | None = None

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        nonlocal bound
        payload = _source_prompt(body)
        assert body["reasoning_effort"] == "low", "Pi did not apply its selected native settings"
        privileged = "\n".join(
            _text(message)
            for message in body["messages"]
            if message["role"] in {"system", "developer"}
        )
        assert _CONFIG_CANARY in privileged and _NATIVE_SKILL_CANARY in privileged
        exposed = {item["function"]["name"] for item in body["tools"]}
        assert {_APPROVED_EXTENSION, _APPROVED_MCP} <= exposed, exposed
        assert {_DENIED_EXTENSION, _DENIED_MCP}.isdisjoint(exposed)
        results = [_text(message) for message in body["messages"] if message["role"] == "tool"]
        count = len(results)
        assert count == len(replies) <= len(tools)
        if count in {1, 5}:
            assert json.loads(results[-1]) == {
                "fixture": "extension",
                "source": True,
                "workspace_write_denied": True,
                "overlay_writable": True,
                "denied_extension_absent": True,
                "denied_mcp_absent": True,
                "unrelated_native_store_absent": True,
                "host_broker_key_absent": True,
            }
        if count == 2:
            assert json.loads(results[-1]) == {
                "fixture": "mcp",
                "source": True,
                "workspace_write_denied": True,
            }
            assert run_ready.wait(10)
            events = runtime.runs.events(admitted.fleet_run_id).events
            attempt = next(
                row
                for row in events
                if isinstance(row, StepAttemptCreatedEvent) and row.step_order == 1
            )
            exited = {
                row.physical_execution_id for row in events if type(row) is ProcessExitedEvent
            }
            (active,) = [
                row
                for row in events
                if type(row) is ProcessStartedEvent
                and row.step_attempt_id == attempt.step_attempt_id
                and row.physical_execution_id not in exited
            ]
            bound = lifetime.enter_context(bind_native_descendant(active, _MCP_CHILD))
            assert not select.select([bound.child_fd], [], [], 0)[0]
        if count in {3, 4}:
            denied = results[-1].lower()
            assert tools[count - 1] in denied and ("not found" in denied or "unknown" in denied), (
                denied
            )
        if count == len(tools):
            return _review(payload)
        replies.append(tools[count])
        return _tool_call(tools[count], count)

    with ExitStack() as lifetime, _server(respond) as (url, errors):
        catalog = tmp_path / "providers.json"
        catalog.write_text(
            _catalog(
                repository,
                program,
                public_runtime,
                url,
                native,
                (
                    "--no-extensions",
                    "--extension",
                    "builtin:mcp",
                    "--extension",
                    "/config/native/allowed.mjs",
                    "--skill",
                    "/config/native/fixture-skill.md",
                ),
            )
        )
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="isolated",
            workspace=repository,
            config_file=catalog,
            session=PiSessionIntent(mode="ephemeral"),
        )
        runtime = ApplicationRuntime.build(
            standard_local=True, standard_process_isolation=backend, git_service=git
        )
        try:
            runtime.locations.add(repository)
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=repository,
                skill=skill,
                task=_TASK,
                role="review",
                verification=_verification(),
            )
            run_ready.set()
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=60)
            assert detail.outcome is FleetRunOutcome.SUCCESS, (
                errors,
                replies,
                runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[-6500:],
            )
            assert not errors and tuple(replies) == tools
            assert bound is not None and select.select([bound.child_fd], [], [], 0)[0]
            agent, verifier = detail.game_tape.repositories[0].steps
            assert agent.settlement.outcome == verifier.settlement.outcome == "SUCCESS"
            assert agent.settlement.cleanup == verifier.settlement.cleanup == "VERIFIED"
            isolation = agent.attempts[0].standard_isolation
            assert isolation is not None and isolation.workspace_read_only
            assert (
                isolation.private_configuration_overlay
                and isolation.filesystem_cleanup == "verified"
            )
            assert not (repository / "extension-denied-write").exists()
            assert not (repository / "mcp-denied-write").exists()
            assert {item.name: item.read_bytes() for item in native.iterdir()} == original
        finally:
            runtime.shutdown(wait_for_runs=True)


@pytest.mark.certification
def test_installed_pi_continuation_excludes_native_project_features(
    tmp_path: Path,
    installed_pi_configuration: tuple[Path, Path, Path, Path],
) -> None:
    program, public_runtime, repository, skill = installed_pi_configuration
    prior_task = "Record the original synthetic review for a later new Run."
    project_extension = "fixture_project_probe"
    project_mcp = "mcp__project__probe"
    extension_marker = "/tmp/oh-goon-pi-project-extension-started"
    mcp_marker = "/tmp/oh-goon-pi-project-mcp-started"
    project_native = repository / ".pi"
    project_native.mkdir()
    requests: list[str] = []
    continuing = False

    def respond(body: dict[str, Any]) -> dict[str, Any]:
        if not continuing:
            payload = _source_prompt(body, prior_task)
            assert not requests
            requests.append("prior")
            return _review(payload)
        payload = _source_prompt(body)
        users = "\n".join(
            _text(message) for message in body["messages"] if message["role"] == "user"
        )
        assert prior_task in users, "The native exact reopen did not retain the prior turn"
        privileged = "\n".join(
            _text(message)
            for message in body["messages"]
            if message["role"] in {"system", "developer"}
        ).replace(payload["repository_evidence"], "")
        assert not any(
            item in privileged
            for item in (prior_task, _PROJECT_CONTEXT_CANARY, _PROJECT_SKILL_CANARY)
        )
        assert {item["function"]["name"] for item in body["tools"]} == {
            "read",
            "grep",
            "find",
            "ls",
        }
        results = [_text(message) for message in body["messages"] if message["role"] == "tool"]
        count = len(results)
        assert len(requests) == count + 1
        if count >= 1:
            expected = (project_mcp, project_extension, extension_marker, mcp_marker)[count - 1]
            result = results[-1].lower()
            assert expected in result
            assert any(word in result for word in ("not found", "unknown", "enoent")), result
        calls: tuple[tuple[str, dict[str, str]], ...] = (
            (project_mcp, {}),
            (project_extension, {}),
            ("read", {"path": extension_marker}),
            ("read", {"path": mcp_marker}),
        )
        requests.append("current")
        if count == len(calls):
            return _review(payload)
        name, arguments = calls[count]
        return _tool_call(name, count, arguments)

    with _server(respond) as (url, errors):
        catalog = tmp_path / "providers.json"
        catalog.write_text(_catalog(repository, program, public_runtime, url))
        original, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="isolated",
            workspace=repository,
            config_file=catalog,
            session=PiSessionIntent(mode="new"),
        )
        prior_runtime = ApplicationRuntime.build(
            standard_local=True, standard_process_isolation=backend, git_service=git
        )
        try:
            prior_runtime.locations.add(repository)
            admitted = prior_runtime.plans.run_native_agent(
                selected=original,
                workspace=repository,
                skill=skill,
                task=prior_task,
                role="review",
                verification=_verification(),
            )
            prior_id = admitted.fleet_run_id
            assert prior_runtime.runs.wait_for_terminal(prior_id, timeout=60).outcome is (
                FleetRunOutcome.SUCCESS
            ), errors
            assert requests == ["prior"] and not errors
        finally:
            prior_runtime.shutdown(wait_for_runs=True)

        discovered_extension = project_native / "extensions" / "fixture.mjs"
        discovered_extension.parent.mkdir(parents=True)
        discovered_extension.write_text(
            _DENIED_EXTENSION_CONTENT.replace(
                "/config/native/denied-extension.started", extension_marker
            ).replace(_DENIED_EXTENSION, project_extension)
        )
        (project_native / "APPEND_SYSTEM.md").write_text(_PROJECT_CONTEXT_CANARY + "\n")
        discovered_skill = project_native / "skills" / "fixture" / "SKILL.md"
        discovered_skill.parent.mkdir(parents=True)
        discovered_skill.write_text(
            "---\nname: fixture\ndescription: " + _PROJECT_SKILL_CANARY + "\n---\n"
            "# Project fixture native skill\nRead the synthetic repository.\n"
        )
        (project_native / "server.py").write_text(_MCP_SERVER)
        (project_native / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "project": {
                            "command": "/usr/bin/python3",
                            "args": [
                                "-I",
                                str(project_native / "server.py"),
                                "project-probe",
                                mcp_marker,
                            ],
                            "exposure": "direct",
                        }
                    }
                }
            )
        )
        selected_session = PiSessionIntent(mode="continue", prior_run_id=prior_id)
        public_native = tmp_path / "other-public-config"
        public_native.mkdir()
        for options in (
            _catalog(repository, program, public_runtime, url, native=public_native),
            _catalog(
                repository,
                program,
                public_runtime,
                url,
                arguments=("--extension", str(discovered_extension)),
            ),
        ):
            catalog.write_text(options)
            with pytest.raises(PiSessionUnavailable):
                resolve_native_cli_selection(
                    agent="pi",
                    profile="isolated",
                    workspace=repository,
                    config_file=catalog,
                    session=selected_session,
                )
        assert requests == ["prior"] and not errors
        catalog.write_text(_catalog(repository, program, public_runtime, url))
        selected, backend, git = resolve_native_cli_selection(
            agent="pi",
            profile="isolated",
            workspace=repository,
            config_file=catalog,
            session=selected_session,
        )
        runtime = ApplicationRuntime.build(
            standard_local=True, standard_process_isolation=backend, git_service=git
        )
        try:
            continuing = True
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=repository,
                skill=skill,
                task=_TASK,
                role="review",
                verification=_verification(),
            )
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=60)
            assert detail.outcome is FleetRunOutcome.SUCCESS, (
                errors,
                requests,
                runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[-6500:],
            )
            assert not errors and requests == ["prior", *(["current"] * 5)]
            evidence = detail.game_tape.pi_session
            assert evidence is not None and evidence.prior_run_id == prior_id
            assert evidence.persistence == "observed" and evidence.native is not None
            agent, verifier = detail.game_tape.repositories[0].steps
            assert agent.settlement.outcome == verifier.settlement.outcome == "SUCCESS"
            assert agent.settlement.cleanup == verifier.settlement.cleanup == "VERIFIED"
            assert runtime.runs.get(prior_id).outcome is FleetRunOutcome.SUCCESS
        finally:
            runtime.shutdown(wait_for_runs=True)
