from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.capability_registry import capability_definitions
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.engine.runner import EngineRunner
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plans import FleetPlanRunner
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.integrations.migration import MigrationRunner
from oh_goon.integrations.recipes import UvRecipeRunner
from oh_goon.runtime._executor_configuration import (
    ExecutorPoolConfiguration,
    configured_executor_pool,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.diagnostics import DiagnosticRouter, JsonlDiagnosticSink, StderrDiagnosticSink
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, RunExecutionSupervisor
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.lifecycle import RuntimeLifecycle
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_definitions import ScheduleDefinitionStore
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.runtime.scope_mutation_guard import DurableScopeMutationGuard
from oh_goon.runtime.state import LocalState
from oh_goon.services.capability_executor import CapabilityExecutor
from oh_goon.services.direct_effect_admission import DirectEffectAdmission
from oh_goon.services.effect_recovery import RepositoryEffectRecovery
from oh_goon.services.fleet_prework_composition import FleetPreworkComposition
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.reconciliation import RepositoryReconciler
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution
from oh_goon.services.repository_effect_fence import RepositoryEffectFence
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement
from oh_goon.services.runtime_safety import RuntimeSafetyService


@dataclass(frozen=True, slots=True)
class RuntimeFoundation:
    """Dependencies that cross from infrastructure wiring into product composition."""

    database: LocalDatabase
    state: LocalState
    execution: ExecutionCoordinator
    lifecycle: RuntimeLifecycle
    schedule_definitions: ScheduleDefinitionStore
    schedule_executions: ScheduleExecutionStore
    effect_recovery: RepositoryEffectRecovery
    auditor: SystemInvariantAuditor
    runtime_safety: RuntimeSafetyService
    supervisor: RunExecutionSupervisor
    executor_pool: FleetExecutorPool
    content_store: FleetContentStore
    repository_effects: RepositoryEffectExecution
    direct_operations: DirectEffectAdmission
    extension_store: FleetExtensionStore
    tool_store: FleetToolStore
    live_output: GameTapeLiveOutputStore
    diagnostics: DiagnosticRouter
    # No ambient keys or implied activation; the production composition owner
    # must explicitly supply one complete, Run-bound authority.
    fleet_prework: FleetPreworkComposition | None = None
    fleet_provisioner: FleetPreworkProvisioner | None = None
    require_authenticated_fleet: bool = True
    require_compiled_fleet_contract: bool = True
    standard_local_fleet: bool = False


@dataclass(frozen=True, slots=True)
class _LocalStateFoundation:
    database: LocalDatabase
    journal: Journal
    execution: ExecutionCoordinator
    state: LocalState


def _build_capability_runtime(
    database: LocalDatabase,
    git: GitService,
    extensions: FleetExtensionStore,
    tools: FleetToolStore,
    *,
    configuration: ExecutorPoolConfiguration | None,
    content_store_root: Path,
    run_parent: Path,
    live_output: GameTapeLiveOutputStore,
    diagnostics: DiagnosticRouter,
) -> tuple[FleetExecutorPool, CapabilityExecutor, FleetContentStore]:
    executor_pool = FleetExecutorPool(database, configuration)
    process_isolation_backends = (
        tuple(item.backend for item in configuration.executors) if configuration is not None else ()
    )
    path_authority = configuration.path_authority if configuration is not None else None
    content_store = FleetContentStore(content_store_root)
    runners = {
        CapabilityRunnerKind.ENGINE: EngineRunner(git),
        CapabilityRunnerKind.RECIPE: UvRecipeRunner(),
        CapabilityRunnerKind.MIGRATION: MigrationRunner(git),
        CapabilityRunnerKind.FLEET_PLAN: FleetPlanRunner(
            git,
            extensions,
            tools,
            process_isolation_backends=process_isolation_backends,
            path_authority=path_authority,
            content_store=content_store,
            run_parent=run_parent,
            output_observer_factory=live_output.observer,
            lifecycle_observer_factory=Journal(database).execution_lifecycle_observer,
            diagnostics=diagnostics,
        ),
    }
    _require_complete_runner_wiring(runners)
    return executor_pool, CapabilityExecutor(runners), content_store


def _require_complete_runner_wiring(runners) -> None:
    required = {definition.runner_kind for definition in capability_definitions()}
    configured = set(runners)
    if configured != required:
        missing = sorted(item.value for item in required - configured)
        extra = sorted(item.value for item in configured - required)
        raise AssertionError(
            "Production capability runner wiring must exactly cover registered capability families; "
            f"missing={missing}, extra={extra}"
        )


def _build_local_state_foundation() -> _LocalStateFoundation:
    database = LocalDatabase()
    league_folders = LeagueFolderStore()
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    state = LocalState(
        database,
        journal=journal,
        mutation_guard=DurableScopeMutationGuard(execution, journal),
        league_folders=league_folders,
    )
    state.reconcile_league_folders()
    return _LocalStateFoundation(database, journal, execution, state)


def _prove_startup_safety(
    state: LocalState,
    execution: ExecutionCoordinator,
    effect_attempts: RepositoryEffectAttemptStore,
    schedule_executions: ScheduleExecutionStore,
    reconciler: RepositoryReconciler,
    settlement: RepositoryEffectSettlement,
) -> tuple[RepositoryEffectRecovery, SystemInvariantAuditor, RuntimeSafetyService]:
    effect_recovery = RepositoryEffectRecovery(
        state.database,
        effect_attempts,
        state.journal,
        execution,
        reconciler,
        settlement,
    )
    effect_recovery.recover()
    auditor = SystemInvariantAuditor(
        state.database,
        state.journal,
        execution,
        effect_attempts,
        schedule_executions,
    )
    gate = RuntimeSafetyGate()
    service = RuntimeSafetyService(auditor, gate)
    service.refresh()
    return effect_recovery, auditor, service


def standard_local_profile(requested: bool | None, *, deployment_selected: bool) -> bool:
    """Own the ordinary local default; an explicit deployment selects strict execution."""
    return not deployment_selected if requested is None else requested


def build_runtime_foundation(
    *,
    fleet_provisioner: FleetPreworkProvisioner | None = None,
    standard_local: bool = False,
) -> RuntimeFoundation:
    """Compose the selected local or strict Fleet execution profile.

    Strict deployments enroll Runs through their independently verified
    provisioner. Ordinary local execution uses the same Fleet lifecycle with
    host process authority and makes no isolation or custody claim.
    """
    from oh_goon.core.enforcer_lace_trust import TrustProtocolError

    if fleet_provisioner is not None and type(fleet_provisioner) is not FleetPreworkProvisioner:
        raise TrustProtocolError(
            "production Fleet provisioner must be an explicit native authority"
        )
    if standard_local and fleet_provisioner is not None:
        raise TrustProtocolError("standard host execution cannot use deployment Fleet enrollment")
    # Repository evidence has one required producer in every execution profile.
    # Reject an unavailable runtime before opening state or recovering effects.
    require_loaded_hashmarks_runtime()
    local = _build_local_state_foundation()
    database = local.database
    lifecycle = RuntimeLifecycle()
    effect_attempts = RepositoryEffectAttemptStore(database)
    schedule_definitions = ScheduleDefinitionStore(database)
    schedule_executions = ScheduleExecutionStore(database)
    git = GitService()
    effect_settlement = RepositoryEffectSettlement(database, local.journal, effect_attempts)
    effect_reconciler = RepositoryReconciler(git)
    effect_recovery, auditor, runtime_safety = _prove_startup_safety(
        local.state,
        local.execution,
        effect_attempts,
        schedule_executions,
        effect_reconciler,
        effect_settlement,
    )
    state_paths = ApplicationStatePathLayout.from_database(database.path)
    diagnostics = DiagnosticRouter(
        (
            StderrDiagnosticSink(),
            JsonlDiagnosticSink(state_paths.diagnostics_log),
        )
    )
    database.set_recovery_observer(
        lambda event: diagnostics.emit(
            DiagnosticEvent(
                severity="WARNING" if event.outcome == "failed" else "INFO",
                source="sqlite_recovery",
                owner="LocalDatabase",
                operation=event.action,
                message=(
                    f"recovery kind={event.kind} reason={event.reason_code} outcome={event.outcome}"
                ),
                execution_affected=event.outcome == "failed",
                failure_class="execution_recovery",
            )
        )
    )
    supervisor = RunExecutionSupervisor(diagnostics=diagnostics)
    configuration = configured_executor_pool(control_state_root=state_paths.executor_control)
    path_authority = configuration.path_authority if configuration is not None else None
    extension_store = FleetExtensionStore(state_paths.extensions, path_authority=path_authority)
    tool_store = FleetToolStore(state_paths.plan_tools, path_authority=path_authority)
    live_output = GameTapeLiveOutputStore()
    executor_pool, capability_executor, content_store = _build_capability_runtime(
        database,
        git,
        extension_store,
        tool_store,
        configuration=configuration,
        content_store_root=state_paths.content_store,
        run_parent=state_paths.run_workspaces,
        live_output=live_output,
        diagnostics=diagnostics,
    )
    repository_effects = RepositoryEffectExecution(
        capability_executor,
        local.journal,
        effect_attempts,
        git,
        effect_fence=RepositoryEffectFence(git),
        settlement=effect_settlement,
    )
    direct_operations = DirectEffectAdmission(
        local.journal,
        local.execution,
        effect_attempts,
        runtime_safety,
        repository_effects,
        git,
    )
    return RuntimeFoundation(
        database=database,
        state=local.state,
        execution=local.execution,
        lifecycle=lifecycle,
        schedule_definitions=schedule_definitions,
        schedule_executions=schedule_executions,
        effect_recovery=effect_recovery,
        auditor=auditor,
        runtime_safety=runtime_safety,
        supervisor=supervisor,
        executor_pool=executor_pool,
        content_store=content_store,
        repository_effects=repository_effects,
        direct_operations=direct_operations,
        extension_store=extension_store,
        tool_store=tool_store,
        live_output=live_output,
        diagnostics=diagnostics,
        fleet_provisioner=fleet_provisioner,
        require_authenticated_fleet=not standard_local,
        require_compiled_fleet_contract=not standard_local,
        standard_local_fleet=standard_local,
    )
