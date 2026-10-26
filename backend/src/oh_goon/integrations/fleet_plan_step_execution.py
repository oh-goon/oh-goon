from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol, TypedDict, Unpack

from oh_goon.core.agent_model_selection import (
    LocalModelReviewDecision,
    LocalModelToolAction,
    LocalModelToolDecision,
    append_untrusted_agent_feedback,
    frozen_local_model_observation_prompt,
    frozen_local_model_prompt,
    frozen_local_model_tool_observation_prompt,
    frozen_local_model_tool_prompt,
    parse_local_model_review_decision,
    parse_local_model_tool_action,
    parse_local_model_tool_review_decision,
    parse_one_local_model_query,
    prepend_repository_evidence,
)
from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.core.errors import FleetPlanInvalid, OperationDeadlineExpired
from oh_goon.core.models.agent_providers import ResolvedNativeAgent
from oh_goon.core.models.agent_repair import AgentRepairContext
from oh_goon.core.models.agent_source import FrozenAgentStepHandoff
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_observation import AgentHarnessObservation
from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    ResolvedAgentStep,
    ResolvedBuiltinAgentWorkload,
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedGitStep,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
    ResolvedOciBuildStep,
    ResolvedOciPublishStep,
    ResolvedOciTagAssertStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
    ResolvedProviderAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits, PlanExecutionPolicy
from oh_goon.core.models.fleet_plan_observation_steps import ResolvedDependencyInventoryStep
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.path_authority import (
    RepositoryPathSpace,
    canonical_path,
)
from oh_goon.core.path_layouts import AttemptPathLayout
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.core.time import utc_now
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandOutputLimitExceeded,
    CommandOutputObserver,
    CommandProcessObserver,
    CommandResult,
    CommandTerminal,
    run_command,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_step_local_execution import (
    execute_extension,
    execute_git,
    execute_pre_commit,
    step_working_directory,
)
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    executable_not_found_outcome,
    executable_not_runnable_outcome,
    from_command,
    process_boundary_settled,
)
from oh_goon.integrations.content_snapshot import SnapshotIdentity, copy_frozen_directory
from oh_goon.integrations.executable_materialization_authority import (
    verify_step_executable_materialization,
)
from oh_goon.integrations.fleet_plan_dependency_observation import execute_dependency_inventory
from oh_goon.integrations.fleet_plan_environment import (
    direct_plan_environment,
    strict_plan_environment,
)
from oh_goon.integrations.fleet_plan_kubernetes_step_execution import execute_kubernetes_step
from oh_goon.integrations.fleet_plan_managed_runtime import (
    ExtensionManagedRuntime,
    ManagedRuntimeSpec,
    PreCommitManagedRuntime,
    UvProjectManagedRuntime,
    uv_preparation_requires_retained_scratch,
)
from oh_goon.integrations.fleet_plan_oci_execution import execute_oci_build
from oh_goon.integrations.fleet_plan_oci_publish_execution import execute_oci_publish
from oh_goon.integrations.fleet_plan_oci_tag_execution import execute_oci_tag_assert
from oh_goon.integrations.fleet_plan_output_limit import bounded_execution_failure_outcome
from oh_goon.integrations.fleet_plan_process_isolation import (
    MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
    IsolatedProcessRequest,
    ProcessIsolationBackend,
    StandardReadOnlyMount,
    TrustedLocalRunScope,
    native_profile_command_runner,
)
from oh_goon.integrations.fleet_plan_run import FleetPlanStepAttempt
from oh_goon.integrations.fleet_plan_secret_redaction import redact_host_secret_text
from oh_goon.integrations.fleet_plan_step_lifecycle_observation import lifecycle_command_runner
from oh_goon.integrations.fleet_plan_step_output_observation import (
    StepDiagnosticObserver,
    observed_command_runner,
    observed_native_visible_claim,
    observed_step_diagnostic,
)
from oh_goon.integrations.hashmarks.agent_tools import (
    PreparedAgentEvidence,
    prepare_agent_evidence,
    prompt_observation,
    task_evidence_prompt,
)


class CommandRunner(Protocol):
    def __call__(
        self,
        *args: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
        terminal: CommandTerminal | None = None,
        input_data: bytes | None = None,
        retain_output_tail: bool = False,
        read_only_mounts: tuple[StandardReadOnlyMount, ...] = (),
        broker_directory: Path | None = None,
        read_only_workspace: bool = False,
        allowed_write_roots: tuple[Path, ...] = (),
        host_network: bool = False,
        private_configuration_directory: Path | None = None,
        preserve_native_executable_path: bool = True,
    ) -> CommandResult: ...


class _CommandRunOptions(TypedDict, total=False):
    cwd: Path | None
    env: Mapping[str, str] | None
    cancel_requested: Callable[[], bool] | None
    stream_output: CommandOutputObserver | None
    process_observer: CommandProcessObserver | None
    runtime_supervision: CommandRuntimeSupervision | None
    terminal: CommandTerminal | None
    input_data: bytes | None
    retain_output_tail: bool
    read_only_mounts: tuple[StandardReadOnlyMount, ...]
    broker_directory: Path | None
    read_only_workspace: bool
    allowed_write_roots: tuple[Path, ...]
    host_network: bool
    private_configuration_directory: Path | None
    preserve_native_executable_path: bool


class _IsolatedExecuteOptions(TypedDict, total=False):
    timeout: float | None
    cancel_requested: Callable[[], bool] | None
    stream_output: CommandOutputObserver
    process_observer: CommandProcessObserver | None
    runtime_supervision: CommandRuntimeSupervision


class _ObservationFields(TypedDict):
    task: str
    skill_bytes: bytes
    recipient: str
    returncode: int
    stdout: str
    stderr: str
    can_search: bool


