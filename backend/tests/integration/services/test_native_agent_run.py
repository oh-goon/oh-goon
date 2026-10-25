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
from dataclasses import replace
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
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import (
    codex_login_command,
    native_executable_sha256,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    native_profile_command_runner,
    native_runtime_read_only_mounts,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


@pytest.mark.parametrize(
    "invalid",
    [
        "runtime-root",
        "runtime-root-descendant",
        "runtime-root-equal",
        "runtime-root-symlink",
        "authentication-home-symlink",
        "workspace",
        "review",
        "agent",
    ],
)
def test_codex_login_conflicts_reject_before_run_admission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    repo = tmp_path / "teams" / "app"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "native-auth"
    home.mkdir()
    if invalid == "authentication-home-symlink":
        alias = tmp_path / "home-link"
        alias.symlink_to(home, target_is_directory=True)
        home = alias
    program = tmp_path / "bin" / "codex"
    program.parent.mkdir()
    native_effect = tmp_path / "native-started"
    program.write_text(f"#!/bin/sh\ntouch '{native_effect}'\nexit 99\n")
    program.chmod(0o700)
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="signed-in",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            codex_login=CodexLoginConnection(
                home=str(repo if invalid == "workspace" else home),
                model="gpt-6-luna",
                reasoning_effort="low",
            )
        ),
        executable_sha256=native_executable_sha256(program),
    )
    if invalid == "runtime-root":
        selected = replace(
            selected,
            definition=selected.definition.model_copy(update={"runtime_roots": (str(tmp_path),)}),
        )
    elif invalid in {
        "runtime-root-descendant",
        "runtime-root-equal",
        "runtime-root-symlink",
        "authentication-home-symlink",
    }:
        root = home if invalid == "runtime-root-equal" else home.resolve() / "public-runtime"
        root.mkdir(exist_ok=True)
        if invalid == "runtime-root-symlink":
            alias = tmp_path / "runtime-link"
            alias.symlink_to(root, target_is_directory=True)
            root = alias
        selected = replace(
            selected,
            definition=selected.definition.model_copy(update={"runtime_roots": (str(root),)}),
        )
    elif invalid == "agent":
        selected = replace(selected, agent="pi")
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        with pytest.raises(FleetPlanInvalid, match="Codex"):
            runtime.plans.run_native_agent(
                selected=selected,
                workspace=repo,
                skill=None,
                task="Must not start",
                role="review" if invalid == "review" else "report",
            )
        assert runtime.runs.history().items == ()
        assert not native_effect.exists()
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.certification
def test_installed_codex_enforces_the_selected_native_tool_boundary(tmp_path: Path) -> None:
    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    if not executable:
        pytest.skip("Select an installed Codex executable explicitly")
    program = Path(executable).resolve(strict=True)
    repo, home, scratch, capture, state = (
        tmp_path / name for name in ("repo", "native-auth", "scratch", "capture", "producer-state")
    )
    for directory in (repo, home, scratch, capture, state):
        directory.mkdir()
    private = home / "canary"
    private.write_text("synthetic private data; never a real credential")
    (capture / "evidence").write_text("frozen producer evidence")
    captured_repository = capture / "repo"
    captured_repository.mkdir()
    captured_source = captured_repository / "code.py"
    captured_bytes = b"def handle_widget():\n    return 42\n"
    captured_source.write_bytes(captured_bytes)
    capture_identity = "native-boundary-captured-source"
    producer_identity = require_loaded_hashmarks_runtime()
    producer_command = (
        sys.executable,
        "-I",
        "-m",
        "oh_goon.integrations.hashmarks.agent_tools",
        "--root",
        str(capture),
        "--state-directory",
        str(state),
        "--capture-identity",
        capture_identity,
        "--tool",
        "list_paths",
    )
    (repo / "escape").symlink_to(private)
    script = repo / "probe.py"
    script.write_text(
        "import json,pathlib,socket,subprocess,sys\n"
        f"home=pathlib.Path({str(home)!r})\n"
        "for path in (home/'canary',pathlib.Path('escape')):\n"
        "    try: path.read_bytes()\n"
        "    except OSError: pass\n"
        "    else: raise AssertionError('private path was readable')\n"
        f"evidence=pathlib.Path({str(capture / 'evidence')!r})\n"
        "assert evidence.read_text()=='frozen producer evidence'\n"
        "try: evidence.write_text('changed')\n"
        "except OSError: pass\n"
        "else: raise AssertionError('capture was writable')\n"
        "try: socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=1)\n"
        "except OSError: pass\n"
        "else: raise AssertionError('host loopback was reachable')\n"
        "with pathlib.Path('hashmarks-output.json').open('w+b') as output:\n"
        f"    subprocess.run({producer_command!r},stdout=output,check=True)\n"
        "    output.seek(0)\n"
        "    encoded=output.read(65537)\n"
        "assert len(encoded)<=65536\n"
        "packet=json.loads(encoded)\n"
        "assert packet['producer']=='Hashmarks'\n"
        f"assert packet['capture_identity']=={capture_identity!r}\n"
        f"assert packet['producer_implementation_identity']=={producer_identity!r}\n"
        "assert packet['paths']==['repo/code.py']\n"
        "pathlib.Path('native-boundary-proof').write_text('PASS')\n"
        f"pathlib.Path({str(state / 'index-proof')!r}).write_text('PASS')\n"
    )
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="signed-in",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            codex_login=CodexLoginConnection(
                home=str(home),
                model="gpt-6-luna",
                reasoning_effort="low",
            )
        ),
        executable_sha256=native_executable_sha256(program),
    )
    runtime_mounts = native_runtime_read_only_mounts(repo, selected)
    command = codex_login_command(
        selected,
        instructions=capture / "evidence",
        workspace=repo,
        scratch=scratch,
        evidence_state=state,
        read_roots=(
            capture,
            program.parent,
            *(mount.source for mount in runtime_mounts),
            *(Path(mount.target) for mount in runtime_mounts),
        ),
    )
    settings = tuple(
        value
        for index, value in enumerate(command.argv)
        if index > 0 and (command.argv[index - 1] == "-c" or value == "-c")
    )
    with ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler) as server:
        result = run_command(
            str(program),
            "--no-daemon",
            *settings,
            "sandbox",
            "--permission-profile",
            "oh_goon",
            "--cd",
            str(repo),
            "--",
            "/usr/bin/python3",
            str(script),
            str(server.server_port),
            cwd=repo,
            env={"PATH": "/usr/bin:/bin", "HOME": str(home), **dict(command.environment)},
            timeout=30.0,
            preserve_native_executable_path=True,
        )
    assert result.returncode == 0, result.stderr
    assert (repo / "native-boundary-proof").read_text() == "PASS"
    assert (state / "index-proof").read_text() == "PASS"
    assert captured_source.read_bytes() == captured_bytes


