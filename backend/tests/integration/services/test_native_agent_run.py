"""Real native processes through admission, Step supervision and durable Game Tape.

The small native-protocol fixture proves lifecycle wiring. Installed coding-agent
and model demonstrations remain separate evidence of actual native behavior.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from dataclasses import replace
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from oh_goon._application_worker_runtime import WorkerGitConfiguration
from oh_goon.api import dependencies
from oh_goon.api.app import create_app
from oh_goon.application import ApplicationRuntime
from oh_goon.cli_ai import resolve_native_cli_selection
from oh_goon.cli_plan_run_readback import run_handoff_projection
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProvidersConfig,
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.engine.command import run_command
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import (
    codex_login_command,
    native_executable_sha256,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    NativeProfileCommandRunner,
    native_profile_command_runner,
    native_runtime_read_only_mounts,
)
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.integration.api.client import authenticated_api_client


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
    from oh_goon.repository_tooling.release.package_slim import canonical_source_identity

    executable = os.environ.get("OH_GOON_NATIVE_CODEX")
    if not executable:
        pytest.skip("Select an installed Codex executable explicitly")
    program = Path(executable).resolve(strict=True)
    runtime_root = os.environ.get("OH_GOON_NATIVE_CODEX_RUNTIME_ROOT")
    repo, home, scratch, capture, state = (
        tmp_path / name for name in ("repo", "native-auth", "scratch", "capture", "producer-state")
    )
    for directory in (repo, home, scratch, capture, state):
        directory.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    authored = repo / "README.md"
    authored.write_text("PUBLIC_SOURCE_CANARY\n")
    source_identity = canonical_source_identity(repo)
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
        "oh_goon.integrations.hashmarks",
        "--root",
        str(capture),
        "--state-directory",
        str(state),
        "--capture-identity",
        capture_identity,
        "--tool",
        "list_paths",
    )
    (scratch / "escape").symlink_to(private)
    script = scratch / "probe.py"
    script.write_text(
        "import json,os,pathlib,socket,stat,subprocess,sys\n"
        "from oh_goon.repository_tooling.release.package_slim import canonical_source_identity\n"
        f"home=pathlib.Path({str(home)!r})\n"
        f"repo=pathlib.Path({str(repo)!r})\n"
        f"scratch=pathlib.Path({str(scratch)!r})\n"
        "for path in (home/'canary',scratch/'escape'):\n"
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
        "authored=repo/'README.md'\n"
        "content=authored.read_bytes()\n"
        "authored.write_bytes(content)\n"
        "metadata={}\n"
        "for name in ('.agents','.aws','.codex'):\n"
        "    path=repo/name\n"
        "    try: info=path.lstat()\n"
        "    except FileNotFoundError: metadata[name]={'exists':False}; continue\n"
        "    assert stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)\n"
        "    descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n"
        "    try:\n"
        "        with os.scandir(descriptor) as entries: assert next(entries,None) is None\n"
        "    finally: os.close(descriptor)\n"
        "    metadata[name]={'exists':True,'mode':stat.S_IMODE(info.st_mode),"
        "'device':info.st_dev,'inode':info.st_ino,'empty':True}\n"
        f"assert canonical_source_identity(repo)=={source_identity!r}\n"
        "(scratch/'namespace-metadata.json').write_text(json.dumps(metadata,sort_keys=True))\n"
        "with (scratch/'hashmarks-output.json').open('w+b') as output:\n"
        f"    subprocess.run({producer_command!r},stdout=output,check=True)\n"
        "    output.seek(0)\n"
        "    encoded=output.read(65537)\n"
        "assert len(encoded)<=65536\n"
        "packet=json.loads(encoded)\n"
        "assert packet['producer']=='Hashmarks'\n"
        f"assert packet['capture_identity']=={capture_identity!r}\n"
        f"assert packet['producer_implementation_identity']=={producer_identity!r}\n"
        "assert packet['paths']==['repo/code.py']\n"
        "(scratch/'native-boundary-proof').write_text('PASS')\n"
        f"pathlib.Path({str(state / 'index-proof')!r}).write_text('PASS')\n"
    )
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="signed-in",
        definition=NativeAgentDefinition(
            executable=str(program),
            runtime_roots=(str(Path(runtime_root).resolve(strict=True)),) if runtime_root else (),
        ),
        profile=NativeAgentProfile(
            codex_login=CodexLoginConnection(
                home=str(home),
                model="gpt-6.1-sol",
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
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(home), **dict(command.environment)}
    feature_result = run_command(
        str(program),
        "--no-daemon",
        *settings,
        "features",
        "list",
        cwd=repo,
        env=environment,
        timeout=10.0,
        preserve_native_executable_path=True,
    )
    assert feature_result.returncode == 0, feature_result.stderr
    native_features = {
        fields[0]: fields[-1]
        for line in feature_result.stdout.splitlines()
        if len(fields := line.split()) >= 3
    }
    assert native_features.get("multi_agent") == "false", native_features
    assert native_features.get("multi_agent_v2") == "false", native_features
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
            sys.executable,
            str(script),
            str(server.server_port),
            cwd=repo,
            env=environment,
            timeout=30.0,
            preserve_native_executable_path=True,
        )
    assert result.returncode == 0, result.stderr
    assert (scratch / "native-boundary-proof").read_text() == "PASS"
    assert (state / "index-proof").read_text() == "PASS"
    assert captured_source.read_bytes() == captured_bytes
    assert canonical_source_identity(repo) == source_identity
    assert authored.read_text() == "PUBLIC_SOURCE_CANARY\n"
    assert all(not (repo / name).exists() for name in (".agents", ".aws", ".codex"))


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
    python_base = Path(sys.base_prefix).resolve()
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
        f"assert config['permissions']['oh_goon']['filesystem'][{str(python_base)!r}] == 'read'\n"
        "assert '--ignore-user-config' in sys.argv and '--ignore-rules' in sys.argv\n"
        "prompt=json.loads(pathlib.Path(config['model_instructions_file']).read_bytes())\n"
        "assert sys.stdin.buffer.read()==prompt['task'].encode('utf-8')\n"
        "assert prompt['task']=='Change VALUE to 42; retain the π canary'\n"
        "assert 'Hashmarks' in prompt['repository_evidence']\n"
        "assert 'Skill' in prompt['skill_instructions'] or 'supplied task' in prompt['skill_instructions']\n"
        "assert pathlib.Path(os.environ['CODEX_HOME'],'auth.json').is_fifo()\n"
        "pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
        "print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Done'}}))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    executable.chmod(0o700)
    catalog = tmp_path / "providers.json"
    catalog.write_text(
        AgentProvidersConfig(
            default_native_profile="signed-in",
            native_agents={"codex": NativeAgentDefinition(executable=str(executable))},
            native_profiles={
                "signed-in": NativeAgentProfile(
                    timeout_seconds=30.0,
                    workspace_roots=(str(repo),),
                    codex_login=CodexLoginConnection(
                        home=str(home), model="gpt-6-luna", reasoning_effort="low"
                    ),
                )
            },
        ).model_dump_json(),
        encoding="utf-8",
    )
    selected, isolation, git = resolve_native_cli_selection(
        agent="codex", profile="signed-in", workspace=repo, config_file=catalog
    )
    assert isolation is None
    assert type(git.command_runner) is NativeProfileCommandRunner
    assert git.observation_runner is None
    captured_git = WorkerGitConfiguration.capture(git)
    restored_git = WorkerGitConfiguration.model_validate_json(
        captured_git.model_dump_json()
    ).restore()
    assert restored_git == git
    assert captured_git.native_repository == repo and captured_git.native_agent == selected
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    runtime = ApplicationRuntime.build(standard_local=True, git_service=git)
    try:
        runtime.locations.add(repo)
        admitted = runtime.plans.run_native_agent(
            selected=selected,
            workspace=repo,
            skill=None,
            task="Change VALUE to 42; retain the π canary",
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


def _native_run_cli_readback(run_id: str, view: str, *, expected: int = 0) -> dict[str, Any]:
    arguments = [sys.executable, "-I", "-m", "oh_goon", "runs", view, run_id, "--json"]
    if view == "events":
        arguments.extend(("--after", "0", "--limit", "1"))
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            arguments,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=45,
        )
        output.seek(0)
        errors.seek(0)
        stdout, stderr = output.read(1_048_577), errors.read(65_537)
    assert len(stdout) <= 1_048_576 and len(stderr) <= 65_536
    assert completed.returncode == expected, (stdout, stderr)
    payload: dict[str, Any] = json.loads(stdout)
    payload.pop("_cli", None)  # Global CLI publication metadata is not a Run fact.
    return payload


def _native_readback_state(database: Path) -> str:
    """Observe all durable tables, including worker claims, through native SQLite."""
    digest = hashlib.sha256()
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("BEGIN")
        for statement in connection.iterdump():
            digest.update(statement.encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def _assert_native_run_surfaces(runtime: ApplicationRuntime, run_id: str) -> None:
    """Read one original native Run through actual CLI and authenticated API transport."""
    detail = runtime.runs.get(run_id)
    tape = detail.game_tape
    app = create_app(local_access_token="synthetic-native-readback-token")
    app.dependency_overrides[dependencies.fleet_run_control_plane] = lambda: runtime.runs
    # Do not enter the application lifespan: this is an observer, not another
    # application startup/recovery/scheduler against the selected state root.
    with closing(authenticated_api_client(app)) as client:
        response = client.get(f"/api/fleet/runs/{run_id}")
        assert response.status_code == 200
        assert response.json() == detail.model_dump(mode="json")
        assert _native_run_cli_readback(run_id, "tape") == response.json()["game_tape"]
        handoff = _native_run_cli_readback(run_id, "handoff")
        assert handoff == run_handoff_projection(detail)
        assert handoff["outcome"] == detail.outcome.value
        assert handoff["recorded_steps"]["omitted_steps"] == 0
        steps = (step for repository in tape.repositories for step in repository.steps)
        for step, retained in zip(steps, handoff["recorded_steps"]["steps"], strict=True):
            assert retained["step_execution_id"] == step.step_execution_id
            assert retained["status"] == step.status.value
            assert retained["settlement"] == step.settlement.model_dump(mode="json")
            if step.native_agent_observations:
                original = step.native_agent_observations[-1]
                claim = retained["native_claims"][0]
                assert claim["authority"] == "UNTRUSTED_HARNESS_CLAIM"
                assert claim["input_identity"] == original.input_identity
                assert claim["source_identity"] == original.source_identity
                assert claim["occurrence"] == original.occurrence
                assert claim["settled_claim"] is original.settled
                assert claim["failed_claim"] is original.failed
                assert claim["omitted_events"] == (
                    max(0, len(original.events) - len(claim["events"]))
                    + original.omitted_observations
                )
            else:
                assert retained["native_claims"] == []
                assert retained["native_claim_availability"] == "unavailable"

        event_response = client.get(
            f"/api/fleet/runs/{run_id}/events", params={"after": 0, "limit": 1}
        )
        assert event_response.status_code == 200
        page = event_response.json()
        assert page == runtime.runs.events(run_id, 0, limit=1).model_dump(mode="json")
        assert _native_run_cli_readback(run_id, "events") == page
        assert len(page["events"]) == 1 and page["has_more"]
        assert page["next_sequence"] == page["events"][-1]["sequence"]

        # Process-local output is gone, while accepted output evidence remains.
        logs = client.get(f"/api/fleet/runs/{run_id}/live-output")
        assert logs.status_code == 200
        assert _native_run_cli_readback(run_id, "logs", expected=3) == logs.json()
        assert logs.json()["state"] == "UNAVAILABLE"
        assert logs.json()["available"] is False and logs.json()["chunks"] == []
        assert logs.json()["failure_navigation_state"] == "UNAVAILABLE"

        outputs = _native_run_cli_readback(run_id, "outputs")
        assert outputs["fleet_run_id"] == run_id
        assert outputs["artifact_observation"] is None  # Unobserved is not known absent.
        for repository, projected in zip(tape.repositories, outputs["repositories"], strict=True):
            assert projected["repository_execution_id"] == repository.repository_execution_id
            for step, output in zip(repository.steps, projected["steps"], strict=True):
                assert output["step_execution_id"] == step.step_execution_id
                assert output["outputs"] == [item.model_dump(mode="json") for item in step.outputs]
                assert output["artifacts"] == [
                    item.model_dump(mode="json") for item in step.artifacts
                ]
                for name in ("stdout_evidence", "stderr_evidence"):
                    evidence = getattr(step, name)
                    assert output[name] == (
                        evidence.model_dump(mode="json") if evidence is not None else None
                    )

    # Reads did not rewrite the accepted outcome, native claims, or verification.
    assert runtime.runs.get(run_id).model_dump(mode="json") == detail.model_dump(mode="json")


@pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("bwrap") is None, reason="Bubblewrap required"
)
def test_cross_harness_handoff_keeps_separate_runs_and_original_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protocol fixtures prove interchangeability through real supervised child Runs."""
    repository = tmp_path / "repos" / "app"
    repository.mkdir(parents=True)
    (repository / "code.py").write_text("VALUE = 41\n")
    (repository / "verify.py").write_text("from code import VALUE\nassert VALUE == 42\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    backend = BubblewrapProcessIsolation(shutil.which("bwrap") or "")
    runtime: ApplicationRuntime | None = None
    run_ids: list[str] = []
    recorded: list[FleetRunDetail] = []
    handoff: dict[str, Any] | None = None
    try:
        for agent in ("codex", "pi", "opencode"):
            executable = tmp_path / f"native-{agent}"
            previous_id = run_ids[-1] if run_ids else None
            previous_agent = "codex" if agent == "pi" else "pi"
            executable.write_text(
                "#!/usr/bin/python3\n"
                "import hashlib,json,pathlib,sys\n"
                f"agent={agent!r}\nprevious_id={previous_id!r}\n"
                "prompt=json.load(sys.stdin)\n"
                "assert 'Hashmarks' in prompt['repository_evidence']\n"
                "assert not any(flag in sys.argv for flag in ('resume','--resume','--session','--continue','--attach'))\n"
                "context=prompt['context']\n"
                "if previous_id is None:\n"
                "    assert context == {}\n"
                "else:\n"
                "    assert context['authority']=='UNTRUSTED_PRIOR_RUN_CONTEXT'\n"
                "    assert context['permission_authority']=='NONE; fresh source capture and phase admission required'\n"
                "    prior=context['prior_run_handoff']\n"
                "    assert prior['fleet_run_id']==previous_id\n"
                f"    assert prior['recorded_steps']['steps'][0]['native_claims'][0]['agent']=={previous_agent!r}\n"
                "    checks=prior['recorded_steps']['steps']\n"
                "    assert checks[1]['status']==('FAILED' if agent=='pi' else 'PASSED')\n"
                "    assert checks[1]['native_claims']==[]\n"
                "    if agent=='pi':\n"
                "        claims=json.dumps(checks[0]['native_claims'][0]['events'])\n"
                "        assert 'wrong-value' in claims and 'VALUE must be 42' in claims\n"
                "captured=pathlib.Path(prompt['captured_repository'],'code.py').read_text()\n"
                "assert captured==('VALUE = 42\\n' if agent=='opencode' else 'VALUE = 41\\n')\n"
                "if agent=='pi':\n"
                "    assert '--no-session' in sys.argv\n"
                "    pathlib.Path('code.py').write_text('VALUE = 42\\n')\n"
                "    text='Repaired the independently failing value'\n"
                "    events=[{'type':'agent_start'},{'type':'message_end','message':{'role':'assistant','content':text,'stopReason':'stop'}},{'type':'agent_settled','aborted':False}]\n"
                "else:\n"
                "    record={'schema':'oh-goon.agent-review/v1',**prompt['identity'],'verdict':'CLEAN','summary':'Reviewed the captured source','reviewed_paths':{'complete_scope':prompt['identity']['scope_identity']},'findings':[]}\n"
                "    if agent=='codex':\n"
                "        assert '--ephemeral' in sys.argv\n"
                "        record.update(verdict='FINDING',findings=[{'finding_id':'wrong-value','explanation':'VALUE must be 42','evidence':[{'path':'repo/code.py','sha256':hashlib.sha256(captured.encode()).hexdigest(),'line':1}]}])\n"
                "    text=json.dumps(record)\n"
                "    if agent=='codex':\n"
                "        events=[{'type':'item.completed','item':{'type':'agent_message','text':text}},{'type':'turn.completed'}]\n"
                "    else:\n"
                "        events=[{'type':'step_start'},{'type':'text','part':{'text':text,'time':{'end':1}}},{'type':'step_finish','part':{'reason':'stop'}}]\n"
                "for event in events: print(json.dumps(event),flush=True)\n"
            )
            executable.chmod(0o700)
            connection = AgentConnection.select(
                AgentProvidersConfig.model_validate(
                    {
                        "providers": {
                            "fixture": {
                                "api": "openai-responses"
                                if agent == "codex"
                                else "openai-chat-completions",
                                "base_url": "http://127.0.0.1:1/v1",
                            }
                        },
                        "models": {"fixture": {"provider": "fixture", "model": "fixture"}},
                    }
                ),
                "fixture",
            )
            selected = ResolvedNativeAgent(
                agent=agent,
                profile_name="isolated-fixture",
                definition=NativeAgentDefinition(executable=str(executable)),
                profile=NativeAgentProfile(
                    backend="bubblewrap", inference_profile="fixture", timeout_seconds=30.0
                ),
                executable_sha256=native_executable_sha256(executable),
                connection=connection,
                backend_configuration_id=backend.configuration_identity(),
            )
            if runtime is not None:
                runtime.shutdown(wait_for_runs=True)
                runtime = None
            runner = native_profile_command_runner(
                repository,
                selected,
                backend,
                RepositoryWorkspaceAuthority.pin_repository_subject,
            )
            runtime = ApplicationRuntime.build(
                standard_local=True,
                standard_process_isolation=backend,
                git_service=GitService(
                    command_runner=runner,
                    observation_runner=partial(runner, read_only_workspace=True),
                ),
            )
            runtime.locations.add(repository)
            admitted = runtime.plans.run_native_agent(
                selected=selected,
                workspace=repository,
                skill=None,
                task="Repair the value" if agent == "pi" else "Review the value",
                role="report" if agent == "pi" else "review",
                prior_context=json.dumps(handoff) if handoff is not None else None,
                verification=((sys.executable, "verify.py"),),
            )
            run_ids.append(admitted.fleet_run_id)
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id, timeout=45)
            assert detail.outcome is (
                FleetRunOutcome.ATTENTION_REQUIRED if agent == "codex" else FleetRunOutcome.SUCCESS
            ), runtime.runs.game_tape_live_output(admitted.fleet_run_id).model_dump_json()
            steps = detail.game_tape.repositories[0].steps
            assert steps[0].status is GameTapeStatus.PASSED
            assert steps[0].native_agent_observations[0].agent == agent
            assert steps[1].native_agent_observations == ()
            recorded.append(detail)
            for original in recorded:
                retained = runtime.runs.get(original.run.fleet_run_id)
                assert retained.run == original.run
                for old, current in zip(
                    original.game_tape.repositories[0].steps,
                    retained.game_tape.repositories[0].steps,
                    strict=True,
                ):
                    assert current.status is old.status
                    assert current.exit_code == old.exit_code
                    assert current.settlement == old.settlement
                    assert current.native_agent_observations == old.native_agent_observations
            handoff = run_handoff_projection(detail)
        assert len(set(run_ids)) == 3
    finally:
        if runtime is not None:
            runtime.shutdown(wait_for_runs=True)

    # Retire the fixture executables before any fresh observer opens the original
    # Journal. Readback cannot need a live harness or silently rerun its work.
    for agent in ("codex", "pi", "opencode"):
        (tmp_path / f"native-{agent}").unlink()
    database = tmp_path / "state" / "app.sqlite"
    before_readback = _native_readback_state(database)
    reloaded = ApplicationRuntime.build(startup_purpose="observation")
    try:
        observations = []
        snapshots = []
        for run_id, agent in zip(run_ids, ("codex", "pi", "opencode"), strict=True):
            detail = reloaded.runs.get(run_id)
            assert detail.run.fleet_run_id == detail.game_tape.fleet_run_id == run_id
            steps = detail.game_tape.repositories[0].steps
            observations.append(steps[0].native_agent_observations[0])
            snapshots.append(
                detail.repository_admissions[0].source_authority.source_snapshot.sha256
            )
            assert observations[-1].agent == agent
            if agent == "codex":
                summaries = "\n".join(event.summary for event in observations[-1].events)
                assert "wrong-value" in summaries and "VALUE must be 42" in summaries
            assert steps[1].status is (
                GameTapeStatus.FAILED if agent == "codex" else GameTapeStatus.PASSED
            )
            assert observations[-1].authority == "UNTRUSTED_HARNESS_CLAIM"
            assert observations[-1].settled and not observations[-1].failed
            assert steps[0].stdout_evidence is not None
            _assert_native_run_surfaces(reloaded, run_id)
        assert len({item.input_identity for item in observations}) == 3
        assert len({item.source_identity for item in observations}) == 3
        assert snapshots[0] == snapshots[1]
        assert snapshots[2] != snapshots[1]
        assert (repository / "code.py").read_text() == "VALUE = 42\n"
    finally:
        reloaded.shutdown(wait_for_runs=True)
    assert _native_readback_state(database) == before_readback


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