class StepLifecycleObserver(Protocol):
    def __call__(
        self,
        step_id: str,
        step_order: int,
        event_type: str,
        *,
        attempt_number: int | None = None,
        physical_execution_id: str | None = None,
        pid: int | None = None,
        process_identity: str | None = None,
        returncode: int | None = None,
        termination_reason: str | None = None,
        output_count: int | None = None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class StepRuntime:
    git: GitService
    extensions: FleetExtensionStore | None
    tools: FleetToolStore | None
    cancel_requested: Callable[[], bool] | None
    termination_requested: Callable[[], TerminationSignal | None] | None = None
    diagnostics: DiagnosticEmitter | None = None
    fleet_run_id: str | None = None
    repository_id: str | None = None
    lifecycle_observer: StepLifecycleObserver | None = None
    runtime_supervision: CommandRuntimeSupervision | None = None
    local_resource_intent_for: (
        Callable[[ResolvedPlanStep, FleetPlanStepAttempt, int], LocalLaceIntentReservation] | None
    ) = None
    # Original immutable admitted Run facts; NOT independent effect authority.
    trusted_local_consent: TrustedLocalRunConsent | None = None
    trusted_local_scope: TrustedLocalRunScope | None = None
    # Original admitted attempt supplies the AG-02 packet once. This callback
    # only transports bytes; it is not an authorization or a second runner.
    agent_delivery_for: Callable[[str, str], bytes] | None = None
    execution_limits: FleetPlanRunLimits | None = None
    repair_context: AgentRepairContext | None = None
    terminal: CommandTerminal | None = None
    standard_process_isolation: ProcessIsolationBackend | None = None
    execution_policy: PlanExecutionPolicy | None = None
    execution_identity: ExecutionIdentity | None = None
    workspace_pin: Callable[[Path], AbstractContextManager[Callable[[], None]]] | None = None
    native_profile: ResolvedNativeAgent | None = None
    project_sources: Mapping[str, Path] = field(default_factory=dict)
    project_workspaces: Mapping[str, Path] = field(default_factory=dict)
    on_unsettled_run_process: Callable[[], None] | None = None


@dataclass(frozen=True, slots=True)
class _StepProcess:
    paths: AttemptPathLayout
    environment: dict[str, str]
    host_environment: Mapping[str, str]
    command_runner: CommandRunner
    git_command_runner: CommandRunner
    managed_runtime: ManagedRuntimeSpec | None
    package_project: Path | None = None
    # Safe controller diagnostics only; native protocol/source bytes stay private.
    diagnostic_output: StepDiagnosticObserver | None = None
    redact_visible_output: Callable[[str], str] | None = None
    visible_output: Callable[[AgentHarnessObservation], None] | None = None


def execute_direct_step(
    repository: Path,
    step: ResolvedPlanStep,
    runtime: StepRuntime,
    *,
    attempt_number: int = 1,
    step_order: int,
    output_observer: StepOutputObserver | None = None,
) -> StepOutcome:
    if runtime.cancel_requested is not None and runtime.cancel_requested():
        raise OperationCancelledError("Fleet Step cancelled before preparation")
    physical_runner = (
        native_profile_command_runner(
            repository,
            runtime.native_profile,
            runtime.standard_process_isolation,
            runtime.workspace_pin,
            termination_requested=runtime.termination_requested,
            repair_context=runtime.repair_context,
        )
        if runtime.native_profile is not None
        else _direct_step_command_runner
    )
    runner = physical_runner
    if runtime.execution_limits is not None:
        limits = runtime.execution_limits
        remaining = (limits.deadline_at - utc_now()).total_seconds()
        if remaining <= 0:
            raise OperationDeadlineExpired("Fleet deadline expired before Step preparation")
        deadline = time.monotonic() + min(remaining, limits.step_timeout_seconds)

        def bounded_runner(
            *args: str, timeout: float | None = 10.0, **kwargs: Unpack[_CommandRunOptions]
        ) -> CommandResult:
            cancelled = kwargs.get("cancel_requested")
            if cancelled is not None and cancelled():
                raise OperationCancelledError("Fleet Step cancelled before native launch")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationDeadlineExpired("Agent repair deadline expired before native launch")
            if kwargs.get("runtime_supervision") is not None:
                raise FleetPlanInvalid("repair deadline cannot replace an admitted native watchdog")
            selected_timeout = remaining if timeout is None else min(timeout, remaining)
            return physical_runner(*args, timeout=selected_timeout, **kwargs)

        runner = bounded_runner
    package = isinstance(step, ResolvedCommandStep) and step.project_source is not None
    temporary = tempfile.TemporaryDirectory(
        prefix="oh-goon-step-", dir=repository.parent if package else None, delete=not package
    )
    with temporary as private_root:
        paths = AttemptPathLayout.from_root(Path(private_root))
        for path in (paths.home, paths.tmp, paths.cache, paths.config):
            path.mkdir()
        environment = direct_plan_environment(
            step.environment,
            home=paths.home,
            tmp=paths.tmp,
            cache=paths.cache,
            config=paths.config,
            host_environment=os.environ,
        )
        process = _StepProcess(
            paths=paths,
            environment=environment,
            host_environment=os.environ,
            command_runner=runner,
            git_command_runner=runner,
            managed_runtime=None,
        )
        retain_package_scratch = False
        try:
            outcome = None
            if isinstance(step, ResolvedCommandStep) and step.project_source is not None:
                process, outcome = _prepare_python_project_attempt(
                    repository, step, process, runtime
                )
            if outcome is None:
                process = _with_lifecycle_observer(
                    process, step, step_order, attempt_number, runtime.lifecycle_observer
                )
                outcome = _execute_typed_step(
                    repository,
                    step,
                    _with_output_observer(
                        process,
                        step,
                        attempt_number,
                        output_observer,
                        runtime.diagnostics,
                        runtime.fleet_run_id,
                        runtime.repository_id,
                    ),
                    runtime,
                )
            if process_boundary_settled(outcome) is not True:
                if runtime.on_unsettled_run_process is not None:
                    runtime.on_unsettled_run_process()
                if package:
                    retain_package_scratch = True
                    outcome = replace(
                        outcome,
                        stderr=outcome.stderr.rstrip()
                        + f"\nPython Goon Step scratch retained while native process cleanup is incomplete: {private_root}",
                    )
            return outcome
        except BaseException as error:
            if uv_preparation_requires_retained_scratch(error):
                if runtime.on_unsettled_run_process is not None:
                    runtime.on_unsettled_run_process()
                if package:
                    retain_package_scratch = True
                    error.add_note(
                        f"Python Goon Step scratch retained while native process cleanup is incomplete: {private_root}"
                    )
            raise
        finally:
            if package and not retain_package_scratch:
                temporary.cleanup()


def execute_strict_step(
    repository: Path,
    step: ResolvedPlanStep,
    attempt: FleetPlanStepAttempt,
    process_isolation: ProcessIsolationBackend,
    runtime: StepRuntime,
    *,
    attempt_number: int = 1,
    step_order: int,
    output_observer: StepOutputObserver | None = None,
) -> StepOutcome:
    if isinstance(step, ResolvedAgentStep):
        # This Step-level cut is not the optional untrusted Agent sandbox.
        # Do not reinterpret trusted local test evidence as strict admission.
        raise FleetPlanInvalid("Agent strict execution backend is not admitted")
    _require_strict_managed_runtime(step, attempt.managed_runtime)
    environment = strict_plan_environment(
        step.environment,
        home=attempt.paths.home,
        tmp=attempt.paths.tmp,
        cache=attempt.paths.cache,
        config=attempt.paths.config,
        host_environment=attempt.host_environment,
    )
    command_runner = _isolated_command_runner(
        attempt,
        step,
        process_isolation,
        termination_requested=runtime.termination_requested,
        fleet_run_id=runtime.fleet_run_id,
        diagnostics=runtime.diagnostics,
        local_resource_intent_for=runtime.local_resource_intent_for,
        trusted_local_consent=runtime.trusted_local_consent,
        trusted_local_scope=runtime.trusted_local_scope,
    )
    process = _StepProcess(
        paths=attempt.paths,
        environment=environment,
        host_environment=attempt.host_environment,
        command_runner=command_runner,
        # One Step/attempt owns a single monotonically increasing process ordinal.
        # Tool/Git processes must not independently reuse ordinal 1.
        git_command_runner=command_runner,
        managed_runtime=attempt.managed_runtime,
    )
    if isinstance(step, ResolvedCommandStep):
        _require_explicit_script_interpreter(repository, step)
        process, preparation_failure = _prepare_strict_uv_attempt(
            repository,
            step,
            process,
            runtime,
        )
        if preparation_failure is not None:
            return preparation_failure
    process = _with_lifecycle_observer(
        process,
        step,
        step_order,
        attempt_number,
        runtime.lifecycle_observer,
    )
    return _execute_typed_step(
        repository,
        step,
        _with_output_observer(
            process,
            step,
            attempt_number,
            output_observer,
            runtime.diagnostics,
            runtime.fleet_run_id,
            runtime.repository_id,
        ),
        runtime,
    )


def _execute_typed_step(
    repository: Path,
    step: ResolvedPlanStep,
    process: _StepProcess,
    runtime: StepRuntime,
) -> StepOutcome:
    try:
        if isinstance(step, ResolvedAgentStep):
            native_deadline = None
            if step.workload.harness == "native":
                assert step.workload.harness == "native"
                assert step.timeout_seconds is not None
                native_deadline = time.monotonic() + min(
                    step.timeout_seconds, step.workload.native_agent.profile.timeout_seconds
                )
            if step.step_id != step.source_step_id and not step.step_id.endswith(
                "." + step.source_step_id
            ):
                raise FleetPlanInvalid("resolved Agent Step identity diverges from frozen owner")
            # Consume the exact AG-02 bytes under this original Step attempt.
            # Reconstruct the fixed command from verified handoff bytes, never
            # from the caller-provided argv or a live repository checkout.
            if runtime.agent_delivery_for is None:
                raise FleetPlanInvalid("original Agent Step attempt lacks immutable delivery")
            packet = runtime.agent_delivery_for(step.step_id, step.handoff_identity)
            if (
                type(packet) is not bytes
                or hashlib.sha256(packet).hexdigest() != step.delivery_sha256
            ):
                raise FleetPlanInvalid("Agent Step delivery does not match resolved source digest")
            native_input = None
            if step.workload.harness == "native":
                from oh_goon.integrations.fleet_plan_native_steps import prepare_native_step_input

                native_input = prepare_native_step_input(packet, step, runtime)
                handoff = native_input.handoff
            else:
                handoff = FrozenAgentStepHandoff.decode_prework_delivery(
                    packet,
                    expected_handoff_identity=step.handoff_identity,
                )
            if handoff.step_id != step.source_step_id or handoff.harness != step.workload.harness:
                raise FleetPlanInvalid("Agent Step meaning differs from admitted frozen delivery")
            selected_model = (
                step.workload.model
                if isinstance(
                    step.workload, (ResolvedBuiltinAgentWorkload, ResolvedProviderAgentWorkload)
                )
                else None
            )
            if handoff.requested_effects.model != selected_model:
                raise FleetPlanInvalid(
                    "Agent model differs from admitted frozen recipient selection"
                )
            if handoff.timeout_seconds != step.timeout_seconds:
                raise FleetPlanInvalid("Agent Step timeout differs from frozen delivery")
            if step.feedback_reference is not None and step.feedback_value is None:
                raise FleetPlanInvalid(
                    "Agent feedback was not bound by the original Fleet sequencer"
                )
            if step.workload.harness == "local-search":
                with prepare_frozen_readonly_skill_search(handoff) as (root, tool_step):
                    return _execute_command_step(root, tool_step, process, runtime)
            with prepare_agent_evidence(handoff) as prepared:
                if native_input is not None:
                    from oh_goon.integrations.fleet_plan_native_steps import (
                        execute_native_agent_step,
                    )

                    assert native_deadline is not None
                    return execute_native_agent_step(
                        repository,
                        step,
                        native_input,
                        prepared,
                        process,
                        runtime,
                        deadline=native_deadline,
                    )
                observation_step = ResolvedCommandStep(
                    step_id=handoff.step_id,
                    argv=(
                        sys.executable,
                        "-I",
                        "-m",
                        "oh_goon.integrations.hashmarks.agent_tools",
                        "--tool",
                        "task_evidence",
                        "--capture-identity",
                        handoff.source.repository.content_identity,
                        "--query=" + handoff.task,
                    ),
                    timeout_seconds=handoff.timeout_seconds,
                    success_codes=(0,),
                )
                evidence = _execute_command_step(prepared.root, observation_step, process, runtime)
                if evidence.returncode != 0:
                    return evidence
                if runtime.cancel_requested is not None and runtime.cancel_requested():
                    raise OperationCancelledError("Agent cancelled after repository observation")
                disclosure = task_evidence_prompt(
                    evidence.stdout,
                    expected_capture_identity=handoff.source.repository.content_identity,
                )
                outcome = _execute_model_agent(
                    handoff=handoff,
                    step=step,
                    process=process,
                    runtime=runtime,
                    workspace=prepared,
                    repository_evidence=disclosure,
                )
                # Provider stdout remains the native typed review/proposal record.
                # Retain the complete initial producer packet on the same Step's
                # evidence output without changing that publication contract.
                return replace(
                    outcome,
                    stderr="Hashmarks initial repository evidence (untrusted data):\n"
                    + evidence.stdout
                    + "\n"
                    + outcome.stderr,
                )
        if isinstance(step, ResolvedCommandStep):
            return _execute_command_step(repository, step, process, runtime)
        if isinstance(step, ResolvedDependencyInventoryStep):
            return execute_dependency_inventory(repository, step)
        if isinstance(step, ResolvedGitStep):
            return _execute_git_step(repository, step, process, runtime)
        if isinstance(step, ResolvedOciBuildStep):
            return execute_oci_build(
                repository,
                step,
                process.paths,
                environment=process.environment,
                command_runner=process.command_runner,
                cancel_requested=runtime.cancel_requested,
            )
        if isinstance(step, (ResolvedOciPublishStep, ResolvedOciTagAssertStep)):
            return _execute_oci_registry_step(repository, step, process, runtime)
        if isinstance(step, (ResolvedKubernetesSetImageStep, ResolvedKubernetesRolloutVerifyStep)):
            return execute_kubernetes_step(
                repository,
                step,
                process.paths,
                environment=process.environment,
                host_environment=process.host_environment,
                command_runner=process.command_runner,
                cancel_requested=runtime.cancel_requested,
            )
        if isinstance(step, ResolvedPreCommitStep):
            return execute_pre_commit(
                repository,
                step,
                runtime.tools,
                runtime.cancel_requested,
                environment=process.environment,
                command_runner=process.command_runner,
                executable=_pre_commit_executable(process.managed_runtime),
            )
        if isinstance(step, ResolvedExtensionStep):
            project_path, environment_path = _extension_runtime_paths(process.managed_runtime)
            return execute_extension(
                repository,
                step,
                runtime.extensions,
                runtime.git,
                runtime.cancel_requested,
                environment=process.environment,
                command_runner=process.command_runner,
                project_path=project_path,
                environment_path=environment_path,
            )
        raise FleetPlanInvalid(f"Unsupported resolved step type: {type(step).__name__}")
    except CommandCancelledError as exc:
        raise OperationCancelledError(str(exc)) from exc
    except (CommandOutputLimitExceeded, subprocess.TimeoutExpired) as exc:
        return bounded_execution_failure_outcome(step, exc)
    except OSError as exc:
        launch_failure = _command_launch_failure_outcome(repository, step, exc)
        if launch_failure is not None:
            return launch_failure
        raise


def _execute_model_agent(
    *,
    handoff: FrozenAgentStepHandoff,
    step: ResolvedAgentStep,
    process: _StepProcess,
    runtime: StepRuntime,
    workspace: PreparedAgentEvidence,
    repository_evidence: str,
) -> StepOutcome:
    """Consume one mandatory producer observation inside the original Agent Step."""
    if isinstance(step.workload, ResolvedProviderAgentWorkload):
        from oh_goon.integrations.agent_review_execution import execute_provider_agent

        return execute_provider_agent(
            step,
            handoff,
            process,
            runtime,
            timeout=_command_timeout(step, runtime),
            repository_evidence=repository_evidence,
        )
    if not isinstance(step.workload, ResolvedBuiltinAgentWorkload):
        raise FleetPlanInvalid("Agent workload does not support a bounded model turn")
    selected_query = None
    if step.workload.harness in (
        "local-model-search",
        "local-model-review",
        "local-model-tools",
        "local-model-files",
        "local-model-skill-resources",
        "local-model-proposal",
    ):
        if handoff.requested_effects.recipients != ("ollama_local",):
            raise FleetPlanInvalid("model recipient differs from frozen authorized choice")
        skill_member = next(
            (member for member in handoff.source.skill.members if member.path == "SKILL.md"),
            None,
        )
        if skill_member is None:
            raise FleetPlanInvalid("local model turn requires frozen SKILL.md")
        prompt_builder = (
            frozen_local_model_tool_prompt
            if step.workload.harness
            in (
                "local-model-tools",
                "local-model-files",
                "local-model-skill-resources",
                "local-model-proposal",
            )
            else frozen_local_model_prompt
        )
        prompt = prompt_builder(
            task=handoff.task,
            skill_bytes=skill_member.data,
            recipient=handoff.requested_effects.recipients[0],
            **(
                {
                    "allow_file": step.workload.harness
                    in (
                        "local-model-files",
                        "local-model-skill-resources",
                        "local-model-proposal",
                    ),
                    "allow_skill_file": step.workload.harness
                    in (
                        "local-model-skill-resources",
                        "local-model-proposal",
                    ),
                }
                if step.workload.harness
                in (
                    "local-model-tools",
                    "local-model-files",
                    "local-model-skill-resources",
                    "local-model-proposal",
                )
                else {}
            ),
        )
        prompt = prepend_repository_evidence(prompt, repository_evidence=repository_evidence)
        prompt = append_untrusted_agent_feedback(prompt, step.feedback_value)
        model_environment = {**process.environment, "OLLAMA_HOST": "http://127.0.0.1:11434"}
        assert (
            step.workload.model is not None
        )  # canonical local-model source resolution requires it
        # Non-pulling local model existence check before model disclosure.
        check = process.command_runner(
            "ollama",
            "show",
            step.workload.model,
            cwd=process.paths.tmp,
            timeout=_command_timeout(step, runtime),
            cancel_requested=runtime.cancel_requested,
            env=model_environment,
            runtime_supervision=runtime.runtime_supervision,
        )
        checked = from_command(check)
        if process_boundary_settled(checked) is not True:
            return checked
        if checked.returncode != 0:
            return replace(
                checked,
                returncode=PLAN_CONTRACT_RETURN_CODE,
                stderr="selected Ollama model is not installed locally",
            )
        answer = process.command_runner(
            "ollama",
            "run",
            step.workload.model,
            prompt,
            "--format",
            "json",
            "--hidethinking",
            "--nowordwrap",
            cwd=process.paths.tmp,
            timeout=_command_timeout(step, runtime),
            cancel_requested=runtime.cancel_requested,
            env=model_environment,
            runtime_supervision=runtime.runtime_supervision,
        )
        answered = from_command(answer)
        if process_boundary_settled(answered) is not True:
            return answered
        if answered.returncode != 0:
            return replace(
                answered,
                returncode=PLAN_CONTRACT_RETURN_CODE,
                stderr="selected Ollama model invocation failed",
            )
        selected_action = (
            parse_local_model_tool_action(
                answer.stdout,
                allow_file=step.workload.harness
                in (
                    "local-model-files",
                    "local-model-skill-resources",
                    "local-model-proposal",
                ),
                allow_skill_file=step.workload.harness
                in (
                    "local-model-skill-resources",
                    "local-model-proposal",
                ),
            )
            if step.workload.harness
            in (
                "local-model-tools",
                "local-model-files",
                "local-model-skill-resources",
                "local-model-proposal",
            )
            else None
        )
        selected_query = (
            selected_action.query
            if selected_action is not None
            else parse_one_local_model_query(answer.stdout)
        )
        if runtime.cancel_requested is not None and runtime.cancel_requested():
            raise OperationCancelledError("Agent cancelled after model selection")
        if step.workload.harness in (
            "local-model-review",
            "local-model-tools",
            "local-model-files",
            "local-model-skill-resources",
            "local-model-proposal",
        ):
            return _run_local_model_review(
                handoff=handoff,
                step=step,
                process=process,
                runtime=runtime,
                first_query=selected_query,
                model_environment=model_environment,
                skill_bytes=skill_member.data,
                first_action=selected_action,
                workspace=workspace,
                repository_evidence=repository_evidence,
            )
    with prepare_frozen_readonly_skill_search(
        handoff,
        selected_query=selected_query,
        workspace=workspace,
    ) as (root, tool_step):
        if tool_step.timeout_seconds != step.timeout_seconds:
            raise FleetPlanInvalid("Agent Step timeout differs from frozen delivery")
        outcome = _execute_command_step(root, tool_step, process, runtime)
        if selected_query is not None:
            outcome = replace(
                outcome,
                stdout=(
                    f"Agent local-model-search selected query: {selected_query}\n" + outcome.stdout
                ),
            )
        return outcome


def _run_local_model_review(
    *,
    handoff: FrozenAgentStepHandoff,
    step: ResolvedAgentStep,
    process: _StepProcess,
    runtime: StepRuntime,
    first_query: str | None,
    model_environment: Mapping[str, str],
    skill_bytes: bytes,
    first_action: LocalModelToolAction | None = None,
    workspace: PreparedAgentEvidence,
    repository_evidence: str,
) -> StepOutcome:
    """Bounded read-only model/tool turns within ONE original admitted Fleet Step/attempt.

    This is orchestration inside the current Step supervisor, not another runner
    or persistence authority. All model/tool processes use the same runner,
    cancellation, attempt paths and runtime deadline.
    """
    assert isinstance(step.workload, ResolvedBuiltinAgentWorkload)
    tool_mode = step.workload.harness in (
        "local-model-tools",
        "local-model-files",
        "local-model-skill-resources",
        "local-model-proposal",
    )
    action = (
        first_action
        if first_action is not None
        else LocalModelToolAction(tool="search_text", query=first_query)
    )
    previous: set[LocalModelToolAction] = set()
    inspected_paths: set[str] = set()
    transcript: list[str] = []
    max_actions = 4 if step.workload.harness in ("local-model-proposal",) else 2
    history: list[dict[str, object]] = []
    for ordinal in range(1, max_actions + 1):
        if runtime.cancel_requested is not None and runtime.cancel_requested():
            raise OperationCancelledError("Agent cancelled before search")
        if action in previous:
            raise FleetPlanInvalid("model repeated the same search instead of progressing")
        previous.add(action)
        with prepare_frozen_readonly_skill_search(
            handoff,
            selected_query=action.query,
            tool_kind=action.tool,
            selected_path=action.path,
            workspace=workspace,
        ) as (root, tool_step):
            if tool_step.timeout_seconds != step.timeout_seconds:
                raise FleetPlanInvalid("Agent Step timeout differs from frozen delivery")
            result = _execute_command_step(root, tool_step, process, runtime)
        if action.tool == "read_file" and result.returncode != 0:
            # The Agent no-match exit semantics apply only to rg searches, never head.
            # Preserve the physical exit in stderr and publish a failing Step status.
            return replace(
                result,
                returncode=PLAN_CONTRACT_RETURN_CODE,
                stderr=f"read_file failed (exit {result.returncode}): {result.stderr}",
            )
        if result.returncode not in tool_step.success_codes:
            return result  # physical failure cannot be reclassified by a model
        if (
            action.tool == "read_file"
            and action.path is not None
            and action.path.startswith("repo/")
        ):
            inspected_paths.add(action.path)
        disclosed_stdout = (
            prompt_observation(
                result.stdout,
                expected_capture_identity=handoff.source.repository.content_identity,
            )
            if action.tool in ("search_text", "list_paths")
            else result.stdout
        )
        transcript.append(
            (
                f"Tool {ordinal}: {action.tool} {action.query or action.path or ''} (exit {result.returncode})\n"
                if tool_mode
                else f"Search {ordinal}: {action.query!r} (exit {result.returncode})\n"
            )
            + f"{result.stdout}\n"
        )
        observation: _ObservationFields = {
            "task": handoff.task,
            "skill_bytes": skill_bytes,
            "recipient": handoff.requested_effects.recipients[0],
            "returncode": result.returncode,
            "stdout": disclosed_stdout,
            "stderr": result.stderr,
            "can_search": ordinal < max_actions,
        }
        if tool_mode:
            prompt = frozen_local_model_tool_observation_prompt(
                **observation,
                action=action,
                history=tuple(history)
                if step.workload.harness in ("local-model-proposal",)
                else (),
                allow_file=step.workload.harness
                in (
                    "local-model-files",
                    "local-model-skill-resources",
                    "local-model-proposal",
                ),
                allow_skill_file=step.workload.harness
                in (
                    "local-model-skill-resources",
                    "local-model-proposal",
                ),
                allow_proposal=step.workload.harness == "local-model-proposal",
            )
        else:
            assert action.query is not None
            prompt = frozen_local_model_observation_prompt(**observation, query=action.query)
        prompt = prepend_repository_evidence(prompt, repository_evidence=repository_evidence)
        prompt = append_untrusted_agent_feedback(prompt, step.feedback_value)
        if step.workload.harness in ("local-model-proposal",):
            history.append(
                {
                    "tool": action.tool,
                    "query": action.query,
                    "path": action.path,
                    "returncode": result.returncode,
                    "stdout_excerpt": disclosed_stdout[:350],
                }
            )
        response = process.command_runner(
            "ollama",
            "run",
            step.workload.model,
            prompt,
            "--format",
            "json",
            "--hidethinking",
            "--nowordwrap",
            cwd=process.paths.tmp,
            timeout=_command_timeout(step, runtime),
            cancel_requested=runtime.cancel_requested,
            env=model_environment,
            runtime_supervision=runtime.runtime_supervision,
        )
        responded = from_command(response)
        if process_boundary_settled(responded) is not True:
            return responded
        if responded.returncode != 0:
            return replace(
                responded,
                returncode=PLAN_CONTRACT_RETURN_CODE,
                stderr="selected Ollama model review invocation failed",
            )
        if step.workload.harness == "local-model-proposal":
            from oh_goon.core.agent_model_selection import (
                _one_json_object,
                frozen_patch_proposal_receipt,
                parse_local_model_patch_proposal,
            )

            if _one_json_object(response.stdout).get("decision") == "propose":
                proposal = parse_local_model_patch_proposal(
                    response.stdout,
                    members=handoff.source.repository.members,
                    inspected_paths=frozenset(inspected_paths),
                )
                if runtime.cancel_requested is not None and runtime.cancel_requested():
                    raise OperationCancelledError("Agent cancelled before proposal publication")
                return replace(
                    result,
                    returncode=0,
                    stdout=(
                        "".join(transcript)
                        + "Agent proposal only; no repository mutation or tests executed\n"
                        + frozen_patch_proposal_receipt(
                            proposal, handoff_identity=handoff.handoff_identity
                        )
                    ),
                )
        decision = (
            parse_local_model_tool_review_decision(
                response.stdout,
                can_continue=ordinal < max_actions,
                allow_file=step.workload.harness
                in (
                    "local-model-files",
                    "local-model-skill-resources",
                    "local-model-proposal",
                ),
                allow_skill_file=step.workload.harness
                in (
                    "local-model-skill-resources",
                    "local-model-proposal",
                ),
            )
            if tool_mode
            else parse_local_model_review_decision(
                response.stdout, can_search=ordinal < max_actions
            )
        )
        if runtime.cancel_requested is not None and runtime.cancel_requested():
            raise OperationCancelledError("Agent cancelled after model review")
        if decision.decision == "finish":
            # Model content is a summary, not a PASS authority: only the
            # physical tool and this validated contract determine success.
            return replace(
                result,
                returncode=0,
                stdout="".join(transcript) + f"Model evaluation: {decision.summary}\n",
            )
        if tool_mode:
            assert isinstance(decision, LocalModelToolDecision) and decision.action is not None
            action = decision.action
        else:
            assert isinstance(decision, LocalModelReviewDecision)
            action = LocalModelToolAction(tool="search_text", query=decision.query)
        if action in previous:
            raise FleetPlanInvalid("model repeated the same search instead of progressing")
    raise FleetPlanInvalid("model review exceeded the selected tool-action budget")


def _execute_command_step(
    repository: Path, step: ResolvedCommandStep, process: _StepProcess, runtime: StepRuntime
) -> StepOutcome:
    argv = step.argv
    managed = process.managed_runtime
    package_runtime = (
        step.project_source is not None
        or isinstance(managed, UvProjectManagedRuntime)
        and managed.package_source is not None
    )
    package = process.package_project if package_runtime else None
    if package_runtime and package is None:
        raise FleetPlanInvalid("Python Goon application requires its prepared project binding")
    if isinstance(managed, UvProjectManagedRuntime) or package is not None:
        if argv[:2] != ("uv", "run"):
            raise FleetPlanInvalid(
                f"Managed uv step {step.step_id} no longer executes through native uv run"
            )
        if package is not None:
            project = package
        else:
            assert isinstance(managed, UvProjectManagedRuntime)
            project = RepositoryPathSpace.from_root(repository).resolve(
                managed.project,
                label=f"Fleet uv project for step {step.step_id}",
                kind="directory",
                require_exists=True,
            )
        try:
            project_index = argv.index("--project")
        except ValueError as exc:
            raise FleetPlanInvalid(
                f"Managed uv step {step.step_id} lost its compiled --project binding"
            ) from exc
        if project_index + 1 >= len(argv):
            raise FleetPlanInvalid(
                f"Managed uv step {step.step_id} has an incomplete compiled --project binding"
            )
        rebound = list(argv)
        rebound[project_index + 1] = str(project)
        if package is not None:
            assert step.uv is not None
            command_index = project_index + 2 + (2 if step.uv.group is not None else 0)
            command = rebound[command_index]
            if Path(command).name != command or command in {".", ".."}:
                raise FleetPlanInvalid("Python Goon must invoke an installed application command")
            rebound[command_index] = str(
                Path(process.environment["UV_PROJECT_ENVIRONMENT"]) / "bin" / command
            )
        argv = ("uv", "run", "--no-sync", *rebound[2:])
    return from_command(
        process.command_runner(
            *argv,
            cwd=step_working_directory(repository, step),
            timeout=_command_timeout(step, runtime),
            cancel_requested=runtime.cancel_requested,
            env=process.environment,
            runtime_supervision=runtime.runtime_supervision,
        )
    )


def _prepare_python_project_attempt(
    repository: Path, step: ResolvedCommandStep, process: _StepProcess, runtime: StepRuntime
) -> tuple[_StepProcess, StepOutcome | None]:
    from oh_goon.core.path_layouts import ManagedPreparationPathLayout
    from oh_goon.engine.native_uv_environment import capture_native_uv_authority

    assert step.project_source is not None and step.uv is not None
    source = runtime.project_sources.get(step.project_source.content_sha256)
    if source is None:
        raise FleetPlanInvalid("Python Goon execution requires its captured package source")
    project = process.paths.uv_build_project
    observed = copy_frozen_directory(
        source,
        project,
        label=f"Python Goon {step.step_id} build project",
        cancel_requested=runtime.cancel_requested,
    )
    expected = step.project_source
    if observed != SnapshotIdentity(
        sha256=expected.content_sha256,
        byte_count=expected.byte_count,
        entry_count=expected.entry_count,
    ):
        raise FleetPlanInvalid("Python Goon source changed while creating its build project")
    native = capture_native_uv_authority(
        process.host_environment,
        owned_uv_names=frozenset({"UV_PROJECT_ENVIRONMENT", "UV_CACHE_DIR", "UV_OFFLINE"}),
    )
    environment = native.environment(
        ManagedPreparationPathLayout.from_root(process.paths.root),
        environment=process.paths.cache / "uv-project-environment",
        cache=process.paths.cache / "uv",
    )
    environment.update(process.environment)
    prepared = replace(process, environment=environment, package_project=project)
    result = prepared.command_runner(
        "uv",
        "sync",
        "--project",
        str(project),
        "--locked",
        "--no-default-groups",
        "--no-editable",
        *(["--group", step.uv.group] if step.uv.group is not None else []),
        cwd=repository,
        timeout=_command_timeout(step, runtime),
        cancel_requested=runtime.cancel_requested,
        env=environment,
        runtime_supervision=runtime.runtime_supervision,
    )
    outcome = from_command(result)
    return prepared, outcome if outcome.returncode != 0 or process_boundary_settled(
        outcome
    ) is not True else None


def _prepare_strict_uv_attempt(
    repository: Path,
    step: ResolvedCommandStep,
    process: _StepProcess,
    runtime: StepRuntime,
) -> tuple[_StepProcess, StepOutcome | None]:
    managed = process.managed_runtime
    if not isinstance(managed, UvProjectManagedRuntime):
        return process, None
    if step.uv is None:
        raise FleetPlanInvalid(f"Managed uv runtime is bound to non-uv step {step.step_id}")

    if managed.package_source is not None:
        project = RepositoryPathSpace.from_root(process.paths.cache).resolve(
            process.paths.uv_build_project.relative_to(process.paths.cache).as_posix(),
            label=f"Fleet uv build project for step {step.step_id}",
            kind="directory",
            require_exists=True,
        )
    else:
        project = managed.package_source or RepositoryPathSpace.from_root(repository).resolve(
            managed.project,
            label=f"Fleet uv project for step {step.step_id}",
            kind="directory",
            require_exists=True,
        )
    environment = process.paths.cache / "uv-project-environment"
    env = dict(process.environment)
    env["UV_PROJECT_ENVIRONMENT"] = str(environment)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    prepared = replace(
        process,
        environment=env,
        package_project=project if managed.package_source is not None else None,
    )
    result = prepared.command_runner(
        "uv",
        "sync",
        "--project",
        str(project),
        *(["--locked"] if managed.isolated_preparation else ["--frozen", "--offline"]),
        "--link-mode",
        "copy",
        "--no-default-groups",
        *(["--group", managed.group] if managed.group is not None else []),
        *(["--no-editable"] if managed.package_source is not None else []),
        cwd=repository,
        timeout=(
            None
            if runtime.runtime_supervision is not None
            else min(step.timeout_seconds or 900.0, 900.0)
        ),
        cancel_requested=runtime.cancel_requested,
        env=env,
        runtime_supervision=runtime.runtime_supervision,
    )
    outcome = from_command(result)
    if outcome.returncode != 0 or process_boundary_settled(outcome) is not True:
        return prepared, outcome
    return prepared, None


def _command_timeout(
    step: ResolvedCommandStep | ResolvedAgentStep, runtime: StepRuntime
) -> float | None:
    """Exactly one runtime deadline owner reaches command execution."""
    if runtime.runtime_supervision is not None:
        return None
    return step.timeout_seconds


def _direct_step_command_runner(
    *args: str,
    timeout: float | None = 10.0,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    stream_output: CommandOutputObserver | None = None,
    process_observer: CommandProcessObserver | None = None,
    runtime_supervision: CommandRuntimeSupervision | None = None,
    terminal: CommandTerminal | None = None,
    input_data: bytes | None = None,
    read_only_mounts: tuple[StandardReadOnlyMount, ...] = (),
    broker_directory: Path | None = None,
    read_only_workspace: bool = False,
    allowed_write_roots: tuple[Path, ...] = (),
    host_network: bool = False,
    private_configuration_directory: Path | None = None,
    preserve_native_executable_path: bool = True,
    retain_output_tail: bool = False,
) -> CommandResult:
    if (
        read_only_mounts
        or broker_directory is not None
        or read_only_workspace
        or allowed_write_roots
        or host_network
        or private_configuration_directory is not None
        or not preserve_native_executable_path
        or retain_output_tail
    ):
        raise FleetPlanInvalid("Native process options require a resolved native profile")
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError("Fleet Step cancelled before process launch")
    return run_command(
        *args,
        timeout=timeout,
        cwd=cwd,
        env=env,
        cancel_requested=cancel_requested,
        stream_output=stream_output,
        process_observer=process_observer,
        runtime_supervision=runtime_supervision,
        terminal=terminal,
        input_data=input_data,
        max_capture_bytes_per_stream=MAX_FLEET_STEP_CAPTURE_BYTES_PER_STREAM,
    )


def _command_launch_failure_outcome(
    repository: Path, step: ResolvedPlanStep, error: OSError
) -> StepOutcome | None:
    if not isinstance(step, ResolvedCommandStep) or error.filename != step.argv[0]:
        return None
    if error.errno == errno.ENOENT:
        if _declared_command_path_exists(repository, step):
            return executable_not_runnable_outcome(step, step.argv[0])
        return executable_not_found_outcome(step, step.argv[0])
    if error.errno in {errno.EACCES, errno.ENOEXEC}:
        return executable_not_runnable_outcome(step, step.argv[0])
    return None


def _declared_command_path_exists(repository: Path, step: ResolvedCommandStep) -> bool:
    executable = Path(step.argv[0])
    if not executable.is_absolute():
        if "/" not in step.argv[0]:
            return False
        executable = step_working_directory(repository, step) / executable
    try:
        return executable.exists()
    except OSError:
        return False


def _execute_oci_registry_step(
    repository: Path,
    step: ResolvedOciPublishStep | ResolvedOciTagAssertStep,
    process: _StepProcess,
    runtime: StepRuntime,
) -> StepOutcome:
    host_environment = process.host_environment
    if isinstance(step, ResolvedOciPublishStep):
        return execute_oci_publish(
            repository,
            step,
            process.paths,
            environment=process.environment,
            host_environment=host_environment,
            command_runner=process.command_runner,
            cancel_requested=runtime.cancel_requested,
        )
    return execute_oci_tag_assert(
        repository,
        step,
        process.paths,
        environment=process.environment,
        host_environment=host_environment,
        command_runner=process.command_runner,
        cancel_requested=runtime.cancel_requested,
    )


def _require_strict_managed_runtime(
    step: ResolvedPlanStep, runtime: ManagedRuntimeSpec | None
) -> None:
    if isinstance(step, ResolvedPreCommitStep) and not isinstance(runtime, PreCommitManagedRuntime):
        raise FleetPlanInvalid("Strict pre-commit execution is missing its bound managed runtime")
    if isinstance(step, ResolvedExtensionStep) and not isinstance(runtime, ExtensionManagedRuntime):
        raise FleetPlanInvalid("Strict extension execution is missing its bound managed runtime")
    if (
        isinstance(step, ResolvedCommandStep)
        and step.uv is not None
        and not isinstance(runtime, UvProjectManagedRuntime)
    ):
        raise FleetPlanInvalid("Strict uv execution is missing its native project runtime")


def _pre_commit_executable(runtime: ManagedRuntimeSpec | None) -> Path | None:
    return runtime.executable if isinstance(runtime, PreCommitManagedRuntime) else None


def _extension_runtime_paths(
    runtime: ManagedRuntimeSpec | None,
) -> tuple[Path | None, Path | None]:
    if isinstance(runtime, ExtensionManagedRuntime):
        return runtime.project, runtime.environment
    return None, None


def _require_explicit_script_interpreter(repository: Path, step: ResolvedCommandStep) -> None:
    """Do not let a workspace shebang silently acquire an undeclared host interpreter."""

    executable = step.argv[0]
    if "/" not in executable and "\\" not in executable:
        return
    cwd = step_working_directory(repository, step)
    candidate = canonical_path(cwd / executable)
    try:
        RepositoryPathSpace.from_root(repository).relative(
            candidate, label=f"Fleet plan step {step.step_id} executable"
        )
    except FleetPlanInvalid:
        return
    if not candidate.is_file():
        return
    try:
        with candidate.open("rb") as handle:
            prefix = handle.read(2)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet plan step {step.step_id} cannot inspect workspace executable {executable!r}"
        ) from exc
    if prefix == b"#!":
        raise FleetPlanInvalid(
            f"Fleet plan step {step.step_id} executes script {executable!r} through an implicit "
            "shebang interpreter. Strict CI/CD requires the interpreter explicitly as argv[0] "
            "(for example: ['sh', './ci/script.sh'])."
        )