@pytest.mark.parametrize("verification_value", [42, 43])
def test_codex_login_uses_original_run_and_independent_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    verification_value: int,
) -> None:
    repo = tmp_path / "teams" / "app"
    repo.mkdir(parents=True)
    (repo / "code.py").write_text("VALUE = 41\n")
    (repo / "verify.py").write_text(
        f"from code import VALUE\nassert VALUE == {verification_value}\n"
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    home = tmp_path / "native-auth"
    home.mkdir()
    python_alias = tmp_path / "python-runtime-alias"
    python_base = Path(sys.base_prefix).resolve()
    python_alias.symlink_to(python_base, target_is_directory=True)
    interpreter_suffix = Path(sys._base_executable).resolve().relative_to(python_base)
    monkeypatch.setattr(sys, "_base_executable", str(python_alias / interpreter_suffix))
    os.mkfifo(home / "auth.json")
    (tmp_path / "bin").mkdir()
    executable = tmp_path / "bin" / "native-codex"
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json,os,pathlib,sys,tomllib\n"
        "config={}\n"
        "for i,arg in enumerate(sys.argv):\n"
        "    if arg == '-c': config.update(tomllib.loads(sys.argv[i+1]))\n"
        "assert config['model']=='gpt-6-luna'\n"
        "assert config['model_reasoning_effort']=='low'\n"
        "assert config['model_provider']=='openai'\n"
        "assert config['approval_policy']=='never'\n"
        "assert not config['permissions']['oh_goon']['network']['enabled']\n"
        f"assert config['permissions']['oh_goon']['filesystem'][{str(python_alias)!r}] == 'read'\n"
        "assert '--ignore-user-config' in sys.argv and '--ignore-rules' in sys.argv\n"
        "prompt=json.loads(sys.stdin.read())\n"
        "assert json.loads(pathlib.Path(config['model_instructions_file']).read_text())==prompt\n"
        "assert 'Hashmarks' in prompt['repository_evidence']\n"
        "assert 'Skill' in prompt['skill_instructions'] or 'supplied task' in prompt['skill_instructions']\n"
        "assert pathlib.Path(os.environ['CODEX_HOME'],'auth.json').is_fifo()\n"
        "pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Done'}}))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    executable.chmod(0o700)
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="signed-in",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            timeout_seconds=30.0,
            codex_login=CodexLoginConnection(
                home=str(home),
                model="gpt-6-luna",
                reasoning_effort="low",
            ),
        ),
        executable_sha256=native_executable_sha256(executable),
    )
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    runner = native_profile_command_runner(
        repo, selected, None, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    runtime = ApplicationRuntime.build(
        standard_local=True, git_service=GitService(command_runner=runner)
    )
    try:
        runtime.locations.add(repo)
        admitted = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repo,
            skill=None,
            task="Change VALUE to 42",
            verification=((sys.executable, "verify.py"),),
        )
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
        assert detail.outcome is (
            FleetRunOutcome.SUCCESS
            if verification_value == 42
            else FleetRunOutcome.ATTENTION_REQUIRED
        ), runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()
        steps = detail.game_tape.repositories[0].steps
        assert steps[0].status is GameTapeStatus.PASSED
        assert steps[0].native_agent_observations[0].settled
        assert steps[0].attempts[0].standard_isolation is None
        assert steps[1].status is (
            GameTapeStatus.PASSED if verification_value == 42 else GameTapeStatus.FAILED
        )
        assert (repo / "code.py").read_text() == "VALUE = 42\n"
        assert (home / "auth.json").is_fifo()
    finally:
        runtime.shutdown(wait_for_runs=True)


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
                assert detail.outcome is expected, (
                    "Run failure: "
                    + detail.run.model_dump_json(include={"status", "failure"})[:8192]
                    + "\nLive output: "
                    + runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()[
                        -8192:
                    ]
                )
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


