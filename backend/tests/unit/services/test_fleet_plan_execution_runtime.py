from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import cast

import pytest

from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedFleetPlan,
)
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.engine.command import CommandResult
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_step_output_observation import (
    observed_command_runner,
    observed_step_diagnostic,
)
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


@pytest.mark.parametrize("launch", ["protected", "managed", "producer", "local", "service"])
def test_protected_stream_is_private_while_native_protocol_still_receives_it(launch):
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="explicit",
        executable_sha256="a" * 64,
        definition=NativeAgentDefinition(executable="/native/codex"),
        profile=NativeAgentProfile(
            backend="local" if launch in {"local", "service"} else "bubblewrap",
            sandbox_owner="harness" if launch in {"local", "service"} else "execution_backend",
            codex_service=CodexServiceConnection(
                home="/private/native", model="native", reasoning_effort="ultra"
            )
            if launch == "service"
            else None,
        ),
    )
    step = ResolvedAgentStep(
        timeout_seconds=3600,
        workload={"harness": "native", "native_agent": selected},
        step_id="native",
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="native",
    )
    store = GameTapeLiveOutputStore()
    store.open_run("run")
    protocol = []
    canary = "synthetic-private-protocol-canary"

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
    argv = (
        ("/native/python", "evidence")
        if launch == "producer"
        else ("/native/python", "-I", "-m", "oh_goon.integrations.agent_inference_relay")
        if launch == "managed"
        else ("/native/codex", "exec")
    )
    result = runner(
        *argv,
        stream_output=lambda stream, text: protocol.append((stream, text)),
        **({"broker_directory": Path("/private/run/broker")} if launch == "managed" else {}),
    )
    assert result.returncode == 7
    assert "".join(text for stream, text in protocol if stream == "stdout") == canary
    assert "".join(text for stream, text in protocol if stream == "stderr") == canary
    retained = store.snapshot("run")
    if launch in {"protected", "managed", "service"}:
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
        ),
    )
    step = ResolvedAgentStep(
        timeout_seconds=3600,
        workload={"harness": "native", "native_agent": selected},
        step_id="native",
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="native",
    )
    diagnostics = []

    class Diagnostics:
        def emit(self, event):
            diagnostics.append(event)

    def command(*argv, stream_output, **kwargs):
        stream_output("stdout", "synthetic-private-protocol-canary")
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
        stream_output=broken_protocol,
    )
    assert result.returncode == 0
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "Protected native protocol observer failed"
    assert "synthetic-private-protocol-canary" not in diagnostics[0].model_dump_json()


@pytest.mark.parametrize(
    "overlap",
    ["workspace-equal", "workspace-parent", "workspace-child", "executable", "runtime-root"],
)
def test_codex_service_direct_run_rejects_native_state_overlap_before_source_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overlap: str
) -> None:
    from oh_goon.application import ApplicationRuntime
    from oh_goon.integrations import agent_native_execution, fleet_plan_native_steps

    workspace = tmp_path / "teams" / "repo"
    workspace.mkdir(parents=True)
    native_home = tmp_path / "native-owned"
    native_home.mkdir()
    program = tmp_path / "public" / "codex"
    program.parent.mkdir()
    program.write_text("synthetic executable; must not be read")
    roots: tuple[str, ...] = ()
    if overlap == "workspace-equal":
        native_home = workspace
    elif overlap == "workspace-parent":
        native_home = workspace.parent
    elif overlap == "workspace-child":
        native_home = workspace / "private-native"
        native_home.mkdir()
    elif overlap == "executable":
        program = native_home / "codex"
        program.write_text("synthetic private file; must not be read")
    else:
        roots = (str(tmp_path),)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="native-service",
        definition=NativeAgentDefinition(executable=str(program), runtime_roots=roots),
        profile=NativeAgentProfile(
            codex_service=CodexServiceConnection(
                home=str(native_home), model="native", reasoning_effort="ultra"
            )
        ),
        executable_sha256="a" * 64,
    )
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        monkeypatch.setattr(
            agent_native_execution,
            "verify_native_agent_executable",
            lambda *args: pytest.fail("native file bytes were opened before separation"),
        )
        monkeypatch.setattr(
            fleet_plan_native_steps,
            "verify_native_agent_executable",
            lambda *args: pytest.fail("native file bytes were opened before separation"),
        )
        monkeypatch.setattr(
            runtime.plans._resolver,
            "resolve_frozen_agents",
            lambda *args, **kwargs: pytest.fail("native state reached source capture"),
        )
        with pytest.raises(AgentProviderError, match="authentication home"):
            runtime.plans.run_native_agent(
                selected=selected,
                workspace=workspace,
                skill=None,
                task="Native state must remain outside captured source",
                role="review",
            )
        assert runtime.runs.history().items == ()
    finally:
        runtime.shutdown(wait_for_runs=True)


