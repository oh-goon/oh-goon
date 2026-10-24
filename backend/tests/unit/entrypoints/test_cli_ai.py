import json
import os
import shutil
import struct
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from oh_goon import cli_ai
from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.cli_parser import build_parser
from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus, GameTapeStatus
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_process_isolation import (
    IsolatedProcessRequest,
    StandardIsolatedProcessRequest,
)
from oh_goon.runtime.agent_providers import load_agent_providers
from oh_goon.services.execution_lock_persistence import load_execution_lock


@pytest.fixture
def native_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    workspace = tmp_path / "project"
    workspace.mkdir()
    assert GitService().run("init", str(workspace)).returncode == 0
    executable = tmp_path / "pi"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    config = tmp_path / "providers.json"
    config.write_text(
        json.dumps(
            {
                "native_agents": {"pi": {"executable": str(executable)}},
                "default_model": "approved",
                "providers": {
                    "host": {
                        "api": "openai-chat-completions",
                        "base_url": "http://127.0.0.1:12345/v1",
                    }
                },
                "models": {"approved": {"provider": "host", "model": "native-model"}},
                "native_profiles": {
                    "local": {"inference_profile": "approved"},
                    "isolated": {"backend": "bubblewrap", "inference_profile": "approved"},
                },
            }
        )
    )
    detail = SimpleNamespace(
        run=SimpleNamespace(fleet_run_id="native-run", status=FleetRunStatus.COMPLETED),
        outcome=FleetRunOutcome.SUCCESS,
        game_tape=SimpleNamespace(repositories=()),
        model_dump_json=Mock(return_value='{"fleet_run_id":"native-run"}'),
    )
    runtime = SimpleNamespace(
        locations=SimpleNamespace(list=Mock(return_value=[]), add=Mock()),
        repositories=SimpleNamespace(exclusions=Mock(return_value=[])),
        plans=SimpleNamespace(
            run_native_agent=Mock(return_value=SimpleNamespace(fleet_run_id="native-run"))
        ),
        runs=SimpleNamespace(
            wait_for_terminal=Mock(return_value=detail),
            cancel=Mock(),
            game_tape_live_output=Mock(
                return_value=SimpleNamespace(chunks=(), dropped_chunk_count=0)
            ),
        ),
        shutdown=Mock(),
    )
    build = Mock(return_value=runtime)

    class CliIsolationDouble(BubblewrapProcessIsolation):
        """CLI plumbing double; the separate process regression uses real Bubblewrap."""

        capabilities = BubblewrapProcessIsolation.capabilities

        def configuration_identity(self) -> str:
            return "native-cli-test-backend"

        def execute(
            self, request: IsolatedProcessRequest | StandardIsolatedProcessRequest, **kwargs: Any
        ) -> CommandResult:
            assert isinstance(request, StandardIsolatedProcessRequest)
            request.require_workspace_current()
            return run_command(*request.argv, cwd=request.cwd, env=request.env, **kwargs)

    isolation_factory = cli_ai._isolation
    backend = CliIsolationDouble("/native/bwrap")
    monkeypatch.setattr(cli_ai, "_isolation", lambda selected: backend)
    monkeypatch.setattr(ApplicationRuntime, "build", build)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-default-config"))
    monkeypatch.delenv("OH_GOON_PROVIDERS_FILE", raising=False)
    return SimpleNamespace(
        workspace=workspace,
        executable=executable,
        config=config,
        runtime=runtime,
        build=build,
        detail=detail,
        backend=backend,
        isolation_factory=isolation_factory,
        argv=[
            "ai",
            "--agent",
            "pi",
            "--workspace",
            str(workspace),
            "--config",
            str(config),
            "--headless",
            "--prompt",
            "repair the defect",
        ],
    )


