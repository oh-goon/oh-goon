from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.agent_repair import AgentRepairContext
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.execution_identity import ExecutionIdentity
from oh_goon.core.models.fleet_plan import (
    MissingConfigPolicy,
    ResolvedCommandStep,
    ResolvedExtensionStep,
    ResolvedFleetPlan,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    PlanExecutionInputEvidence,
    PlanIsolationBackendEvidence,
    PlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import ExecutionPurpose, FleetPlanRunLimits
from oh_goon.core.models.lace_trusted_local import TrustedLocalRunConsent
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.termination import TerminationSignal
from oh_goon.core.path_authority import RepositoryPathSpace, StrictHostPathAuthority, canonical_path
from oh_goon.core.path_layouts import AttemptPathLayout, RunPathLayout
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.ports.local_lace_intent import LocalLaceIntentReservation
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.engine.command import CommandTerminal
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitObservation, GitService
from oh_goon.integrations._fleet_plan_host_resources import preflight_run_capabilities
from oh_goon.integrations._fleet_plan_step_dataflow import (
    validate_step_declared_paths,
)
from oh_goon.integrations._fleet_plan_step_local_execution import (
    step_working_directory,
    validate_extension,
    validate_pre_commit_runtime,
)
from oh_goon.integrations._fleet_plan_step_sequence import FleetPlanStepSequence
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_environment import require_environment
from oh_goon.integrations.fleet_plan_import_applicability import applicable_plan_for_repository
from oh_goon.integrations.fleet_plan_managed_runtime import (
    prepare_managed_runtimes,
    uv_preparation_requires_retained_scratch,
)
from oh_goon.integrations.fleet_plan_process_isolation import (
    ProcessIsolationBackend,
    TrustedLocalRunScope,
    admitted_tl1_run_context,
    isolation_backend_evidence,
    native_profile_command_runner,
    select_bound_process_isolation_backend,
)
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from oh_goon.integrations.fleet_plan_step_execution import StepLifecycleObserver, StepRuntime


@dataclass(frozen=True, slots=True)
class PlanExecutionOutcome:
    steps: tuple[PlanStepExecution, ...]
    stdout: str
    stderr: str
    execution_inputs: PlanExecutionInputEvidence | None = None


def _plan_uses_content_store(plan: ResolvedFleetPlan) -> bool:
    return any(
        step.cache.retain
        or getattr(step.cache.source, "kind", None) == "retained"
        or any(artifact.retention == "content_store" for artifact in step.artifacts)
        for step in plan.steps
    )


class FleetPlanRunExecutor:
    """Prepare and execute resolved plans through one explicit physical mode."""

    def __init__(
        self,
        git: GitService,
        extensions: FleetExtensionStore | None = None,
        tools: FleetToolStore | None = None,
        *,
        process_isolation_backends: tuple[ProcessIsolationBackend, ...],
        path_authority: StrictHostPathAuthority | None = None,
        content_store: FleetContentStore | None = None,
        run_parent: Path | None = None,
        diagnostics: DiagnosticEmitter | None = None,
        terminal: CommandTerminal | None = None,
        standard_process_isolation: ProcessIsolationBackend | None = None,
        workspace_pin: Callable[[Path], AbstractContextManager[Callable[[], None]]] | None = None,
    ) -> None:
        self._git = git
        self._extensions = extensions
        self._tools = tools
        self._process_isolation_backends = process_isolation_backends
        self._path_authority = path_authority
        self._content_store = content_store
        self._run_parent = canonical_path(run_parent) if run_parent is not None else None
        self._diagnostics = diagnostics
        self._terminal = terminal
        self._standard_process_isolation = standard_process_isolation
        self._workspace_pin = workspace_pin

    def prepare_direct_run(
        self,
        repository: RepositoryIdentity,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        """Revalidate one already-admitted non-CI/CD run before effect-start."""

        if plan.execution.purpose is ExecutionPurpose.CICD:
            raise FleetPlanInvalid("Strict CI/CD plans require prepare_strict_run")
        self._preflight_direct_steps(repository.path, plan, cancel_requested=cancel_requested)

    def prepare_strict_run(
        self,
        repository: RepositoryIdentity,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
        admitted_source_authority: RepositorySourceAuthority,
        admitted_isolation_backend: PlanIsolationBackendEvidence | None,
        admitted_run: FleetRunDetail | None = None,
        execution_target: FleetPlanTarget | None = None,
    ) -> PreparedPlanRun:
        """Validate and freeze strict CI/CD inputs before EFFECT_STARTED."""

        if plan.execution.purpose is not ExecutionPurpose.CICD:
            raise FleetPlanInvalid("Non-CI/CD Fleet plans cannot prepare strict execution")
        if admitted_isolation_backend is None:
            raise FleetPlanInvalid(
                "Strict Fleet CI/CD requires process-isolation authority admitted before RUN_ADMITTED"
            )
        if self._path_authority is None:
            raise FleetPlanInvalid(
                "Strict Fleet CI/CD requires configured deployment path authority"
            )
        plan = self._with_project_delivery(plan, execution_target, repository.repository_id)
        process_isolation = select_bound_process_isolation_backend(
            plan,
            admitted_isolation_backend,
            self._process_isolation_backends,
            admitted_run=admitted_run,
        )
        # The original journal owns these facts. Selection already validated
        # the same Run; no consent is minted or inferred from Plan inputs.
        consent, scope = (
            admitted_tl1_run_context(plan, admitted_isolation_backend, admitted_run)
            if admitted_run is not None
            else (None, None)
        )
        active_plan, source_applicability = applicable_plan_for_repository(repository.path, plan)
        preflight_run_capabilities(active_plan, self._path_authority)
        self._preflight_strict_steps(
            repository.path, active_plan, cancel_requested=cancel_requested
        )
        content_store = self._content_store
        if _plan_uses_content_store(active_plan) and content_store is None:
            raise FleetPlanInvalid(
                "Fleet plan requires retained content, but no FleetContentStore was composed"
            )
        managed_runtimes = prepare_managed_runtimes(
            active_plan, extensions=self._extensions, tools=self._tools, git=self._git
        )
        prepared = PreparedPlanRun(
            repository,
            plan,
            source_authority=admitted_source_authority,
            process_isolation=process_isolation,
            managed_runtimes=managed_runtimes,
            content_store=content_store,
            isolation_backend=isolation_backend_evidence(process_isolation),
            path_authority=self._path_authority,
            run_parent=self._run_parent,
            cancel_requested=cancel_requested,
            trusted_local_consent=consent,
            trusted_local_scope=scope,
        )
        if prepared.execution_input_evidence().import_applicability != source_applicability:
            prepared.close()
            raise FleetPlanInvalid(
                "Fleet imported-Goon applicability changed between managed-source preflight "
                "and immutable execution snapshot"
            )
        return prepared

    def execute_direct_run(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
        termination_requested: Callable[[], TerminationSignal | None] | None = None,
        output_observer: StepOutputObserver | None = None,
        fleet_run_id: str | None = None,
        repository_id: str | None = None,
        lifecycle_observer: StepLifecycleObserver | None = None,
        agent_delivery_for: Callable[[str, str], bytes] | None = None,
        execution_limits: FleetPlanRunLimits | None = None,
        repair_context: AgentRepairContext | None = None,
        execution_target: FleetPlanTarget | None = None,
    ) -> PlanExecutionOutcome:
        """Execute one already-preflighted direct plan through live host authority."""

        if execution_target is not None:
            if execution_target.plan != plan:
                raise FleetPlanInvalid("Admitted execution target differs from the selected Plan")
        if plan.execution.purpose is ExecutionPurpose.CICD:
            raise FleetPlanInvalid("Strict CI/CD plans require execute_strict_run")
        runtime = self._step_runtime(
            cancel_requested,
            fleet_run_id,
            repository_id,
            lifecycle_observer,
            termination_requested=termination_requested,
            agent_delivery_for=agent_delivery_for,
            execution_limits=execution_limits,
            repair_context=repair_context,
        )
        runtime = replace(
            runtime,
            git=self.git_for_plan(repository, plan, termination_requested=termination_requested),
            execution_policy=plan.execution,
            execution_identity=execution_target.identity
            if execution_target is not None
            else ExecutionIdentity(digest=build_execution_lock(plan).digest),
            native_profile=plan.native_agent,
        )
        if not any(getattr(step, "project_source", None) is not None for step in plan.steps):
            sequence = FleetPlanStepSequence(repository, runtime, output_observer)
            for step in plan.steps:
                sequence.run_direct(step)
            return self._outcome(sequence)
        from oh_goon.integrations.python_goon_projects import materialize_plan_projects

        if self._run_parent is not None:
            self._run_parent.mkdir(parents=True, exist_ok=True)
        plan = self._with_project_delivery(plan, execution_target, repository_id)
        temporary = TemporaryDirectory(
            prefix="oh-goon-python-run-", dir=self._run_parent, delete=False
        )
        retain_package_scratch = False

        def retain_unsettled_package_scratch() -> None:
            nonlocal retain_package_scratch
            retain_package_scratch = True

        try:
            directory = temporary.name
            layout = RunPathLayout.from_root(Path(directory))
            sources = materialize_plan_projects(plan, layout)
            workspaces: dict[str, Path] = {}
            for step in plan.steps:
                if isinstance(step, ResolvedCommandStep) and step.project_source is not None:
                    workspace = AttemptPathLayout.from_root(
                        layout.attempt_root(step.step_id, 1)
                    ).workspace
                    workspace.mkdir(parents=True)
                    RepositoryPathSpace.from_root(workspace).resolve(
                        step.working_directory,
                        label=f"Python Goon {step.step_id} output working_directory",
                        kind="directory",
                    ).mkdir(parents=True, exist_ok=True)
                    workspaces[step.step_id] = workspace
            runtime = replace(
                runtime,
                project_sources=sources,
                project_workspaces=workspaces,
                on_unsettled_run_process=retain_unsettled_package_scratch,
            )
            sequence = FleetPlanStepSequence(repository, runtime, output_observer)
            for step in plan.steps:
                sequence.run_direct(step)
            outcome = self._outcome(sequence)
            if any(
                step.status != "SKIPPED" and step.settlement.cleanup != "VERIFIED"
                for step in outcome.steps
            ):
                retain_package_scratch = True
                outcome = replace(
                    outcome,
                    stderr=outcome.stderr.rstrip()
                    + f"\nPython Goon Run scratch retained while native process cleanup is incomplete: {directory}",
                )
            return outcome
        except BaseException as error:
            if retain_package_scratch or uv_preparation_requires_retained_scratch(error):
                retain_package_scratch = True
                retained_note = (
                    "Python Goon Run scratch retained while native process cleanup is incomplete: "
                    f"{temporary.name}"
                )
                error.add_note(retained_note)
                if self._diagnostics is not None:
                    self._diagnostics.emit(
                        DiagnosticEvent(
                            severity="WARNING",
                            source="fleet_plan_run_execution",
                            owner="FleetPlanRunExecutor",
                            operation="retain_incomplete_package_scratch",
                            message=retained_note,
                            execution_affected=True,
                            exception_type=error.__class__.__name__,
                            fleet_run_id=fleet_run_id,
                            repository_id=repository_id,
                        )
                    )
            raise
        finally:
            if not retain_package_scratch:
                temporary.cleanup()

    def _with_project_delivery(
        self, plan: ResolvedFleetPlan, target: FleetPlanTarget | None, repository_id: str | None
    ) -> ResolvedFleetPlan:
        from oh_goon.integrations.python_goon_projects import MAX_PROJECT_ARCHIVE_BYTES

        projects = {
            step.project_source.content_sha256: step.project_source
            for step in plan.steps
            if isinstance(step, ResolvedCommandStep) and step.project_source is not None
        }
        if not projects:
            return plan
        if target is None:
            if set(projects) <= plan._project_archives.keys():
                return plan
            raise FleetPlanInvalid("Python Goon execution requires original source transport")
        if self._content_store is None or repository_id is None:
            raise FleetPlanInvalid("Python Goon source transport requires repository custody")
        references = {
            item.project_sha256: item
            for item in target.project_deliveries
            if item.owner_repository_id == repository_id
        }
        if not set(projects) <= references.keys():
            raise FleetPlanInvalid("Python Goon delivery does not cover the admitted repository")
        archives: dict[str, bytes] = {}
        for digest in projects:
            reference = references[digest]
            stored = self._content_store.get(
                reference.reference_id, owner_repository_id=repository_id, expected_kind="file"
            )
            with stored.path.open("rb") as source:
                packet = source.read(MAX_PROJECT_ARCHIVE_BYTES + 1)
            if len(packet) > MAX_PROJECT_ARCHIVE_BYTES:
                raise FleetPlanInvalid("Python Goon source exceeds delivery bounds")
            archives[digest] = packet
        captured = plan.model_copy()
        captured._project_archives = archives
        return captured

    def git_for_plan(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        *,
        termination_requested: Callable[[], TerminationSignal | None] | None = None,
    ) -> GitService:
        """Keep native Git hooks within this Plan's frozen physical profile."""
        if plan.native_agent is None:
            return self._git
        runner = native_profile_command_runner(
            repository,
            plan.native_agent,
            self._standard_process_isolation,
            self._workspace_pin,
            termination_requested=termination_requested,
        )
        return replace(
            self._git,
            command_runner=runner,
            observation_runner=partial(runner, read_only_workspace=True)
            if plan.native_agent.profile.backend == "bubblewrap"
            else runner,
        )

    def observe_repository(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        cancel_requested: Callable[[], bool] | None,
        *,
        termination_requested: Callable[[], TerminationSignal | None] | None = None,
    ) -> GitObservation:
        return self.git_for_plan(
            repository, plan, termination_requested=termination_requested
        ).observe(repository, cancel_requested)

    def execute_strict_run(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        prepared_run: PreparedPlanRun,
        *,
        cancel_requested: Callable[[], bool] | None,
        termination_requested: Callable[[], TerminationSignal | None] | None = None,
        output_observer: StepOutputObserver | None = None,
        fleet_run_id: str | None = None,
        repository_id: str | None = None,
        lifecycle_observer: StepLifecycleObserver | None = None,
        local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None,
    ) -> PlanExecutionOutcome:
        """Execute one strict plan only through its already-bound prepared authority."""

        if plan.execution.purpose is not ExecutionPurpose.CICD:
            raise FleetPlanInvalid(
                "Non-CI/CD Fleet plans cannot consume strict execution authority"
            )
        if prepared_run.plan_id != plan.plan_id:
            raise FleetPlanInvalid(
                f"Prepared strict run is bound to plan {prepared_run.plan_id}, not {plan.plan_id}"
            )
        # Check exact Run identity before binding callbacks or materializing
        # any disposable Step attempts. No consent may transfer to another Run.
        if (
            prepared_run.trusted_local_scope is not None
            and prepared_run.trusted_local_scope.fleet_run_id != fleet_run_id
        ):
            raise FleetPlanInvalid("TL-1 prepared Run identity differs from worker Run")
        # The callback used while freezing immutable inputs can be backed by a
        # preparation-scoped SQLite observer and is invalid after prepare_strict_run
        # returns. Rebind the execution-lifetime callback at the strict execution
        # boundary before any physical attempt/cache/lineage work begins.
        prepared_run.bind_execution_cancel_requested(cancel_requested)
        runtime = self._step_runtime(
            cancel_requested,
            fleet_run_id,
            repository_id,
            lifecycle_observer,
            termination_requested=termination_requested,
            local_lace_intent_for=local_lace_intent_for,
            trusted_local_consent=prepared_run.trusted_local_consent,
            trusted_local_scope=prepared_run.trusted_local_scope,
        )
        execution_inputs = prepared_run.execution_input_evidence()
        sequence = FleetPlanStepSequence(repository, runtime, output_observer)
        for step in plan.steps:
            sequence.run_strict(prepared_run, step)
        return self._outcome(sequence, execution_inputs=execution_inputs)

    def _step_runtime(
        self,
        cancel_requested: Callable[[], bool] | None,
        fleet_run_id: str | None,
        repository_id: str | None,
        lifecycle_observer: StepLifecycleObserver | None,
        *,
        termination_requested: Callable[[], TerminationSignal | None] | None = None,
        local_lace_intent_for: Callable[..., LocalLaceIntentReservation] | None = None,
        trusted_local_consent: TrustedLocalRunConsent | None = None,
        trusted_local_scope: TrustedLocalRunScope | None = None,
        agent_delivery_for: Callable[[str, str], bytes] | None = None,
        execution_limits: FleetPlanRunLimits | None = None,
        repair_context: AgentRepairContext | None = None,
    ) -> StepRuntime:
        return StepRuntime(
            git=self._git,
            extensions=self._extensions,
            tools=self._tools,
            cancel_requested=cancel_requested,
            termination_requested=termination_requested,
            diagnostics=self._diagnostics,
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
            lifecycle_observer=lifecycle_observer,
            local_resource_intent_for=local_lace_intent_for,
            trusted_local_consent=trusted_local_consent,
            trusted_local_scope=trusted_local_scope,
            agent_delivery_for=agent_delivery_for,
            execution_limits=execution_limits,
            repair_context=repair_context,
            terminal=self._terminal,
            standard_process_isolation=self._standard_process_isolation,
            workspace_pin=self._workspace_pin,
        )

    @staticmethod
    def _outcome(
        sequence: FleetPlanStepSequence,
        *,
        execution_inputs: PlanExecutionInputEvidence | None = None,
    ) -> PlanExecutionOutcome:
        return PlanExecutionOutcome(
            steps=sequence.results,
            stdout=sequence.stdout,
            stderr=sequence.stderr,
            execution_inputs=execution_inputs,
        )

    def _preflight_direct_steps(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        self.git_for_plan(repository, plan)
        for step in plan.steps:
            self._preflight_common(repository, step, cancel_requested=cancel_requested)
            if not isinstance(step, ResolvedCommandStep) or step.project_source is None:
                step_working_directory(repository, step)
            self._validate_live_managed_runtime(step)

    def _preflight_strict_steps(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        for step in plan.steps:
            self._preflight_common(repository, step, cancel_requested=cancel_requested)
            if not isinstance(step, ResolvedCommandStep) or step.project_source is None:
                step_working_directory(repository, step)

    @staticmethod
    def _preflight_common(
        repository: Path,
        step: ResolvedPlanStep,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        if cancel_requested is not None and cancel_requested():
            raise OperationCancelledError("Fleet plan cancelled during preflight")
        require_environment(step)
        if not isinstance(step, ResolvedCommandStep) or step.project_source is None:
            validate_step_declared_paths(repository, step)
        if isinstance(step, ResolvedPreCommitStep) and (
            step.if_missing is MissingConfigPolicy.FAIL
            and not (repository / ".pre-commit-config.yaml").is_file()
        ):
            raise FleetPlanInvalid(
                f"Repository has no .pre-commit-config.yaml required by step {step.step_id}"
            )

    def _validate_live_managed_runtime(self, step: ResolvedPlanStep) -> None:
        """Revalidate live managed inputs only for direct execution using host paths."""

        if isinstance(step, ResolvedExtensionStep):
            validate_extension(self._extensions, self._git, step)
            assert self._extensions is not None
            self._extensions.check_environment(step.extension_id)
        elif isinstance(step, ResolvedPreCommitStep):
            validate_pre_commit_runtime(self._tools, step)