@pytest.mark.parametrize("mode", ["failure", "malformed", "timeout", "output_limit"])
def test_protected_codex_failure_withholds_private_native_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    repository = tmp_path / "teams" / "protected"
    repository.mkdir(parents=True)
    (repository / "source.py").write_text("VALUE = 41\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    executable = tmp_path / "bin" / "codex-fixture"
    executable.parent.mkdir()
    executable.write_text(
        "#!/usr/bin/python3\n"
        "import json,sys,time\n"
        "sys.stdin.read()\n"
        "secret='SYNTHETIC_PRIVATE_NATIVE_OUTPUT'\n"
        "print(secret, file=sys.stderr, flush=True)\n"
        "print(json.dumps({'type':'item.completed','item':{'id':secret,'type':'command_execution','aggregated_output':secret}}),flush=True)\n"
        + (
            "time.sleep(30)\n"
            if mode == "timeout"
            else "sys.stdout.write(secret * 90000); sys.stdout.flush()\n"
            if mode == "output_limit"
            else "print(secret,flush=True)\n"
            if mode == "malformed"
            else "sys.exit(7)\n"
        )
    )
    executable.chmod(0o700)
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="protected-fixture",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            backend="bubblewrap",
            sandbox_owner="execution_backend",
            timeout_seconds=10.0,
            inference_profile="selected",
        ),
        executable_sha256=native_executable_sha256(executable),
        connection=AgentConnection.select(
            AgentProvidersConfig.model_validate(
                {
                    "providers": {
                        "synthetic": {
                            "api": "openai-responses",
                            "base_url": "http://127.0.0.1:1/v1",
                        }
                    },
                    "models": {"selected": {"provider": "synthetic", "model": "synthetic"}},
                }
            ),
            "selected",
        ),
        backend_configuration_id=backend.configuration_identity(),
    )
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    runner = native_profile_command_runner(
        repository, selected, backend, RepositoryWorkspaceAuthority.pin_repository_subject
    )
    runtime = ApplicationRuntime.build(
        standard_local=True,
        standard_process_isolation=backend,
        git_service=GitService(
            command_runner=runner, observation_runner=partial(runner, read_only_workspace=True)
        ),
    )
    try:
        runtime.locations.add(repository)
        admitted = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repository,
            skill=None,
            task="Review captured source",
            role="review",
        )
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=30)
        assert detail.outcome is not FleetRunOutcome.SUCCESS
        live = runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()
        assert "SYNTHETIC_PRIVATE_NATIVE_OUTPUT" not in live
        assert "SYNTHETIC_PRIVATE_NATIVE_OUTPUT" not in detail.model_dump_json()
        assert (repository / "source.py").read_text() == "VALUE = 41\n"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_protected_coding_report_rejects_before_run_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    executable = tmp_path / "codex"
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o700)
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="protected",
        definition=NativeAgentDefinition(executable=str(executable)),
        profile=NativeAgentProfile(
            backend="bubblewrap",
            sandbox_owner="execution_backend",
            inference_profile="selected",
        ),
        executable_sha256=native_executable_sha256(executable),
        connection=AgentConnection.select(
            AgentProvidersConfig.model_validate(
                {
                    "providers": {
                        "synthetic": {
                            "api": "openai-responses",
                            "base_url": "http://127.0.0.1:1/v1",
                        }
                    },
                    "models": {"selected": {"provider": "synthetic", "model": "synthetic"}},
                }
            ),
            "selected",
        ),
    )
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "database.sqlite"))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        with pytest.raises(FleetPlanInvalid, match="separately admitted scoped apply"):
            runtime.plans.run_native_agent(
                selected=selected,
                workspace=repository,
                skill=None,
                task="Write source",
            )
        assert runtime.runs.history().items == ()
    finally:
        runtime.shutdown(wait_for_runs=True)