def _execute_git_step(
    repository: Path,
    step: ResolvedGitStep,
    process: _StepProcess,
    runtime: StepRuntime,
) -> StepOutcome:
    environment = dict(process.environment)
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return execute_git(
        runtime.git,
        repository,
        step,
        runtime.cancel_requested,
        environment=environment,
        command_runner=process.git_command_runner,
    )


def _with_output_observer(
    process: _StepProcess,
    step: ResolvedPlanStep,
    attempt_number: int,
    observer: StepOutputObserver | None,
    diagnostics: DiagnosticEmitter | None,
    fleet_run_id: str | None,
    repository_id: str | None,
) -> _StepProcess:
    if observer is None and diagnostics is None and not isinstance(step, ResolvedAgentStep):
        return process
    command_runner = (
        observed_command_runner(
            process.command_runner,
            step=step,
            attempt_number=attempt_number,
            observer=observer,
            diagnostics=diagnostics,
            host_environment=process.host_environment,
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
        )
        if observer is not None
        else process.command_runner
    )

    def redact_visible_output(text: str) -> str:
        return redact_diagnostic_text(redact_host_secret_text(step, text, process.host_environment))

    return replace(
        process,
        command_runner=command_runner,
        git_command_runner=command_runner if observer is not None else process.git_command_runner,
        diagnostic_output=observed_step_diagnostic(
            step=step,
            attempt_number=attempt_number,
            observer=observer,
            diagnostics=diagnostics,
            host_environment=process.host_environment,
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
        ),
        redact_visible_output=redact_visible_output,
        visible_output=observed_native_visible_claim(
            step_id=step.step_id,
            attempt_number=attempt_number,
            diagnostics=diagnostics,
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
        ),
    )


