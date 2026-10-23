from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from oh_goon.core.models.agent_providers import (
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedLocalSearchAgentStep,
)
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.engine.command import CommandResult
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_step_output_observation import observed_command_runner
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_contained_plan_timeout_is_canonical_failed_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="timeout",
        description="",
        steps=(ResolvedCommandStep(step_id="slow", argv=("slow-command",), timeout_seconds=1),),
    )

    def fake_run_command(*argv: str, **kwargs):
        del kwargs
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    execution = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        tmp_path, plan, cancel_requested=None
    )

    step = execution.steps[0]
    assert isinstance(step, FailedPlanStepExecution)
    assert step.returncode == 124
    assert "timed out" in execution.stderr


def test_fleet_tool_store_prepares_once_and_preflight_checks_without_syncing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        calls.append((tuple(argv), dict(kwargs)))
        project_index = argv.index("--project") + 1
        project = Path(argv[project_index])
        if argv[1] == "lock":
            (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        elif argv[1] == "sync" and "--check" not in argv:
            environment = Path(cast(dict[str, str], kwargs["env"])["UV_PROJECT_ENVIRONMENT"])
            executable = (
                environment
                / ("Scripts" if os.name == "nt" else "bin")
                / ("pre-commit.exe" if os.name == "nt" else "pre-commit")
            )
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("", encoding="utf-8")
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    monkeypatch.setenv("HOME", "/home/operator")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/operator/.config")
    monkeypatch.setenv("UV_FUTURE_NATIVE_SETTING", "opaque-native-value")
    monkeypatch.setenv("REPO_A_SECRET", "must-not-enter-preparation")
    monkeypatch.setattr("oh_goon.engine.fleet_tools.run_command", fake_run_command)
    store = FleetToolStore(tmp_path / "plan-tools", path_authority=TEST_PATH_AUTHORITY)
    runtime = store.prepare_pre_commit("pre-commit==4.6.2")
    calls.clear()

    repeated = store.prepare_pre_commit("pre-commit==4.6.2")
    assert repeated == runtime
    assert len(calls) == 1
    assert "--check" in calls[0][0]
    calls.clear()

    executable = store.check_pre_commit(
        runtime_id=runtime.runtime_id,
        requirement=runtime.requirement,
        pyproject_sha256=runtime.pyproject_sha256,
        uv_lock_sha256=runtime.uv_lock_sha256,
    )

    assert executable.is_file()
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:2] == (str(TEST_PATH_AUTHORITY.tool("uv")), "sync")
    environment = cast(dict[str, str], kwargs["env"])
    assert "REPO_A_SECRET" not in environment
    assert environment["HOME"] == "/home/operator"
    assert environment["XDG_CONFIG_HOME"] == "/home/operator/.config"
    assert environment["UV_FUTURE_NATIVE_SETTING"] == "opaque-native-value"
    assert environment["XDG_CACHE_HOME"].startswith(str(store.root))
    assert "--check" in argv


def test_non_strict_single_attempt_reuses_initial_git_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "baseline")
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="observation-reuse",
        description="",
        steps=(ResolvedCommandStep(timeout_seconds=3600, step_id="once", argv=("noop",)),),
    )
    observations = 0
    original_observe = GitService.observe

    def observe_once_per_boundary(self, repository, cancel_requested=None):
        nonlocal observations
        observations += 1
        return original_observe(self, repository, cancel_requested)

    monkeypatch.setattr(GitService, "observe", observe_once_per_boundary)
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: CommandResult(
            argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1
        ),
    )

    PhysicalFleetPlanExecutorHarness(GitService()).execute(repository, plan, cancel_requested=None)

    assert observations == 2


def test_direct_steps_reobserve_repository_after_inter_step_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    tracked = repository / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "baseline")
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="inter-step-mutation",
        description="",
        steps=(
            ResolvedCommandStep(timeout_seconds=3600, step_id="first", argv=("noop", "first")),
            ResolvedCommandStep(timeout_seconds=3600, step_id="second", argv=("noop", "second")),
        ),
    )
    observations = []
    original_observe = GitService.observe

    def observe_with_external_mutation(self, path, cancel_requested=None):
        observation = original_observe(self, path, cancel_requested)
        observations.append(observation)
        if len(observations) == 2:
            tracked.write_text("mutated-between-steps\n", encoding="utf-8")
        return observation

    monkeypatch.setattr(GitService, "observe", observe_with_external_mutation)
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        lambda *argv, **kwargs: CommandResult(
            argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1
        ),
    )

    execution = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        repository, plan, cancel_requested=None
    )

    first, second = execution.steps
    assert len(observations) == 4
    assert first.repository_state_after != second.repository_state_before
    assert "tracked.txt" in observations[2].changed_files