def test_ai_uses_one_resolved_selection_and_original_run_wait(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([*native_cli.argv, "--verify", "python -c 'print(42)'", "--json"]) == 0
    call = native_cli.runtime.plans.run_native_agent.call_args.kwargs
    assert call["selected"].definition.executable == str(native_cli.executable)
    assert not call["selected"].interactive
    assert call["workspace"] == native_cli.workspace
    assert call["verification"] == (("python", "-c", "print(42)"),)
    assert call["task"] == "repair the defect" and call["skill"] is None
    native_cli.runtime.locations.add.assert_called_once_with(native_cli.workspace)
    native_cli.runtime.runs.wait_for_terminal.assert_called_once_with("native-run")
    native_cli.runtime.shutdown.assert_called_once_with(wait_for_runs=False)
    native_cli.build.assert_called_once_with(
        standard_local=True,
        terminal=None,
        standard_process_isolation=native_cli.backend,
        git_service=native_cli.build.call_args.kwargs["git_service"],
    )
    assert native_cli.build.call_args.kwargs["git_service"].command_runner is not None
    output = capsys.readouterr()
    assert json.loads(output.out)["fleet_run_id"] == "native-run"
    assert "Fleet Run ID: native-run" in output.err


def test_ai_does_not_change_configured_parent_or_excluded_repository(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.runtime.locations.list.return_value = [native_cli.workspace.parent]
    assert main(native_cli.argv) == 0
    native_cli.runtime.locations.add.assert_not_called()
    native_cli.runtime.plans.run_native_agent.reset_mock()
    native_cli.runtime.repositories.exclusions.return_value = [{"path": str(native_cli.workspace)}]
    assert main(native_cli.argv) == 2
    native_cli.runtime.plans.run_native_agent.assert_not_called()
    assert "excluded" in capsys.readouterr().err


def test_ai_native_failure_returns_nonzero_and_preserves_run_detail(
    native_cli: SimpleNamespace,
) -> None:
    native_cli.detail.outcome = FleetRunOutcome.FAILED
    native_cli.detail.run.status = FleetRunStatus.FAILED
    assert main(native_cli.argv) == 1


def test_native_failure_stderr_is_visible_before_cleanup_without_stdout_source(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.detail.outcome = FleetRunOutcome.FAILED
    native_cli.detail.run.status = FleetRunStatus.FAILED
    native_cli.runtime.runs.game_tape_live_output.return_value = SimpleNamespace(
        dropped_chunk_count=0,
        chunks=(
            SimpleNamespace(stream="stdout", text="PRIVATE INITIAL PRODUCER SOURCE"),
            SimpleNamespace(stream="stderr", text="native configuration is invalid\n"),
        ),
    )

    def shutdown(**kwargs: object) -> None:
        native_cli.runtime.runs.game_tape_live_output.assert_called_once_with("native-run")

    native_cli.runtime.shutdown.side_effect = shutdown
    assert main([*native_cli.argv, "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["fleet_run_id"] == "native-run"
    assert "native configuration is invalid" in output.err
    assert "PRIVATE INITIAL" not in output.out + output.err


def test_native_failure_stderr_tail_has_utf8_byte_bound_and_omission_notice(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.detail.outcome = FleetRunOutcome.FAILED
    native_cli.runtime.runs.game_tape_live_output.return_value = SimpleNamespace(
        dropped_chunk_count=3,
        chunks=(SimpleNamespace(stream="stderr", text="OLD" + "é" * 16000),),
    )
    assert main([*native_cli.argv, "--json"]) == 1
    output = capsys.readouterr()
    assert "earlier output omitted" in output.err and "OLD" not in output.err
    assert output.err.count("é") * 2 <= 16384
    assert json.loads(output.out)["fleet_run_id"] == "native-run"


@pytest.mark.parametrize("live_available", [True, False])
def test_native_failure_reports_recorded_stop_reason_without_transient_stderr(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], live_available: bool
) -> None:
    native_cli.detail.outcome = FleetRunOutcome.FAILED
    native_cli.detail.game_tape.repositories = (
        SimpleNamespace(
            steps=(
                SimpleNamespace(
                    status=GameTapeStatus.FAILED,
                    step_id="native-agent",
                    exit_code=125,
                    native_agent_observations=(
                        SimpleNamespace(
                            agent="pi",
                            failed=True,
                            events=(
                                SimpleNamespace(kind="agent", summary="pi: stop_reason=length"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    native_cli.runtime.runs.game_tape_live_output.return_value = SimpleNamespace(
        dropped_chunk_count=0,
        chunks=(SimpleNamespace(stream="stdout", text="PRIVATE PRODUCER AND MODEL OUTPUT"),),
    )
    if not live_available:
        native_cli.runtime.runs.game_tape_live_output.side_effect = OSError("live owner closed")
    assert main([*native_cli.argv, "--json"]) == 1
    output = capsys.readouterr()
    assert json.loads(output.out)["fleet_run_id"] == "native-run"
    assert "Native protocol claim" in output.err
    assert "pi: stop_reason=length" in output.err and "125" in output.err
    assert "PRIVATE PRODUCER" not in output.out + output.err


def test_native_verification_failure_has_recorded_exit_when_logs_are_stdout_only(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.detail.outcome = FleetRunOutcome.FAILED
    native_cli.detail.game_tape.repositories = (
        SimpleNamespace(
            steps=(
                SimpleNamespace(
                    status=GameTapeStatus.FAILED,
                    step_id="verify-1",
                    exit_code=7,
                    native_agent_observations=(),
                ),
            ),
        ),
    )
    assert main(native_cli.argv) == 1
    assert "verify-1 (exit 7)" in capsys.readouterr().err


def test_recorded_native_failure_claims_are_bounded_utf8_without_stdout(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.detail.outcome = FleetRunOutcome.FAILED
    native_cli.detail.game_tape.repositories = (
        SimpleNamespace(
            steps=(
                SimpleNamespace(
                    status=GameTapeStatus.FAILED,
                    step_id="native-agent",
                    exit_code=125,
                    native_agent_observations=(
                        SimpleNamespace(
                            failed=True,
                            events=tuple(
                                SimpleNamespace(kind="agent", summary="é" * 1000) for _ in range(10)
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    assert main(native_cli.argv) == 1
    lines = capsys.readouterr().err.splitlines()
    claims = [line for line in lines if line.startswith("Native protocol claim")]
    assert len(claims) == 7
    assert all(len(line.split(": ", 1)[1].encode("utf-8")) <= 512 for line in claims)


def test_ai_ctrl_c_uses_original_cancel_and_wait(native_cli: SimpleNamespace) -> None:
    native_cli.runtime.runs.wait_for_terminal.side_effect = [KeyboardInterrupt(), native_cli.detail]
    assert main(native_cli.argv) == 130
    native_cli.runtime.runs.cancel.assert_called_once_with("native-run")
    assert native_cli.runtime.runs.wait_for_terminal.call_count == 2
    native_cli.runtime.shutdown.assert_called_once_with(wait_for_runs=False)


def test_ai_cleanup_does_not_mask_primary_failure(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.runtime.plans.run_native_agent.side_effect = AgentProviderError("original failure")
    native_cli.runtime.shutdown.side_effect = RuntimeError("secondary cleanup")
    assert main(native_cli.argv) == 2
    assert "original failure" in capsys.readouterr().err


def test_ai_missing_native_program_fails_before_application_state(
    native_cli: SimpleNamespace,
) -> None:
    native_cli.executable.unlink()
    assert main(native_cli.argv) == 2
    native_cli.build.assert_not_called()


def test_ai_requires_exact_git_root_before_application_state(native_cli: SimpleNamespace) -> None:
    nested = native_cli.workspace / "nested"
    nested.mkdir()
    argv = list(native_cli.argv)
    argv[argv.index(str(native_cli.workspace))] = str(nested)
    assert main(argv) == 2
    native_cli.build.assert_not_called()


@pytest.mark.parametrize("argument", ["", "unterminated '"])
def test_ai_invalid_verification_fails_before_application_state(
    native_cli: SimpleNamespace, argument: str
) -> None:
    assert main([*native_cli.argv, "--verify", argument]) == 2
    native_cli.build.assert_not_called()


def test_isolated_resource_caps_require_native_resource_boundary_before_application_state(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.loads(native_cli.config.read_text())
    payload["native_profiles"]["isolated"]["resources"] = {"memory_mib": 128}
    native_cli.config.write_text(json.dumps(payload))
    monkeypatch.setattr(cli_ai, "_isolation", native_cli.isolation_factory)
    monkeypatch.setattr(
        "oh_goon.cli_ai.shutil.which", lambda name: "/native/bwrap" if name == "bwrap" else None
    )
    assert main([*native_cli.argv, "--profile", "isolated"]) == 2
    native_cli.build.assert_not_called()


def test_ai_loads_selected_catalog_once(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    load = Mock(wraps=load_agent_providers)
    monkeypatch.setattr(cli_ai, "load_agent_providers", load)
    assert main(native_cli.argv) == 0
    load.assert_called_once_with(native_cli.config)


def test_ai_explicit_missing_catalog_does_not_use_empty_default(
    native_cli: SimpleNamespace,
) -> None:
    native_cli.config.unlink()
    assert main(native_cli.argv) == 2
    native_cli.build.assert_not_called()


def test_ai_absent_default_catalog_does_not_infer_native_model_connection(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", f"{native_cli.executable.parent}{os.pathsep}{os.environ['PATH']}")
    argv = list(native_cli.argv)
    index = argv.index("--config")
    del argv[index : index + 2]
    assert main(argv) == 2
    assert "managed inference" in capsys.readouterr().err
    native_cli.build.assert_not_called()


def test_ai_isolated_profile_requires_native_backend_before_state(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lookup = shutil.which
    monkeypatch.setattr(cli_ai, "_isolation", native_cli.isolation_factory)

    def selected_lookup(name: str, *, path: str | None = None) -> str | None:
        return None if name == "bwrap" else lookup(name, path=path)

    monkeypatch.setattr("oh_goon.cli_ai.shutil.which", selected_lookup)
    assert main([*native_cli.argv, "--profile", "isolated"]) == 2
    assert "requires installed Bubblewrap" in capsys.readouterr().err
    native_cli.build.assert_not_called()


def test_ai_owned_transport_replacement_rejects_before_state(native_cli: SimpleNamespace) -> None:
    payload = json.loads(native_cli.config.read_text())
    payload["native_agents"]["pi"]["arguments"] = ["--mode", "rpc"]
    native_cli.config.write_text(json.dumps(payload))
    assert main(native_cli.argv) == 2
    native_cli.build.assert_not_called()


@pytest.mark.parametrize("attack", ["missing_inference", "protocol", "local", "model_arguments"])
def test_unsupported_native_capability_fails_before_application_state(
    native_cli: SimpleNamespace, attack: str
) -> None:
    payload = json.loads(native_cli.config.read_text())
    argv = native_cli.argv
    if attack == "missing_inference":
        payload["default_model"] = None
    elif attack == "protocol":
        payload["providers"]["host"]["api"] = "ollama"
    elif attack == "local":
        argv = [*argv, "--profile", "local"]
    else:
        payload["native_agents"]["pi"]["arguments"] = ["--model", "other"]
    native_cli.config.write_text(json.dumps(payload))
    assert main(argv) == 2
    native_cli.build.assert_not_called()


def test_ai_prompt_file_is_bounded_before_application_state(native_cli: SimpleNamespace) -> None:
    prompt = native_cli.workspace / "task.txt"
    prompt.write_bytes(b"x" * 65537)
    argv = native_cli.argv[:-2] + ["--prompt-file", str(prompt)]
    assert main(argv) == 2
    native_cli.build.assert_not_called()


@pytest.mark.parametrize("source", ["argument", "file"])
def test_native_task_character_limit_precedes_application_mutation(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    text = "é" * 4001
    if source == "argument":
        argv = native_cli.argv[:-1] + [text]
    else:
        task = native_cli.workspace / "task.txt"
        task.write_text(text)
        argv = native_cli.argv[:-2] + ["--prompt-file", str(task)]
    assert main(argv) == 2
    native_cli.build.assert_not_called()
    assert "4000 authored characters" in capsys.readouterr().err


def test_ai_interactive_json_conflict_fails_before_application_state(
    native_cli: SimpleNamespace,
) -> None:
    argv = [argument for argument in native_cli.argv if argument != "--headless"]
    assert main([*argv, "--interactive", "--json"]) == 2
    native_cli.build.assert_not_called()


def test_ai_entrypoint_is_the_original_cli_frontdoor(monkeypatch: pytest.MonkeyPatch) -> None:
    delegate = Mock(return_value=13)
    monkeypatch.setattr("oh_goon.cli.main", delegate)
    assert cli_ai.main(["--agent", "pi", "--prompt", "task"]) == 13
    delegate.assert_called_once_with(["ai", "--agent", "pi", "--prompt", "task"])


def test_ai_terminal_mode_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["ai", "--agent", "pi", "--prompt", "task", "--headless", "--interactive"]
        )


def test_native_plan_run_reuses_frozen_cli_selection_and_original_run_owner(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    native_cli.runtime.plans.require_cli_plan_preparation = Mock()
    native_cli.runtime.plans.run = Mock(
        return_value=SimpleNamespace(
            fleet_run_id="native-run",
            target=SimpleNamespace(plan=SimpleNamespace(plan_id="frozen-plan")),
            status=FleetRunStatus.QUEUED,
        )
    )
    monkeypatch.setattr(
        "oh_goon.cli_runtime_plans._wait_for_terminal_with_progress",
        Mock(return_value=native_cli.detail),
    )
    load = Mock(wraps=load_agent_providers)
    monkeypatch.setattr("oh_goon.cli_ai.load_agent_providers", load)
    import yaml

    from oh_goon.core.models.repository import RepositoryIdentity

    root = native_cli.workspace.parent
    league = root / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(native_cli.workspace)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(native_cli.config))
    skill = root / "skills/task"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Task\nInspect the selected project.\n")
    (native_cli.workspace / "goon.yml").write_text("""version: 1
inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: native
  task: inspect this project
  requested_effects:
    repository_access: read_only
    recipients: [host]
""")
    config = root / "run.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "repository-goon"},
                "target": {
                    "repository_id": RepositoryIdentity.model_validate(
                        {"path": native_cli.workspace}
                    ).repository_id
                },
                "inputs": {"repo": "local:project", "skill": "local:skills/task"},
                "agent": {
                    "implementation": "pi",
                    "profile": "isolated",
                    "acceptance": "native",
                    "repository_root": str(root),
                    "skill_root": ".",
                },
            }
        )
    )
    argv = ["run", str(config), "--json"]
    assert main(argv) == 0
    load.assert_called_once_with(native_cli.config)
    call = native_cli.runtime.plans.run.call_args.kwargs
    assert call["native_agent"].agent == "pi"
    assert call["native_workspace"] == native_cli.workspace
    assert "agent_connection" not in call
    assert native_cli.build.call_args.kwargs["git_service"].command_runner is not None


def test_native_lock_captures_once_and_binds_selected_implementation(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = native_cli.workspace.parent
    skill = root / "skills/task"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Task\nFollow the supplied task.\n")
    (native_cli.workspace / "code.txt").write_text("source\n")
    recipe = root / "goon.yml"
    recipe.write_text("""inputs:
  repo: {type: repository_reference}
  skill: {type: skill_reference}
  acceptance: {type: acceptance_profile}
steps:
- uses: agent
  harness: native
  task: inspect this project
  requested_effects:
    repository_access: write
    recipients: [host]
""")
    from oh_goon.services.agent_authoring_sources import capture_agent_sources

    capture = Mock(wraps=capture_agent_sources)
    monkeypatch.setattr("oh_goon.services.agent_authoring_sources.capture_agent_sources", capture)
    import yaml

    from oh_goon.core.models.repository import RepositoryIdentity

    league = root / "league.yml"
    league.write_text(yaml.safe_dump({"version": 1, "folders": [str(native_cli.workspace)]}))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("OH_GOON_PROVIDERS_FILE", str(native_cli.config))
    recipe.rename(native_cli.workspace / "goon.yml")
    config = root / "run.yml"
    config.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "repository-goon"},
                "target": {
                    "repository_id": RepositoryIdentity.model_validate(
                        {"path": native_cli.workspace}
                    ).repository_id
                },
                "inputs": {"repo": "local:project", "skill": "local:skills/task"},
                "agent": {
                    "implementation": "pi",
                    "profile": "isolated",
                    "acceptance": "native",
                    "repository_root": str(root),
                    "skill_root": ".",
                },
            }
        )
    )
    argv = ["lock", str(config)]
    assert main(argv) == 0
    capture.assert_called_once()
    before = load_execution_lock(root / "goon.lock").digest
    native_cli.executable.write_text("#!/bin/sh\nexit 17\n")
    assert main(argv) == 0
    assert load_execution_lock(root / "goon.lock").digest != before
    native_cli.build.assert_not_called()


def test_native_workspace_flags_require_explicit_agent_before_app(
    native_cli: SimpleNamespace,
) -> None:
    with pytest.raises(SystemExit):
        main(["lock", "goon.yml", "--native-workspace", str(native_cli.workspace)])
    native_cli.build.assert_not_called()


@pytest.mark.process
@pytest.mark.skipif(shutil.which("bwrap") is None, reason="Bubblewrap is not installed")
def test_native_bubblewrap_preflight_retains_canonical_git_root(
    native_cli: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_ai, "_isolation", native_cli.isolation_factory)
    selected, backend, git = cli_ai.resolve_native_cli_selection(
        agent="pi",
        profile="isolated",
        workspace=native_cli.workspace,
        config_file=native_cli.config,
    )
    assert selected.backend_configuration_id is not None and backend is not None
    assert git.observation_runner is not None
    assert git.observe(native_cli.workspace).is_repository
    native_cli.build.assert_not_called()


@pytest.mark.skipif(os.name == "nt", reason="native POSIX terminal transport")
def test_host_terminal_preserves_bytes_resize_and_restores_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl
    import termios

    master, slave = os.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 25, 90, 0, 0))
        original = termios.tcgetattr(slave)
        flags = fcntl.fcntl(slave, fcntl.F_GETFL)
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: slave))
            patch.setattr(sys, "stdout", SimpleNamespace(fileno=lambda: slave))
            with pytest.raises(ValueError, match="primary"):
                with cli_ai._host_terminal() as terminal:
                    assert terminal.initial_size == (25, 90)
                    assert terminal.read_input(10) is None
                    os.write(master, b"key")
                    assert terminal.read_input(10) == b"key"
                    terminal.write_output(b"\x1b[31mhello\r\n")
                    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
                    assert terminal.current_size is not None and terminal.current_size() == (
                        30,
                        100,
                    )
                    raise ValueError("primary")
        assert os.read(master, 100) == b"\x1b[31mhello\r\n"
        assert termios.tcgetattr(slave) == original
        assert fcntl.fcntl(slave, fcntl.F_GETFL) == flags
    finally:
        os.close(slave)
        os.close(master)


@pytest.mark.parametrize("state", ["success", "failed", "cancelled", "interrupt", "crash"])
def test_ai_always_emits_one_durable_handoff_reference_at_admission(
    native_cli: SimpleNamespace, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    if state == "failed":
        native_cli.detail.outcome = FleetRunOutcome.FAILED
    elif state == "cancelled":
        native_cli.detail.outcome = FleetRunOutcome.CANCELLED
        native_cli.detail.run.status = FleetRunStatus.CANCELLED
    elif state == "interrupt":
        native_cli.runtime.runs.wait_for_terminal.side_effect = [
            KeyboardInterrupt(),
            native_cli.detail,
        ]
    elif state == "crash":
        native_cli.runtime.runs.wait_for_terminal.side_effect = RuntimeError("controller stopped")
    if state == "crash":
        with pytest.raises(RuntimeError, match="controller stopped"):
            main(native_cli.argv)
    else:
        assert (
            main(native_cli.argv)
            == {"success": 0, "failed": 1, "cancelled": 1, "interrupt": 130}[state]
        )
    lines = capsys.readouterr().err.splitlines()
    references = [
        line.removeprefix("Durable Run handoff argv: ")
        for line in lines
        if line.startswith("Durable Run handoff argv: ")
    ]
    assert len(references) == 1
    assert json.loads(references[0]) == ["oh-goon", "plan", "handoff", "native-run", "--json"]


def test_ai_explicit_prior_handoff_is_data_for_a_fresh_native_run(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon import cli_plan_run_readback

    prior = object()
    native_cli.runtime.runs.get = Mock(return_value=prior)
    projection = Mock(
        return_value={
            "fleet_run_id": "prior-run",
            "outcome": "FAILED",
            "remaining_work": "Unverified",
        }
    )
    monkeypatch.setattr(cli_plan_run_readback, "run_handoff_projection", projection)
    assert main([*native_cli.argv, "--handoff-from", "prior-run"]) == 0
    native_cli.runtime.runs.get.assert_called_once_with("prior-run")
    projection.assert_called_once_with(prior)
    call = native_cli.runtime.plans.run_native_agent.call_args.kwargs
    assert json.loads(call["prior_context"])["outcome"] == "FAILED"
    assert call["task"] == "repair the defect"
    assert call["selected"].definition.executable == str(native_cli.executable)
    native_cli.runtime.runs.cancel.assert_not_called()


def test_ai_oversize_prior_handoff_rejects_before_new_admission_or_catalog_mutation(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    native_cli.runtime.runs.get = Mock(return_value=object())
    monkeypatch.setattr(
        "oh_goon.cli_plan_run_readback.run_handoff_projection", lambda _: {"summary": "x" * 65536}
    )
    assert main([*native_cli.argv, "--handoff-from", "prior-run"]) == 2
    native_cli.runtime.plans.run_native_agent.assert_not_called()
    native_cli.runtime.locations.add.assert_not_called()
    assert "65536 bytes" in capsys.readouterr().err


@pytest.mark.parametrize(
    "prior",
    [
        "[]",
        "not JSON",
        '{"summary":"' + "x" * 65536 + '"}',
    ],
    ids=["non-object", "invalid-json", "oversize"],
)
def test_native_service_rejects_invalid_prior_context_before_fresh_source_or_run(
    native_cli: SimpleNamespace, prior: str
) -> None:
    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.services.fleet_plans import FleetPlanService

    runs, resolver = Mock(), Mock()
    service = FleetPlanService(
        resolver, runs, Mock(), Mock(), Mock(), content_store=Mock(), standard_local_fleet=True
    )
    with pytest.raises(FleetPlanInvalid, match="Prior Run handoff"):
        service.run_native_agent(
            selected=Mock(),
            workspace=native_cli.workspace,
            skill=None,
            task="New task",
            prior_context=prior,
        )
    assert runs.mock_calls == [] and resolver.mock_calls == []


def test_native_prior_context_is_bound_before_new_plan_lock_and_admission(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedLocalSearchAgentStep
    from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
    from oh_goon.services.fleet_plans import FleetPlanService

    selected, _, _ = cli_ai.resolve_native_cli_selection(
        agent="pi",
        profile="isolated",
        workspace=native_cli.workspace,
        config_file=native_cli.config,
    )
    step = ResolvedLocalSearchAgentStep(
        step_id="native-agent",
        argv=("python",),
        delivery_sha256="a" * 64,
        handoff_identity="fresh-handoff",
        source_step_id="native-agent",
        harness="native",
        native_agent=selected,
    )
    original = ResolvedFleetPlan.create(
        name="native",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
        native_agent=selected,
    )
    resolver, runs = Mock(), Mock()
    resolver.resolve_frozen_agents.return_value = (original, (), "b" * 64)
    service = FleetPlanService(
        resolver, runs, Mock(), Mock(), Mock(), content_store=Mock(), standard_local_fleet=True
    )
    monkeypatch.setattr(service, "require_plan_ingress_preparation", Mock())
    monkeypatch.setattr(service, "_prepare_native_agent", Mock(return_value=selected))
    target = Mock()
    monkeypatch.setattr(service, "_agent_target", target)
    lock = Mock()
    monkeypatch.setattr("oh_goon.services.fleet_plans.write_execution_lock", lock)
    service.run_native_agent(
        selected=selected,
        workspace=native_cli.workspace,
        skill=None,
        task="New task",
        prior_context=json.dumps({"fleet_run_id": "prior-run", "outcome": "FAILED"}),
    )
    resolved = target.call_args.args[0]
    assert resolved.plan_id != original.plan_id
    assert resolved.native_agent is selected
    context = json.loads(resolved.steps[0].review_context)
    assert context["authority"] == "UNTRUSTED_PRIOR_RUN_CONTEXT"
    assert context["prior_run_handoff"]["outcome"] == "FAILED"
    assert context["permission_authority"].startswith("NONE")
    from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock

    verify_execution_lock(build_execution_lock(resolved), lock.call_args.args[1])
    assert lock.call_args.args[1].digest != build_execution_lock(original).digest
    resolver.resolve_frozen_agents.assert_called_once()
    runs.start_prepared_plan.assert_called_once()
    assert step.review_context is None


def test_native_prior_json_recursion_error_is_typed_before_source_or_run(
    native_cli: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.services.fleet_plans import FleetPlanService

    runs, resolver = Mock(), Mock()
    service = FleetPlanService(
        resolver, runs, Mock(), Mock(), Mock(), content_store=Mock(), standard_local_fleet=True
    )
    monkeypatch.setattr(
        "oh_goon.services.fleet_plans.json.loads",
        Mock(side_effect=RecursionError("private parser detail")),
    )
    with pytest.raises(FleetPlanInvalid, match="Prior Run handoff") as rejected:
        service.run_native_agent(
            selected=Mock(),
            workspace=native_cli.workspace,
            skill=None,
            task="New task",
            prior_context="{}",
        )
    assert "private parser detail" not in str(rejected.value)
    assert runs.mock_calls == [] and resolver.mock_calls == []
