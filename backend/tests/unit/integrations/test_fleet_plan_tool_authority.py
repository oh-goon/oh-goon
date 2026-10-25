from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import (
    SHELL_RUNTIME_SUPPORT_TOOLS,
    required_execution_tool_names,
    required_runtime_tool_names,
    step_execution_tool,
    step_runtime_tool_names,
)
from oh_goon.core.models.agent_providers import (
    NativeAgentDefinition,
    NativeAgentName,
    NativeAgentProfile,
    ResolvedNativeAgent,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedCommandStep,
    ResolvedDependencyInventoryStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    CompletedPlanStepTeardownEvidence,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionInputEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanSnapshotEvidence,
    PlanStepAttemptEvidence,
    PlanToolEvidence,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.fleet_plan_tool_authority import PlanHostToolAuthorityEvidence
from oh_goon.core.path_authority import StrictHostPathAuthority
from oh_goon.integrations._fleet_plan_host_resources import (
    capture_host_tools,
    preflight_run_capabilities,
)
from oh_goon.integrations._fleet_plan_runtime_root import capture_runtime_root


def _output() -> PlanOutputEvidence:
    return PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)


def _snapshot(char: str) -> PlanSnapshotEvidence:
    return PlanSnapshotEvidence(sha256=char * 64, byte_count=1, entry_count=1)


def _authority(*, sha256: str = "a" * 64) -> PlanHostToolAuthorityEvidence:
    return PlanHostToolAuthorityEvidence(name="uv", sha256=sha256, byte_count=123)


