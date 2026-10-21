"""Real native processes through admission, Step supervision and durable Game Tape.

The small native-protocol fixture proves lifecycle wiring. Installed coding-agent
and model demonstrations remain separate evidence of actual native behavior.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Literal

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import native_profile_command_runner
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="Bubblewrap required"
)
@pytest.mark.parametrize(
    "invalid_input",
    [
        "special-config",
        "host-runtime",
        "active-database",
        "readonly-database",
        "private-config",
        "private-runtime",
        "tool-authority",
    ],
)
def test_public_native_configuration_is_preflighted_before_run_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_input: str
) -> None:
    repo = tmp_path / "teams" / "app"
    if invalid_input == "tool-authority":
        repo = tmp_path / "state" / "extensions" / "app"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    configuration = tmp_path / "public-native-config"
    if invalid_input == "private-config":
        configuration = tmp_path / "state" / "executor-control" / "public-native-config"
    configuration.mkdir(parents=True)
    if invalid_input == "special-config":
        os.mkfifo(configuration / "unapproved-ipc")
    database = (
        repo / ".application" / "database.sqlite"
        if invalid_input in {"active-database", "readonly-database"}
        else tmp_path / "state" / "database.sqlite"
    )
    monkeypatch.setenv("OH_GOON_DB", str(database))
    runtime_roots: tuple[str, ...] = ()
    if invalid_input == "private-runtime":
        private_runtime = tmp_path / "state" / "content-store"
        private_runtime.mkdir(parents=True)
        runtime_roots = (str(private_runtime),)
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    catalog = AgentProvidersConfig.model_validate_json(
        json.dumps(
            {
                "default_model": "selected",
                "providers": {
                    "approved": {
                        "api": "openai-chat-completions",
                        "base_url": "http://127.0.0.1:1/v1",
                    }
                },
                "models": {"selected": {"provider": "approved", "model": "fixture/model"}},
            }
        )
    )
    selected = ResolvedNativeAgent(
        agent="pi",
        profile_name="managed",
        definition=NativeAgentDefinition(
            executable=str(Path(sys.executable).resolve()),
            configuration_directory=str(configuration),
            runtime_roots=runtime_roots,
        ),
        profile=NativeAgentProfile(backend="bubblewrap", inference_profile="selected"),
        executable_sha256=native_executable_sha256(Path(sys.executable).resolve()),
        connection=AgentConnection.select(catalog),
        backend_configuration_id=backend.configuration_identity(),
    )
    runner = native_profile_command_runner(
        repo, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    runtime = ApplicationRuntime.build(
        standard_local=True,
        standard_process_isolation=backend,
        git_service=GitService(
            command_runner=runner, observation_runner=partial(runner, read_only_workspace=True)
        ),
    )
    try:
        if invalid_input == "host-runtime":
            monkeypatch.setattr(sys, "prefix", str(repo))
        reason = {
            "special-config": "IPC/special",
            "host-runtime": "loaded host control-plane",
            "tool-authority": "host-managed tool authority",
        }.get(invalid_input, "private application state")
        with pytest.raises(FleetPlanInvalid, match=reason):
            runtime.plans.run_native_agent(
                selected=selected,
                workspace=repo,
                skill=None,
                task="Must not start",
                role="review" if invalid_input == "readonly-database" else "report",
            )
        assert runtime.runs.history().items == ()
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="Bubblewrap required"
)
@pytest.mark.parametrize("verification_value", [42, 43])
@pytest.mark.parametrize("agent", ["pi", "codex"])
def test_native_run_keeps_every_model_turn_bound_and_retains_step_local_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verification_value: int,
    agent: Literal["pi", "codex"],
) -> None:
    observed: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(body)
            payload = json.dumps(
                {"choices": [{"message": {"content": "fixture response"}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            pass

    repo = tmp_path / "teams" / "app"
    repo.mkdir(parents=True)
    (repo / "code.py").write_text("VALUE = 41\n")
    (repo / "verify.py").write_text(
        f"from code import VALUE\nassert VALUE == {verification_value}\n"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    skill = tmp_path / "skills" / "fixture" / "native"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Fixture Skill\nRetain the native-skill-canary.\n")
    executable = tmp_path / "native-fixture"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json, os, pathlib, sys, tomllib, urllib.request\n"
        f"agent = {agent!r}\n"
        "if agent == 'codex':\n"
        "    config = tomllib.loads(pathlib.Path(os.environ['CODEX_HOME'], 'config.toml').read_text())\n"
        "    assert config['web_search'] == 'disabled'\n"
        "assert 'NATIVE_RUN_KEY' not in os.environ\n"
        "assert os.environ['OH_GOON_NATIVE_BROKER_KEY'] == 'oh-goon-local'\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type': 'agent_start'}), flush=True)\n"
        "for label in ['initial reasoning', 'follow-up after context compaction']:\n"
        "    field = 'messages' if agent == 'pi' else 'input'\n"
        "    body = json.dumps({'model': 'fixture/model', field: [{'role': 'user', 'content': label}]}).encode()\n"
        "    route = 'chat/completions' if agent == 'pi' else 'responses'\n"
        "    request = urllib.request.Request('http://127.0.0.1:18743/v1/' + route, data=body, headers={'Content-Type': 'application/json'})\n"
        "    with urllib.request.urlopen(request, timeout=10) as response:\n"
        "        assert json.load(response)['choices']\n"
        "    print(json.dumps({'type': 'auto_compaction_end'}), flush=True)\n"
        "pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
        "if agent == 'pi':\n"
        "    print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant', 'content': 'Fixture completed', 'stopReason': 'stop'}}), flush=True)\n"
        "    print(json.dumps({'type': 'agent_settled', 'aborted': False}), flush=True)\n"
        "else:\n"
        "    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Fixture completed'}}), flush=True)\n"
        "    print(json.dumps({'type': 'turn.completed'}), flush=True)\n"
    )
    executable.chmod(0o700)
    # The database's parent is also the approved repository's parent, not a privacy root.
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "custom.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("NATIVE_RUN_KEY", "host-only-key")
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            catalog = AgentProvidersConfig.model_validate_json(
                json.dumps(
                    {
                        "providers": {
                            "approved": {
                                "api": "openai-chat-completions"
                                if agent == "pi"
                                else "openai-responses",
                                "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                                "api_key_env": "NATIVE_RUN_KEY",
                            }
                        },
                        "models": {"selected": {"provider": "approved", "model": "fixture/model"}},
                    }
                )
            )
            selected = ResolvedNativeAgent(
                agent=agent,
                profile_name="managed",
                definition=NativeAgentDefinition(executable=str(executable)),
                profile=NativeAgentProfile(
                    backend="bubblewrap", inference_profile="selected", timeout_seconds=30.0
                ),
                executable_sha256=native_executable_sha256(executable),
                connection=AgentConnection.select(catalog, "selected"),
                backend_configuration_id=backend.configuration_identity(),
            )
            command_runner = native_profile_command_runner(
                repo, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
            )
            git = GitService(
                command_runner=command_runner,
                observation_runner=partial(command_runner, read_only_workspace=True),
            )
            runtime = ApplicationRuntime.build(
                standard_local=True, standard_process_isolation=backend, git_service=git
            )
            try:
                runtime.locations.add(repo)
                admitted = runtime.plans.run_native_agent(
                    selected=selected,
                    workspace=repo,
                    skill=skill,
                    task="Read code.py, retain native-skill-canary, and change VALUE to 42.",
                    verification=((sys.executable, "verify.py"),),
                )
                detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
                expected = (
                    FleetRunOutcome.SUCCESS
                    if verification_value == 42
                    else FleetRunOutcome.ATTENTION_REQUIRED
                )
                assert detail.outcome is expected, runtime.runs.game_tape_live_output(
                    admitted.fleet_run_id
                ).model_dump_json()
                steps = detail.game_tape.repositories[0].steps
                assert len(steps) == 2
                assert steps[0].status is GameTapeStatus.PASSED
                assert steps[1].status is (
                    GameTapeStatus.PASSED if verification_value == 42 else GameTapeStatus.FAILED
                )
                for step in steps:
                    isolation = step.attempts[0].standard_isolation
                    assert isolation is not None
                    assert isolation.backend.configuration_id == selected.backend_configuration_id
                    assert isolation.host_and_external_ip_networking == "denied"
                native = steps[0].native_agent_observations
                assert len(native) == 1 and native[0].agent == agent
                assert native[0].authority == "UNTRUSTED_HARNESS_CLAIM"
                assert native[0].settled and not native[0].failed
                assert any(event.kind == "agent" for event in native[0].events)
                if agent == "pi":
                    assert any(event.kind == "context" for event in native[0].events)
                assert steps[1].native_agent_observations == ()
                assert len(observed) == 2
                for request in observed:
                    context = request["messages" if agent == "pi" else "input"][0]["content"]
                    assert "Hashmarks" in context
                    assert "native-skill-canary" in context
                    assert "host-only-key" not in json.dumps(request)
                assert "host-only-key" not in detail.model_dump_json()
                assert (repo / "code.py").read_text() == "VALUE = 42\n"
            finally:
                runtime.shutdown(wait_for_runs=True)
        finally:
            server.shutdown()
            worker.join(timeout=5)
    assert not worker.is_alive()
