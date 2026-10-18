from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from oh_goon.core.errors import RuntimeQuiescing
from oh_goon.runtime.lifecycle import RuntimeLifecycleState

if TYPE_CHECKING:
    from oh_goon.runtime.diagnostics import DiagnosticRouter
    from oh_goon.runtime.executor_pool import FleetExecutorPool
    from oh_goon.runtime.lifecycle import RuntimeLifecycle
    from oh_goon.runtime.schedule_scheduler import ScheduleScheduler
    from oh_goon.scout.service import ScoutService
    from oh_goon.services.command_center import CommandCenterService
    from oh_goon.services.dependencies import DependencyFleetService
    from oh_goon.services.direct_effect_admission import DirectEffectAdmission
    from oh_goon.services.filesystem import FilesystemService
    from oh_goon.services.fleet import FleetService
    from oh_goon.services.fleet_plans import FleetPlanService
    from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
    from oh_goon.services.goon import GoonService
    from oh_goon.services.journal_diagnostics import JournalDiagnosticsService
    from oh_goon.services.league import LeagueService
    from oh_goon.services.league_lineups import LeagueLineupService
    from oh_goon.services.locations import LocationService
    from oh_goon.services.migration import MigrationService
    from oh_goon.services.recovery import RecoveryService
    from oh_goon.services.repositories import RepositoryService
    from oh_goon.services.schedules import ScheduleService
    from oh_goon.services.security import SecurityService
    from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService
    from oh_goon.services.shared_plans import SharedPlanService
    from oh_goon.services.system_health import SystemHealthService


@dataclass(frozen=True, slots=True)
class ApplicationRuntime:
    """Public process composition/lifecycle root exposing product boundaries only."""

    operations: DirectEffectAdmission
    filesystem: FilesystemService
    locations: LocationService
    league: LeagueService
    repositories: RepositoryService
    dependencies: DependencyFleetService
    migration: MigrationService
    command_center: CommandCenterService
    security: SecurityService
    fleet: FleetService
    runs: FleetRunControlPlane
    executor_pool: FleetExecutorPool
    diagnostics: DiagnosticRouter
    system_health: SystemHealthService
    schedules: ScheduleService
    recovery: RecoveryService
    journal_diagnostics: JournalDiagnosticsService
    plans: FleetPlanService
    goon: GoonService
    shared_plan: SharedPlanService
    shared_plan_schedules: SharedPlanScheduleService
    lineups: LeagueLineupService
    scout: ScoutService
    _lifecycle: RuntimeLifecycle
    _schedule_scheduler: ScheduleScheduler

    @classmethod
    def build(
        cls,
        *,
        fleet_provisioner: FleetPreworkProvisioner | None = None,
        standard_local: bool | None = None,
    ) -> ApplicationRuntime:
        # Concrete dependencies remain statically visible in the private application wiring
        # modules, but are materialized only when a process runtime is actually built.
        from oh_goon._application_foundation import build_runtime_foundation, standard_local_profile
        from oh_goon._application_services import build_product_services

        return build_product_services(
            build_runtime_foundation(
                fleet_provisioner=fleet_provisioner,
                standard_local=standard_local_profile(
                    standard_local, deployment_selected=fleet_provisioner is not None
                ),
            )
        )

    def start_background(self) -> None:
        state = self._lifecycle.mark_running()
        if state is not RuntimeLifecycleState.RUNNING:
            raise RuntimeQuiescing(f"Runtime cannot restart background work from {state.value}")
        self._schedule_scheduler.start()

    def shutdown(self, *, wait_for_runs: bool = False) -> None:
        if not self._lifecycle.begin_quiesce():
            return
        self._schedule_scheduler.stop()
        self.runs.quiesce()
        if not wait_for_runs:
            self.runs.cancel_active_for_shutdown()
        self.runs.shutdown(wait=True)
        self._lifecycle.close()