def _inputs(authority: PlanHostToolAuthorityEvidence) -> PlanExecutionInputEvidence:
    return PlanExecutionInputEvidence(
        source_authority_id="repository-source-authority-" + "0" * 64,
        repository_id="repo-" + "1" * 20,
        base_workspace=_snapshot("b"),
        runtime_root=_snapshot("c"),
        isolation_backend=PlanIsolationBackendEvidence(
            backend_id="test",
            configuration_id="cfg",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
        host_tools=(authority,),
    )


def _step(*, step_id: str = "quality", sha256: str = "a" * 64) -> CompletedPlanStepExecution:
    attempt = PlanStepAttemptEvidence(
        attempt=1,
        returncode=0,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none"),
        tool=PlanToolEvidence(name="uv", origin="host", sha256=sha256, byte_count=123),
    )
    return CompletedPlanStepExecution(
        step_id=step_id,
        kind="command",
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        attempts=(attempt,),
        teardown=CompletedPlanStepTeardownEvidence(attempt_roots_observed=1),
    )


def test_strict_result_accepts_exact_frozen_execution_environment_tool() -> None:
    result = FleetPlanResult(
        plan_id="plan", steps=(_step(),), execution_inputs=_inputs(_authority())
    )

    authority = result.execution_inputs.host_tools[0] if result.execution_inputs else None
    assert authority is not None
    assert authority.authority_id.startswith("plan-host-tool-authority-")


def test_strict_result_does_not_scope_tool_authority_to_step_consumers() -> None:
    result = FleetPlanResult(
        plan_id="plan",
        steps=(_step(step_id="publish"),),
        execution_inputs=_inputs(_authority()),
    )
    assert result.steps[0].step_id == "publish"


def test_strict_result_rejects_tool_bytes_that_differ_from_frozen_environment() -> None:
    with pytest.raises(ValidationError, match="does not match the frozen run tool authority"):
        FleetPlanResult(
            plan_id="plan",
            steps=(_step(sha256="d" * 64),),
            execution_inputs=_inputs(_authority()),
        )


def test_required_tools_include_primary_and_explicit_child_tool_capabilities() -> None:
    step = ResolvedCommandStep(
        step_id="quality",
        argv=("uv", "run", "pytest"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("bash", "git")),
    )
    plan = SimpleNamespace(steps=(step,))

    assert required_execution_tool_names(plan) == ("bash", "git", "uv")  # type: ignore[arg-type]


def test_explicit_shell_gets_fixed_frozen_runtime_support_without_broadening_authority() -> None:
    step = ResolvedCommandStep(
        step_id="tooling",
        argv=("make", "test-tooling-complete"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("bash", "sh")),
    )
    plan = SimpleNamespace(steps=(step,))

    assert required_execution_tool_names(plan) == ("bash", "make", "sh")  # type: ignore[arg-type]
    runtime = required_runtime_tool_names(plan)  # type: ignore[arg-type]
    assert set(SHELL_RUNTIME_SUPPORT_TOOLS) <= set(runtime)
    assert {"curl", "git", "npm", "uv"}.isdisjoint(set(runtime) - {"bash", "make", "sh"})
    assert step_runtime_tool_names(step) == runtime


def test_non_shell_step_does_not_gain_shell_runtime_support() -> None:
    step = ResolvedCommandStep(
        step_id="quality",
        argv=("uv", "run", "pytest"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("git",)),
    )
    plan = SimpleNamespace(steps=(step,))

    assert required_runtime_tool_names(plan) == ("git", "uv")  # type: ignore[arg-type]


def test_in_process_dependency_inventory_requires_no_environment_tool() -> None:
    step = ResolvedDependencyInventoryStep(step_id="dependency-inventory")
    plan = SimpleNamespace(steps=(step,))

    assert required_execution_tool_names(plan) == ()  # type: ignore[arg-type]
    assert required_runtime_tool_names(plan) == ()  # type: ignore[arg-type]


def _native_step(agent: NativeAgentName) -> ResolvedAgentStep:
    return ResolvedAgentStep(
        workload={
            "harness": "native",
            "native_agent": ResolvedNativeAgent(
                agent=agent,
                profile_name="selected",
                definition=NativeAgentDefinition(executable=f"/selected/{agent}-custom"),
                profile=NativeAgentProfile(),
                executable_sha256="a" * 64,
            ),
        },
        step_id="native-agent",
        timeout_seconds=30,
        delivery_sha256="b" * 64,
        handoff_identity="handoff",
        source_step_id="native-agent",
    )


@pytest.mark.parametrize("agent", ("codex", "opencode", "pi"))
def test_native_executable_selection_is_separate_from_host_support(agent: NativeAgentName) -> None:
    step = _native_step(agent)
    plan = SimpleNamespace(steps=(step,))
    seen: list[str] = []

    class Authority:
        def tool(self, name: str) -> object:
            seen.append(name)
            assert name == "python"
            return object()

    assert step_execution_tool(step) == f"/selected/{agent}-custom"
    assert required_execution_tool_names(plan) == ("python",)  # type: ignore[arg-type]
    assert required_runtime_tool_names(plan) == ("python",)  # type: ignore[arg-type]
    preflight_run_capabilities(plan, Authority())  # type: ignore[arg-type]
    assert seen == ["python"]


@pytest.mark.parametrize("agent", ("codex", "opencode", "pi"))
@pytest.mark.parametrize("stage", ("native", "relay", "producer"))
def test_native_evidence_retains_actual_invocation_and_launcher_bytes(
    agent: NativeAgentName,
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from oh_goon.engine.command_runtime_authority import RuntimeAuthorityReceipt
    from oh_goon.engine.git import GitService
    from oh_goon.integrations import fleet_plan_attempt_evidence as attempt_evidence_module
    from oh_goon.integrations._fleet_plan_step_evidence import step_evidence
    from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
    from oh_goon.integrations.fleet_plan_attempt_evidence import build_direct_attempt_evidence

    step = _native_step(agent)
    selected = f"/selected/{agent}-custom"
    launcher = selected if stage == "native" else "/launch/python3.13"
    argv = (
        (selected, "run", "--configured")
        if stage == "native"
        else (
            launcher,
            "-m",
            "oh_goon.integrations.agent_inference_relay",
            "--",
            selected,
            "run",
        )
        if stage == "relay"
        else (launcher, "-m", "oh_goon.integrations.hashmarks.agent_tools", "task_evidence")
    )
    runtime = RuntimeAuthorityReceipt(
        executable_path=launcher,
        executable_identity="sha256:" + "c" * 64,
        executable_byte_count=123,
        path_authority_identity="sha256:" + "1" * 64,
        environment_identity="sha256:" + "2" * 64,
    )
    outcome = StepOutcome(argv=argv, returncode=17, runtime_authority=runtime)
    repository = tmp_path / "repo"
    repository.mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "later-shadow"))

    def forbidden_path_lookup(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("native evidence must use the launch receipt, not read selected paths")

    monkeypatch.setattr(attempt_evidence_module, "_resolve_executable_path", forbidden_path_lookup)
    observation = GitService().observe(repository)
    attempt = build_direct_attempt_evidence(
        step, step, 1, outcome, repository, observation, observation, retry=None
    )
    execution = step_evidence(
        step=step,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
        attempts=(attempt,),
        artifacts=(),
    )
    retained = FleetPlanResult.model_validate_json(
        FleetPlanResult(plan_id="native-plan", steps=(execution,)).model_dump_json(
            exclude_computed_fields=True
        )
    )

    assert retained.steps[0].argv == argv
    assert retained.steps[0].attempts[0].argv == argv
    tool = retained.steps[0].attempts[0].tool
    assert tool is not None
    assert tool.name == Path(launcher).name
    assert tool.sha256 == "c" * 64 and tool.byte_count == 123
    assert step.workload.harness == "native"
    assert tool.sha256 != step.workload.native_agent.executable_sha256


@pytest.mark.parametrize(
    "argv",
    (
        (),
        ("/launch/python3.13", "-m", "oh_goon.integrations.hashmarks.agent_tools"),
        ("/launch/bwrap", "--bind", "/runtime", "/runtime", "/launch/python3.13", "-m", "relay"),
    ),
)
def test_native_output_limit_retains_only_the_invocation_captured_by_the_process_owner(
    argv: tuple[str, ...],
) -> None:
    from oh_goon.engine.command import CommandOutputLimitExceeded, CommandSupervisorReceipt
    from oh_goon.integrations.fleet_plan_output_limit import bounded_execution_failure_outcome

    error = CommandOutputLimitExceeded(
        argv=argv,
        stream="stdout",
        limit_bytes=4096,
        stdout="bounded",
        stderr="",
        supervisor=CommandSupervisorReceipt(),
    )
    outcome = bounded_execution_failure_outcome(_native_step("pi"), error)

    assert outcome.argv == argv
    assert outcome.returncode != 0


def test_enforcer_checks_required_tool_availability_from_execution_environment() -> None:
    step = ResolvedCommandStep(
        step_id="quality",
        argv=("uv", "run", "pytest"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("git",)),
    )
    plan = SimpleNamespace(steps=(step,))
    seen: list[str] = []

    class Authority:
        def tool(self, name: str) -> object:
            seen.append(name)
            return object()

    preflight_run_capabilities(plan, Authority())  # type: ignore[arg-type]
    assert seen == ["git", "uv"]


def test_enforcer_rejects_missing_required_execution_environment_tool() -> None:
    step = ResolvedCommandStep(step_id="quality", argv=("uv", "run", "pytest"), timeout_seconds=60)
    plan = SimpleNamespace(steps=(step,))

    class Authority:
        def tool(self, name: str) -> object:
            raise FleetPlanInvalid(f"missing {name}")

    with pytest.raises(FleetPlanInvalid, match="missing required tool.*uv"):
        preflight_run_capabilities(plan, Authority())  # type: ignore[arg-type]


def test_toolchain_identity_depends_on_tool_bytes_not_step_layout() -> None:
    first = _inputs(_authority(sha256="a" * 64))
    second = _inputs(_authority(sha256="d" * 64))

    assert first.host_toolchain_id != second.host_toolchain_id


def test_frozen_tool_authority_survives_durable_result_round_trip() -> None:
    result = FleetPlanResult(
        plan_id="plan", steps=(_step(),), execution_inputs=_inputs(_authority())
    )

    restored = FleetPlanResult.model_validate_json(
        result.model_dump_json(exclude_computed_fields=True)
    )

    assert restored.execution_inputs is not None
    assert restored.execution_inputs.host_toolchain_id == result.execution_inputs.host_toolchain_id
    assert (
        restored.execution_inputs.host_tools[0].authority_id
        == result.execution_inputs.host_tools[0].authority_id
    )


def _executable(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755)
    return path


def test_preflight_accepts_nvm_style_npm_and_npx_symlinks_from_admitted_bin(
    tmp_path: Path,
) -> None:
    node_root = tmp_path / "node-v24"
    tools = node_root / "bin"
    package = node_root / "lib" / "node_modules" / "npm"
    package_bin = package / "bin"
    tools.mkdir(parents=True)
    package_bin.mkdir(parents=True)
    (package / "package.json").write_text('{"name":"npm"}\n', encoding="utf-8")
    inspector = _executable(tools / "ldd", b"inspector")
    _executable(tools / "node", b"node-binary")
    npm = _executable(package_bin / "npm-cli.js", b"#!/usr/bin/env node\n")
    npx = _executable(package_bin / "npx-cli.js", b"#!/usr/bin/env node\n")
    (tools / "npm").symlink_to(npm)
    (tools / "npx").symlink_to(npx)
    authority = StrictHostPathAuthority.create(
        trusted_roots=(tools,),
        tool_search_paths=(tools,),
        runtime_inspector=inspector,
    )
    step = ResolvedCommandStep(
        step_id="browser",
        argv=("node", "script.mjs"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("npm", "npx")),
    )

    plan = SimpleNamespace(steps=(step,))
    preflight_run_capabilities(plan, authority)  # type: ignore[arg-type]
    bindings = capture_host_tools(
        plan,
        tmp_path / "frozen-tools",
        authority,
        cancel_requested=None,  # type: ignore[arg-type]
    )

    assert authority.tool("npm") == npm.resolve()
    assert authority.tool("npx") == npx.resolve()
    assert bindings["npm"].runtime_support is not None
    assert bindings["npx"].runtime_support is not None
    assert (
        bindings["npm"].runtime_support.snapshot_path
        == bindings["npx"].runtime_support.snapshot_path
    )


def test_node_cli_host_tool_freezes_package_support_and_uses_identity_separate_launcher(
    tmp_path: Path,
) -> None:
    package = tmp_path / "node_modules" / "npm"
    (package / "package.json").parent.mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text('{"name":"npm"}\n', encoding="utf-8")
    entry = _executable(
        package / "bin" / "npm-cli.js",
        b"#!/usr/bin/env node\nrequire('../lib/cli.js')\n",
    )
    (package / "lib").mkdir()
    (package / "lib" / "cli.js").write_text("module.exports = () => 0\n", encoding="utf-8")
    node = _executable(tmp_path / "bin" / "node", b"node-binary")
    step = ResolvedCommandStep(
        step_id="frontend",
        argv=("node", "script.mjs"),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("npm",)),
    )
    plan = SimpleNamespace(steps=(step,))

    class Authority:
        def tool(self, name: str) -> Path:
            return {"node": node, "npm": entry}[name]

    bindings = capture_host_tools(
        plan,
        tmp_path / "frozen-tools",
        Authority(),
        cancel_requested=None,  # type: ignore[arg-type]
    )
    npm = bindings["npm"]

    assert npm.identity.sha256 != npm.execution_identity.sha256
    assert npm.runtime_support is not None
    assert (npm.runtime_support.snapshot_path / "lib" / "cli.js").is_file()
    assert npm.runtime_support.entry_relative.as_posix() == "bin/npm-cli.js"
    assert npm.execution_snapshot_path.read_text(encoding="utf-8") == (
        '#!/toolchain/bin/node\nrequire("/toolchain/support/000/bin/npm-cli.js")\n'
    )

    runtime = capture_runtime_root(
        bindings.values(),
        {},
        tmp_path / "runtime-root",
        runtime_inspector=_executable(tmp_path / "ldd", b"inspector"),
        network_enabled=False,
        cancel_requested=None,
    )
    assert runtime.root.joinpath("toolchain/support/000/lib/cli.js").is_file()


