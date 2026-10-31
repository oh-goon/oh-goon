from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.application import ApplicationRuntime

from oh_goon._application_foundation import RuntimeFoundation, _build_local_state_foundation
from oh_goon.integrations.gitlab_ci_component import GitLabComponentFetcher
from oh_goon.integrations.gitlab_ci_goon import GitLabCiGoonMigrator
from oh_goon.integrations.hashmarks.runtime_contract import require_loaded_hashmarks_runtime
from oh_goon.runtime.dependency_observations import DependencyObservationStore
from oh_goon.runtime.dependency_targets import DependencyTargetMaterializationStore
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.league_lineups import LeagueLineupFileStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.runtime.schedule_scheduler import ScheduleScheduler
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.scout.service import ScoutService, scout_enabled_from_environment
from oh_goon.services.command_center import CommandCenterService
from oh_goon.services.dependencies import DependencyFleetService
from oh_goon.services.filesystem import FilesystemService
from oh_goon.services.fleet import FleetService
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from oh_goon.services.goon import GoonService
from oh_goon.services.journal_diagnostics import JournalDiagnosticsService
from oh_goon.services.league import LeagueService
from oh_goon.services.league_lineups import LeagueLineupService
from oh_goon.services.locations import LocationService
from oh_goon.services.migration import MigrationService
from oh_goon.services.recovery import RecoveryService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.schedule_admission import ScheduleAdmissionPlanner
from oh_goon.services.schedule_execution import ScheduleExecutionEngine
from oh_goon.services.schedules import ScheduleService
from oh_goon.services.security import SecurityService
from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService
from oh_goon.services.shared_plans import SharedPlanService
from oh_goon.services.system_health import SystemHealthService
from oh_goon.services.target_selection import FleetTargetSelector


def build_registration_service() -> LocationService:
    """Compose explicit scope mutation without application recovery or workers."""
    require_loaded_hashmarks_runtime()
    return LocationService(_build_local_state_foundation(startup_purpose="workflow").state)


def _build_fleet_services(
    foundation: RuntimeFoundation,
    repositories: RepositoryService,
    migration: MigrationService,
    league: LeagueService,
) -> FleetRunControlPlane:
    # Production cannot silently reenable the historic Fleet reservation route,
    # even when a manually constructed RuntimeFoundation is supplied here.
    from oh_goon.core.enforcer_lace_trust import TrustProtocolError
    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner

    standard = foundation.standard_local_fleet is True
    if standard:
        if (
            foundation.require_authenticated_fleet is not False
            or foundation.require_compiled_fleet_contract is not False
            or foundation.fleet_prework is not None
            or foundation.fleet_provisioner is not None
        ):
            raise TrustProtocolError("standard-local mode cannot inherit native Fleet enrollment")
    elif (
        foundation.require_authenticated_fleet is not True
        or foundation.require_compiled_fleet_contract is not True
        or foundation.fleet_prework is not None
        or (
            foundation.fleet_provisioner is not None
            and type(foundation.fleet_provisioner) is not FleetPreworkProvisioner
        )
    ):
        raise TrustProtocolError("production Fleet requires authenticated admission")
    admission = FleetAdmissionPlanner(
        foundation.state,
        FleetTargetSelector(repositories, migration, league),
        foundation.auditor,
        foundation.runtime_safety,
    )
    lifecycle = FleetRunLifecycle(
        foundation.database,
        foundation.state.journal,
        foundation.execution,
        foundation.executor_pool,
        foundation.content_store,
        require_authenticated_fleet=foundation.require_authenticated_fleet,
        require_compiled_fleet_contract=foundation.require_compiled_fleet_contract,
        fleet_prework=foundation.fleet_prework,
        fleet_provisioner=foundation.fleet_provisioner,
        standard_local_fleet=standard,
        run_resource_scope=foundation.run_resource_scope,
        run_resource_envelope=foundation.run_resource_envelope,
    )
    execution = FleetRunExecution(
        lifecycle,
        FleetTargetScheduler(foundation.repository_effects),
        foundation.live_output,
        foundation.diagnostics,
    )
    return FleetRunControlPlane(
        admission,
        foundation.state.journal,
        lifecycle,
        execution,
        foundation.supervisor,
        foundation.live_output,
        foundation.diagnostics,
        schedule_executions=foundation.schedule_executions,
        content_store=foundation.content_store,
    )