def _with_lifecycle_observer(
    process: _StepProcess,
    step: ResolvedPlanStep,
    step_order: int,
    attempt_number: int,
    observer: StepLifecycleObserver | None,
) -> _StepProcess:
    if observer is None:
        return process
    runner = lifecycle_command_runner(
        process.command_runner,
        step_id=step.step_id,
        step_order=step_order,
        attempt_number=attempt_number,
        observer=observer,
    )
    return replace(
        process,
        command_runner=runner,
        git_command_runner=runner,
    )


def _isolated_command_runner(
    attempt: FleetPlanStepAttempt,
    step: ResolvedPlanStep,
    process_isolation: ProcessIsolationBackend,
    *,
    termination_requested: Callable[[], TerminationSignal | None] | None,
    fleet_run_id: str | None = None,
    diagnostics: DiagnosticEmitter | None = None,
    local_resource_intent_for: Callable[
        [ResolvedPlanStep, FleetPlanStepAttempt, int], LocalLaceIntentReservation
    ]
    | None = None,
    trusted_local_consent: TrustedLocalRunConsent | None = None,
    trusted_local_scope: TrustedLocalRunScope | None = None,
) -> CommandRunner:
    command_ordinal = 0

    def run_in_sandbox(
        *argv: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
        **native_options: Any,
    ) -> CommandResult:
        if native_options:
            raise FleetPlanInvalid("Ordinary native launch options cannot replace strict custody")
        if cwd is None or env is None:
            raise FleetPlanInvalid(
                f"Fleet sandbox command for step {step.step_id} requires explicit cwd and env"
            )
        nonlocal command_ordinal
        command_ordinal += 1
        materialization_economics = verify_step_executable_materialization(
            attempt.paths.workspace, attempt.executable_materialization
        )
        request = IsolatedProcessRequest(
            attempt=attempt,
            argv=tuple(argv),
            cwd=cwd,
            env=dict(env),
            termination_requested=termination_requested,
            fleet_run_id=fleet_run_id,
            step_id=step.step_id,
            command_ordinal=command_ordinal,
            diagnostics=diagnostics,
            trusted_local_consent=trusted_local_consent,
            trusted_local_scope=trusted_local_scope,
            # No effect authority here until the original Enforcer/LACE owner
            # supplies a validated effect-time port in a separate checkpoint.
            local_resource_intent=(
                local_resource_intent_for(step, attempt, command_ordinal)
                if local_resource_intent_for is not None
                else None
            ),
        )
        execute_kwargs: _IsolatedExecuteOptions = {
            "timeout": timeout,
            "cancel_requested": cancel_requested,
            "process_observer": process_observer,
        }
        if stream_output is not None:
            execute_kwargs["stream_output"] = stream_output
        if runtime_supervision is not None:
            execute_kwargs["runtime_supervision"] = runtime_supervision
        result = process_isolation.execute(request, **execute_kwargs)
        return replace(
            result,
            execution_authority_economics=(
                materialization_economics,
                *result.execution_authority_economics,
            ),
        )

    return run_in_sandbox