def test_node_cli_host_tool_requires_explicit_node_capability(tmp_path: Path) -> None:
    package = tmp_path / "node_modules" / "npm"
    (package / "package.json").parent.mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text('{"name":"npm"}\n', encoding="utf-8")
    entry = _executable(package / "bin" / "npm-cli.js", b"#!/usr/bin/env node\n")
    primary = _executable(tmp_path / "bin" / "true", b"primary")
    step = ResolvedCommandStep(
        step_id="frontend",
        argv=("true",),
        timeout_seconds=60,
        capabilities=PlanStepCapabilities(tools=("npm",)),
    )
    plan = SimpleNamespace(steps=(step,))

    class Authority:
        def tool(self, name: str) -> Path:
            return {"true": primary, "npm": entry}[name]

    with pytest.raises(FleetPlanInvalid, match="requires explicit child tool capability 'node'"):
        capture_host_tools(
            plan,
            tmp_path / "frozen-tools",
            Authority(),
            cancel_requested=None,  # type: ignore[arg-type]
        )


def test_direct_attempt_tool_evidence_uses_bound_runtime_receipt_not_later_path_lookup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib

    from oh_goon.engine.command_runtime_authority import RuntimeAuthorityReceipt
    from oh_goon.engine.git import GitService
    from oh_goon.integrations import fleet_plan_attempt_evidence as attempt_evidence_module
    from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
    from oh_goon.integrations.fleet_plan_attempt_evidence import build_direct_attempt_evidence

    repository = tmp_path / "repo"
    repository.mkdir()
    launched = tmp_path / "launch-bin" / "authority-tool"
    launched.parent.mkdir()
    launched_bytes = b"exact-launched-tool-bytes"
    launched.write_bytes(launched_bytes)
    launched.chmod(0o755)

    def forbidden_path_lookup(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("direct attempt evidence must not resolve executable bytes again")

    monkeypatch.setattr(
        attempt_evidence_module,
        "_resolve_executable_path",
        forbidden_path_lookup,
    )

    step = ResolvedCommandStep(
        timeout_seconds=30,
        step_id="authority-evidence",
        argv=("authority-tool",),
    )
    runtime = RuntimeAuthorityReceipt(
        executable_path=str(launched.resolve()),
        executable_identity="sha256:" + hashlib.sha256(launched_bytes).hexdigest(),
        executable_byte_count=len(launched_bytes),
        path_authority_identity="sha256:" + "1" * 64,
        environment_identity="sha256:" + "2" * 64,
    )
    outcome = StepOutcome(
        argv=step.argv,
        returncode=0,
        runtime_authority=runtime,
    )
    observation = GitService().observe(repository)

    evidence = build_direct_attempt_evidence(
        step,
        step,
        1,
        outcome,
        repository,
        observation,
        observation,
        retry=None,
    )

    assert evidence.tool is not None
    assert evidence.tool.name == "authority-tool"
    assert evidence.tool.origin == "host"
    assert evidence.tool.sha256 == hashlib.sha256(launched_bytes).hexdigest()
    assert evidence.tool.byte_count == len(launched_bytes)


@pytest.mark.parametrize("mutation", ("replace", "delete_and_shadow"))
def test_direct_attempt_tool_evidence_survives_post_launch_executable_mutation(
    mutation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import hashlib

    from oh_goon.engine.command_runtime_authority import RuntimeAuthorityReceipt
    from oh_goon.engine.git import GitService
    from oh_goon.integrations import fleet_plan_attempt_evidence as attempt_evidence_module
    from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
    from oh_goon.integrations.fleet_plan_attempt_evidence import build_direct_attempt_evidence

    repository = tmp_path / "repo"
    repository.mkdir()
    launched = tmp_path / "launch-bin" / "authority-tool"
    launched.parent.mkdir()
    launched_bytes = b"launch-time-authority"
    launched.write_bytes(launched_bytes)
    launched.chmod(0o755)
    launch_sha = hashlib.sha256(launched_bytes).hexdigest()

    step = ResolvedCommandStep(
        timeout_seconds=30,
        step_id="authority-mutation",
        argv=("authority-tool",),
    )
    runtime = RuntimeAuthorityReceipt(
        executable_path=str(launched.resolve()),
        executable_identity="sha256:" + launch_sha,
        executable_byte_count=len(launched_bytes),
        path_authority_identity="sha256:" + "1" * 64,
        environment_identity="sha256:" + "2" * 64,
    )
    outcome = StepOutcome(argv=step.argv, returncode=0, runtime_authority=runtime)

    rogue = tmp_path / "rogue-bin" / "authority-tool"
    rogue.parent.mkdir()
    rogue.write_bytes(b"different-post-launch-tool")
    rogue.chmod(0o755)
    monkeypatch.setenv("PATH", str(rogue.parent))
    if mutation == "replace":
        launched.write_bytes(b"replaced-after-launch")
    else:
        launched.unlink()

    def forbidden_path_lookup(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError(
            "attempt evidence must report the launch receipt without re-resolution"
        )

    monkeypatch.setattr(attempt_evidence_module, "_resolve_executable_path", forbidden_path_lookup)
    observation = GitService().observe(repository)
    evidence = build_direct_attempt_evidence(
        step,
        step,
        1,
        outcome,
        repository,
        observation,
        observation,
        retry=None,
    )

    assert evidence.tool is not None
    assert evidence.tool.name == "authority-tool"
    assert evidence.tool.origin == "host"
    assert evidence.tool.sha256 == launch_sha
    assert evidence.tool.byte_count == len(launched_bytes)
