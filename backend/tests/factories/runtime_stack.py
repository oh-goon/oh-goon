from __future__ import annotations

from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, RunExecutionSupervisor
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.schedule_executions import ScheduleExecutionStore
from oh_goon.runtime.state import LocalState
from oh_goon.services.capability_executor import CapabilityExecutor
from oh_goon.services.direct_effect_admission import DirectEffectAdmission
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.fleet import FleetService
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from oh_goon.services.invariant_auditor import SystemInvariantAuditor
from oh_goon.services.league import LeagueService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.runtime_safety import RuntimeSafetyService
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.repository_effect_runtime import effect_execution
from tests.fakes.capability_runners import CommandRunnerStub, fixed_capability_runners


def execution_stack(database: LocalDatabase, journal: Journal, runner: object):
    execution = ExecutionCoordinator(database)
    adapter = CommandRunnerStub(runner)
    adapters = fixed_capability_runners(adapter)
    executor = CapabilityExecutor(adapters)
    return execution, executor


def direct_operation_stack(
    database: LocalDatabase,
    journal: Journal,
    adapter: object,
    *,
    faults: object | None = None,
    effect_fence: object | None = None,
    runtime_safety: object | None = None,
):
    execution = ExecutionCoordinator(database)
    attempts = RepositoryEffectAttemptStore(database)
    executor = CapabilityExecutor(
        fixed_capability_runners(adapter),  # type: ignore[arg-type]
        faults=faults,  # type: ignore[arg-type]
    )
    safety = runtime_safety
    if safety is None:
        schedules = ScheduleExecutionStore(database)
        auditor = SystemInvariantAuditor(database, journal, execution, attempts, schedules)
        safety = RuntimeSafetyService(auditor, RuntimeSafetyGate())
        safety.refresh()
    git = GitService()
    effects = effect_execution(
        database,
        executor,
        journal,
        attempts,
        git,
        faults=faults,
        effect_fence=effect_fence,  # type: ignore[arg-type]
    )
    operations = DirectEffectAdmission(
        journal,
        execution,
        attempts,
        safety,  # type: ignore[arg-type]
        effects,
        git,
    )
    return operations, attempts, execution, executor


def fleet_stack(state: LocalState, runner: object, *, faults: object | None = None):
    repositories = RepositoryService(state, RepositoryDiscoveryService())
    execution = ExecutionCoordinator(state.database)
    adapter = CommandRunnerStub(runner)
    executor = CapabilityExecutor(
        fixed_capability_runners(adapter),
        faults=faults,  # type: ignore[arg-type]
    )
    migration = MigrationService(state, RepositoryDiscoveryService())
    fleet = FleetService(repositories, state)
    attempts = RepositoryEffectAttemptStore(state.database)
    schedules = ScheduleExecutionStore(state.database)
    auditor = SystemInvariantAuditor(state.database, state.journal, execution, attempts, schedules)
    safety = RuntimeSafetyService(auditor, RuntimeSafetyGate())
    safety.refresh()
    git = GitService()
    effects = effect_execution(
        state.database,
        executor,
        state.journal,
        attempts,
        git,
        faults=faults,
    )
    league = LeagueService(state, RepositoryDiscoveryService())
    admission = FleetAdmissionPlanner(
        state, FleetTargetSelector(repositories, migration, league), auditor, safety
    )
    executor_pool = FleetExecutorPool(state.database, None)
    lifecycle = FleetRunLifecycle(state.database, state.journal, execution, executor_pool)
    live_output = GameTapeLiveOutputStore()
    run_execution = FleetRunExecution(lifecycle, FleetTargetScheduler(effects), live_output)
    manager = FleetRunControlPlane(
        admission,
        state.journal,
        lifecycle,
        run_execution,
        RunExecutionSupervisor(),
        live_output,
        schedule_executions=schedules,
    )
    return fleet, manager, executor, execution