def _build_schedule_services(
    foundation: RuntimeFoundation,
    runs: FleetRunControlPlane,
) -> tuple[ScheduleService, ScheduleScheduler]:
    engine = ScheduleExecutionEngine(foundation.schedule_executions, runs)
    admission = ScheduleAdmissionPlanner(
        foundation.database,
        foundation.schedule_definitions,
        foundation.schedule_executions,
        foundation.auditor,
        foundation.runtime_safety,
    )
    schedules = ScheduleService(
        foundation.database,
        foundation.schedule_definitions,
        foundation.schedule_executions,
        engine,
        admission,
        foundation.runtime_safety,
    )
    return schedules, ScheduleScheduler(schedules.tick, diagnostics=foundation.diagnostics)


def build_product_services(foundation: RuntimeFoundation) -> ApplicationRuntime:
    """Compose product services from the already-proven runtime foundation."""
    filesystem = FilesystemService()
    locations = LocationService(foundation.state)
    discovery = RepositoryDiscoveryService()
    league = LeagueService(foundation.state, discovery)
    repositories = RepositoryService(foundation.state, discovery)
    migration = MigrationService(foundation.state, discovery)
    runs = _build_fleet_services(foundation, repositories, migration, league)
    schedules, schedule_scheduler = _build_schedule_services(foundation, runs)
    goon_templates = GoonTemplateStore()
    plan_templates = PlanTemplateStore()
    plan_resolver = FleetPlanResolver(
        foundation.extension_store,
        foundation.tool_store,
        goon_templates,
        git_service=foundation.git_service,
    )
    plans = FleetPlanService(
        plan_resolver,
        runs,
        schedules,
        foundation.extension_store,
        foundation.tool_store,
        content_store=foundation.content_store,
        standard_local_fleet=foundation.standard_local_fleet,
        terminal=foundation.terminal,
        standard_process_isolation=foundation.standard_process_isolation,
        git_service=foundation.git_service,
    )
    shared_plan_store = (
        SharedPlanFileStore(initialize=False)
        if foundation.startup_purpose == "observation"
        else SharedPlanFileStore()
    )
    shared_plan = SharedPlanService(shared_plan_store, league, plans, runs, plan_templates)
    shared_plan_schedules = SharedPlanScheduleService(shared_plan_store, league, plans, schedules)
    system_health = SystemHealthService(
        foundation.runtime_safety,
        lambda: [
            failure
            for failure in (
                foundation.supervisor.snapshot().last_failure,
                schedule_scheduler.snapshot().last_failure,
            )
            if failure is not None
        ],
    )
    # Import locally to keep the public composition root free of a wiring-only mirror DTO.
    # The builder now creates the one runtime representation directly.
    from oh_goon.application import ApplicationRuntime

    return ApplicationRuntime(
        operations=foundation.direct_operations,
        filesystem=filesystem,
        locations=locations,
        league=league,
        repositories=repositories,
        dependencies=DependencyFleetService(
            repositories,
            DependencyObservationStore(foundation.database, foundation.state.journal),
            DependencyTargetMaterializationStore(foundation.database),
        ),
        migration=migration,
        command_center=CommandCenterService(repositories, foundation.state),
        security=SecurityService(repositories, foundation.state),
        fleet=FleetService(repositories, foundation.state),
        runs=runs,
        executor_pool=foundation.executor_pool,
        diagnostics=foundation.diagnostics,
        schedules=schedules,
        system_health=system_health,
        recovery=RecoveryService(foundation.effect_recovery, foundation.runtime_safety),
        journal_diagnostics=JournalDiagnosticsService(foundation.database),
        plans=plans,
        goon=GoonService(league, plans, GitLabCiGoonMigrator(GitLabComponentFetcher())),
        shared_plan=shared_plan,
        shared_plan_schedules=shared_plan_schedules,
        lineups=LeagueLineupService(
            LeagueLineupFileStore(initialize=False)
            if foundation.startup_purpose == "observation"
            else LeagueLineupFileStore(),
            league,
        ),
        scout=ScoutService(
            foundation.state.journal,
            database=foundation.database,
            enabled=scout_enabled_from_environment(),
        ),
        _lifecycle=foundation.lifecycle,
        _schedule_scheduler=schedule_scheduler,
        _startup_purpose=foundation.startup_purpose,
    )