@contextmanager
def prepare_frozen_readonly_skill_search(
    handoff: FrozenAgentStepHandoff,
    *,
    selected_query: str | None = None,
    tool_kind: Literal[
        "search_text", "list_paths", "list_skill_paths", "read_file"
    ] = "search_text",
    selected_path: str | None = None,
    workspace: PreparedAgentEvidence | None = None,
) -> Iterator[tuple[Path, ResolvedCommandStep]]:
    """Prepare one fixed read-only tool Step from original Resolver-captured bytes.

    This is PREWORK: it does not grant permission to execute. Only the original
    admitted Step lifecycle may consume the returned ResolvedCommandStep. No
    paths from authoring are re-opened and SKILL.md is never executed as code.
    AGENT-01A supports one deterministic local search, not a model/agent loop.
    """
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    if type(handoff) is not FrozenAgentStepHandoff:
        raise FleetPlanInvalid("AGENT-01A requires original frozen Agent handoff")
    handoff.verify()
    effects = handoff.requested_effects
    if (
        handoff.harness
        not in (
            "local-search",
            "local-model-search",
            "local-model-review",
            "local-model-tools",
            "local-model-files",
            "local-model-skill-resources",
            "local-model-proposal",
        )
        or effects.repository_access != "read_only"
        or effects.capabilities.egress.mode != "none"
        or effects.capabilities.nested_strict_execution
        or effects.capabilities.tools
    ):
        raise FleetPlanInvalid(
            "AGENT-01A local-search permits only read-only, offline, recipient-free tools"
        )
    if (
        tool_kind not in ("search_text", "list_paths", "list_skill_paths", "read_file")
        or (
            tool_kind == "list_paths"
            and handoff.harness
            not in (
                "local-model-tools",
                "local-model-files",
                "local-model-skill-resources",
                "local-model-proposal",
            )
        )
        or (
            tool_kind == "list_skill_paths"
            and handoff.harness not in ("local-model-skill-resources", "local-model-proposal")
        )
        or (
            tool_kind == "read_file"
            and handoff.harness
            not in (
                "local-model-files",
                "local-model-skill-resources",
                "local-model-proposal",
            )
        )
    ):
        raise FleetPlanInvalid("Agent tool was not selected by the frozen harness")
    query: str | None
    if handoff.harness == "local-search":
        if selected_query is not None or effects.recipients or effects.model is not None:
            raise FleetPlanInvalid(
                "local-search permits only read-only, offline, recipient-free tools"
            )
        prefix = "search:"
        if not handoff.task.startswith(prefix):
            raise FleetPlanInvalid("AGENT-01A task must start with search:")
        query = handoff.task[len(prefix) :].strip()
    else:
        if effects.recipients != ("ollama_local",) or effects.model is None:
            raise FleetPlanInvalid("model search requires frozen local recipient selection")
        query = selected_query
    if tool_kind == "search_text":
        if (
            type(query) is not str
            or not query
            or len(query) > 200
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in query)
        ):
            raise FleetPlanInvalid("AGENT-01A search query must be bounded printable text")
    elif query is not None:
        raise FleetPlanInvalid("non-search tool cannot request a search expression")
    if tool_kind == "read_file":
        from oh_goon.core.agent_model_selection import parse_local_model_tool_action

        file_action = parse_local_model_tool_action(
            json.dumps({"tool": "read_file", "path": selected_path}),
            allow_file=True,
            allow_skill_file=handoff.harness
            in ("local-model-skill-resources", "local-model-proposal"),
        )
        allowed_paths = {"repo/" + member.path for member in handoff.source.repository.members}
        if handoff.harness in (
            "local-model-skill-resources",
            "local-model-proposal",
        ):
            allowed_paths.update("skill/" + member.path for member in handoff.source.skill.members)
        if file_action.path not in allowed_paths:
            raise FleetPlanInvalid("read_file target is not a captured repository member")
    elif selected_path is not None:
        raise FleetPlanInvalid("only read_file may select a frozen member path")

    context = prepare_agent_evidence(handoff) if workspace is None else nullcontext(workspace)
    with context as prepared:
        if prepared.handoff is not handoff:
            raise FleetPlanInvalid("Agent evidence workspace belongs to a different capture")
        root = prepared.root
        argv = (
            ("head", "-c", "2048", "--", selected_path)
            if tool_kind == "read_file"
            else ("rg", "--files", "--hidden", "--no-ignore", "--", "skill")
            if tool_kind == "list_skill_paths"
            else (
                sys.executable,
                "-I",
                "-m",
                "oh_goon.integrations.hashmarks.agent_tools",
                "--tool",
                tool_kind,
                "--capture-identity",
                handoff.source.repository.content_identity,
                *(("--query=" + query,) if query is not None else ()),
            )
        )
        step = ResolvedCommandStep(
            step_id=handoff.step_id,
            argv=argv,
            timeout_seconds=handoff.timeout_seconds,
            success_codes=(0,),  # source scope and absence qualification are in native evidence
        )
        yield root, step


@contextmanager
def prepare_delivered_readonly_skill_search(
    packet: bytes,
    *,
    expected_handoff_identity: str,
    expected_step_id: str,
) -> Iterator[tuple[Path, ResolvedCommandStep]]:
    """Consume original AG-02 immutable bytes at the existing Step preparation cut.

    Decoding and Step-identity matching is transport verification, **not** admission.
    No new runner, authority, filesystem reacquisition, or fallback is introduced.
    Only an already-authorized Step attempt may execute the projected command.
    """
    from oh_goon.core.models.agent_source import FrozenAgentStepHandoff

    if type(expected_step_id) is not str or not expected_step_id:
        raise FleetPlanInvalid("AGENT-01B1 expected admitted Step identity required")
    handoff = FrozenAgentStepHandoff.decode_prework_delivery(
        packet,
        expected_handoff_identity=expected_handoff_identity,
    )
    if handoff.step_id != expected_step_id:
        raise FleetPlanInvalid("AGENT-01B1 delivery Step differs from selected Step")
    with prepare_frozen_readonly_skill_search(handoff) as prepared:
        yield prepared