@pytest.mark.skipif(os.name == "nt", reason="native POSIX executable fixture")
@pytest.mark.parametrize("read_only", [False, True], ids=["coding", "review"])
def test_native_plan_local_profile_honors_authored_workspace_access_and_reuses_source_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_only: bool
) -> None:
    import sys
    from unittest.mock import Mock

    from oh_goon.application import ApplicationRuntime
    from oh_goon.core.errors import FleetPlanInvalid
    from oh_goon.core.execution_lock_identity import build_execution_lock
    from oh_goon.core.models.agent_providers import CodexLoginConnection
    from oh_goon.core.models.fleet_plan_composition import PlanInputValue
    from oh_goon.core.models.operation import FleetPlanOperationEvidence
    from oh_goon.core.models.run import FleetRunState
    from oh_goon.core.product_identity import repository_id
    from oh_goon.integrations.agent_native_execution import native_executable_sha256
    from oh_goon.services.agent_authoring_sources import capture_agent_sources
    from oh_goon.services.execution_lock_persistence import write_execution_lock
    from tests.factories.shipped_templates import PLAN_TEMPLATES

    repository = tmp_path / "repositories" / "subject"
    repository.mkdir(parents=True)
    source = repository / "code.txt"
    source.write_text("broken\n")
    verifier = repository / "check"
    verifier.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        "assert Path('code.txt').read_text() == 'repaired\\n'\n"
        "Path('verification-completed').touch()\n"
    )
    verifier.chmod(0o755)
    assert (
        subprocess.run([str(verifier)], cwd=repository, capture_output=True, timeout=10).returncode
        == 1
    )
    _git(repository, "init", "-q")
    program = tmp_path / "native-tools" / "codex"
    program.parent.mkdir()
    program.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "sys.stdin.buffer.read()\n"
        "Path('code.txt').write_text('repaired\\n')\n"
        "Path('native-invoked').touch()\n"
        "print(json.dumps({'type': 'turn.started'}), flush=True)\n"
        "print(json.dumps({'type': 'item.completed', 'item': "
        "{'type': 'agent_message', 'text': 'Synthetic edit completed'}}), flush=True)\n"
        "print(json.dumps({'type': 'turn.completed'}), flush=True)\n"
    )
    program.chmod(0o755)
    account = tmp_path / "empty-native-account"
    account.mkdir()
    selected = ResolvedNativeAgent(
        agent="codex",
        profile_name="local-login",
        definition=NativeAgentDefinition(executable=str(program)),
        profile=NativeAgentProfile(
            backend="local",
            codex_login=CodexLoginConnection(
                home=str(account), model="synthetic-native", reasoning_effort="low"
            ),
        ),
        executable_sha256=native_executable_sha256(program),
    )
    workflow = tmp_path / "workflow"
    skill = workflow / "skills" / "task"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Fixture\nRepair code.txt. Read no auth or env files.\n")
    path = workflow / "plan.yml"
    path.write_text(
        PLAN_TEMPLATES.source(
            "agents/native-review" if read_only else "agents/native-coding"
        ).content
    )
    inputs: dict[str, PlanInputValue] = {
        "repo": "local:subject",
        "skill": "local:skills/task",
    }
    if not read_only:
        inputs["verification_command"] = "check"
    acceptance = ("agentic-review" if read_only else "native-coding",)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "app.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent)
        resolver = runtime.plans._resolver
        graph = resolver.analyze_source(path)
        monkeypatch.setattr(
            resolver,
            "analyze_source",
            lambda *args, **kwargs: pytest.fail("the captured source graph was rediscovered"),
        )
        capture = Mock(wraps=capture_agent_sources)
        monkeypatch.setattr(
            "oh_goon.services.agent_authoring_sources.capture_agent_sources", capture
        )

        def start_selected() -> FleetRunState:
            return runtime.plans.run(
                path,
                inputs=inputs,
                repository_ids={repository_id(repository)},
                repository=repository,
                agent_repository_root=repository.parent,
                agent_skill_root=workflow,
                allowed_agent_acceptance=acceptance,
                native_agent=selected,
                native_workspace=repository,
                source_graph=graph,
            )

        if read_only:
            with pytest.raises(FleetPlanInvalid, match="Codex login requires"):
                start_selected()
            capture.assert_not_called()
            assert runtime.runs.history().items == ()
            assert source.read_text() == "broken\n"
            assert not (repository / "native-invoked").exists()
            assert not (repository / "verification-completed").exists()
        else:
            plan, _, _ = resolver.resolve_frozen_agents(
                path,
                inputs=inputs,
                repository_root=repository.parent,
                skill_root=workflow,
                allowed_acceptance=acceptance,
                native_agent=selected,
                repository=repository,
                git_service=runtime.plans._git_service,
                source_graph=graph,
            )
            write_execution_lock(workflow / "goon.lock", build_execution_lock(plan))
            capture.reset_mock()
            started = start_selected()
            completed = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
            evidence = completed.operations[0].evidence
            assert isinstance(evidence, FleetPlanOperationEvidence)
            assert completed.game_tape.status.value == "PASSED"
            assert len(evidence.plan.steps) == 2
            assert all(step.status == "COMPLETED" for step in evidence.plan.steps)
            assert evidence.plan.steps[1].argv == ("./check",)
            assert source.read_text() == "repaired\n"
            assert (repository / "native-invoked").is_file()
            assert (repository / "verification-completed").is_file()
            capture.assert_called_once()
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_safe_control_diagnostic_retains_redacted_evidence_when_live_observer_fails(
    tmp_path: Path,
) -> None:
    import json

    from oh_goon.core.models.diagnostic import DiagnosticEvent
    from oh_goon.runtime.diagnostics import DiagnosticRouter, JsonlDiagnosticSink

    path = tmp_path / "passive" / "diagnostics.jsonl"
    step = ResolvedCommandStep(step_id="repair", argv=("native",))

    def broken_observer(*args: object) -> None:
        raise OSError("synthetic live-output unavailable")

    observer = observed_step_diagnostic(
        step=step,
        attempt_number=2,
        observer=broken_observer,
        diagnostics=DiagnosticRouter((JsonlDiagnosticSink(path),)),
        host_environment={},
        fleet_run_id="run-authority",
        repository_id="repo-authority",
    )
    observer("Native execution failed Bearer synthetic-secret /home/example/private/config")
    events = [
        DiagnosticEvent.model_validate(json.loads(line)) for line in path.read_text().splitlines()
    ]
    assert len(events) == 2
    assert events[0].operation == "native_failure_attempt_2"
    assert events[1].operation == "control_diagnostic"
    assert "synthetic-secret" not in path.read_text()
    assert "/home/example" not in path.read_text()
