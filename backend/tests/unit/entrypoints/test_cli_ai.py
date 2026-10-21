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
from oh_goon.core.vocabulary import FleetRunOutcome, FleetRunStatus
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
    argv = [
        "plan",
        "run",
        str(native_cli.workspace / "goon.yml"),
        "--repository-id",
        "selected",
        "--native-agent",
        "pi",
        "--native-workspace",
        str(native_cli.workspace),
        "--agent-providers-file",
        str(native_cli.config),
        "--agent-repository-root",
        str(native_cli.workspace.parent),
        "--agent-skill-root",
        str(native_cli.workspace.parent),
        "--allow-agent-acceptance",
        "native",
        "--json",
    ]
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
    argv = [
        "lock",
        str(recipe),
        "--native-agent",
        "pi",
        "--native-workspace",
        str(native_cli.workspace),
        "--agent-providers-file",
        str(native_cli.config),
        "--agent-repository-root",
        str(root),
        "--agent-skill-root",
        str(root),
        "--allow-agent-acceptance",
        "native",
        "--input",
        "repo=local:project",
        "--input",
        "skill=local:skills/task",
        "--input",
        "acceptance=native",
    ]
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
    assert git.root(native_cli.workspace) == native_cli.workspace
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