def test_live_output_observer_failure_emits_diagnostic_without_changing_command_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step

    class Diagnostics:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event) -> None:
            self.events.append(event)

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        kwargs["stream_output"]("stdout", "hello\n")
        return CommandResult(
            argv=tuple(argv),
            returncode=0,
            stdout="hello",
            stderr="",
            duration_ms=1,
        )

    def broken_observer(*_args) -> None:
        raise RuntimeError("live output store unavailable")

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command",
        fake_run_command,
    )
    diagnostics = Diagnostics()
    runtime = StepRuntime(
        git=GitService(),
        extensions=None,
        tools=None,
        cancel_requested=None,
        diagnostics=diagnostics,
        fleet_run_id="run-output",
        repository_id="repo-output",
    )
    step = ResolvedCommandStep(step_id="echo", argv=("echo", "hello"), timeout_seconds=10)

    outcome = execute_direct_step(
        tmp_path,
        step,
        runtime,
        step_order=1,
        output_observer=broken_observer,
    )

    assert outcome.returncode == 0
    assert len(diagnostics.events) == 1
    event = diagnostics.events[0]
    assert event.fleet_run_id == "run-output"
    assert event.repository_id == "repo-output"
    assert event.step_id == "echo"
    assert event.operation == "stream_stdout"
    assert event.execution_affected is False
    assert event.diagnostic_capability_affected == "live_output_capture"


@pytest.mark.parametrize("launch", ["protected_auth", "producer", "no_credential", "local_auth"])
def test_protected_auth_stream_is_private_while_native_protocol_still_receives_it(launch):
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="explicit",
        executable_sha256="a" * 64,
        definition=NativeAgentDefinition(executable="/native/codex"),
        profile=NativeAgentProfile(
            backend="local" if launch == "local_auth" else "bubblewrap",
            sandbox_owner="harness" if launch == "local_auth" else "execution_backend",
            codex_login=CodexLoginConnection(
                home="/private/native-auth",
                model="selected-model",
                reasoning_effort="low",
                credential_store="file",
            ),
        ),
    )
    step = ResolvedLocalSearchAgentStep(
        step_id="native",
        argv=("python",),
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="native",
        harness="native",
        native_agent=selected,
    )
    store = GameTapeLiveOutputStore()
    store.open_run("run")
    protocol = []
    canary = "synthetic-file-credential-canary"

    def command(*argv, stream_output, **kwargs):
        stream_output("stdout", canary[:16])
        stream_output("stdout", canary[16:])
        stream_output("stderr", canary)
        return CommandResult(argv=argv, returncode=7, stdout=canary, stderr=canary)

    runner = observed_command_runner(
        command,
        step=step,
        attempt_number=1,
        observer=store.observer("run", "repo"),
        diagnostics=None,
        host_environment={},
        fleet_run_id="run",
        repository_id="repo",
    )
    argv = ("/native/python", "evidence") if launch == "producer" else ("/native/codex", "exec")
    result = runner(
        *argv,
        stream_output=lambda stream, text: protocol.append((stream, text)),
        **(
            {"credential_file": Path("/private/native-auth/auth.json")}
            if launch in ("protected_auth", "local_auth")
            else {}
        ),
    )
    assert result.returncode == 7
    assert "".join(text for stream, text in protocol if stream == "stdout") == canary
    assert "".join(text for stream, text in protocol if stream == "stderr") == canary
    retained = store.snapshot("run")
    if launch == "protected_auth":
        assert retained.chunks == ()
        assert canary not in retained.model_dump_json()
    else:
        assert canary in "".join(chunk.text for chunk in retained.chunks)


def test_protected_protocol_observer_exception_cannot_export_private_output():
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="explicit",
        executable_sha256="a" * 64,
        definition=NativeAgentDefinition(executable="/native/codex"),
        profile=NativeAgentProfile(
            backend="bubblewrap",
            sandbox_owner="execution_backend",
            codex_login=CodexLoginConnection(
                home="/private/native-auth",
                model="selected-model",
                reasoning_effort="low",
                credential_store="file",
            ),
        ),
    )
    step = ResolvedLocalSearchAgentStep(
        step_id="native",
        argv=("python",),
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="native",
        harness="native",
        native_agent=selected,
    )
    diagnostics = []

    class Diagnostics:
        def emit(self, event):
            diagnostics.append(event)

    def command(*argv, stream_output, **kwargs):
        stream_output("stdout", "synthetic-file-credential-canary")
        return CommandResult(argv=argv, returncode=0, stdout="", stderr="")

    def broken_protocol(stream, text):
        raise RuntimeError(text)

    runner = observed_command_runner(
        command,
        step=step,
        attempt_number=1,
        observer=lambda *args: pytest.fail("private native output was exposed"),
        diagnostics=Diagnostics(),
        host_environment={},
        fleet_run_id="run",
        repository_id="repo",
    )
    result = runner(
        "/native/codex",
        "exec",
        credential_file=Path("/private/native-auth/auth.json"),
        stream_output=broken_protocol,
    )
    assert result.returncode == 0
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "Protected native protocol observer failed"
    assert "synthetic-file-credential-canary" not in diagnostics[0].model_dump_json()
