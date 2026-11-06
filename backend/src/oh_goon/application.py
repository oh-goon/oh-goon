from __future__ import annotations

from dataclasses import dataclass, replace
from time import monotonic
from typing import TYPE_CHECKING

from oh_goon.core.errors import RuntimeQuiescing
from oh_goon.runtime.lifecycle import RuntimeDrainPolicy, RuntimeLifecycleState

if TYPE_CHECKING:
    from oh_goon._application_foundation import RuntimeStartupPurpose
    from oh_goon.core.models.run_resources import RunResourceEnvelopeEvidence
    from oh_goon.engine.command import CommandTerminal
    from oh_goon.engine.git import GitService
    from oh_goon.integrations.fleet_plan_process_isolation import ProcessIsolationBackend
    from oh_goon.integrations.run_resource_envelope import ResolvedRunResourceScope
    from oh_goon.runtime.diagnostics import DiagnosticRouter
    from oh_goon.runtime.executor_pool import FleetExecutorPool
    from oh_goon.runtime.lifecycle import RuntimeLifecycle
    from oh_goon.runtime.schedule_scheduler import ScheduleScheduler
    from oh_goon.scout.service import ScoutService
    from oh_goon.services.catalog_showcase import CatalogShowcaseService
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
    catalog_showcase: CatalogShowcaseService
    _lifecycle: RuntimeLifecycle
    _schedule_scheduler: ScheduleScheduler
    _startup_purpose: RuntimeStartupPurpose = "application"

    @classmethod
    def build_registration_service(cls) -> LocationService:
        """Use the original LocalState guard without starting application recovery."""
        from oh_goon._application_services import build_registration_service

        return build_registration_service()

    @classmethod
    def build(
        cls,
        *,
        fleet_provisioner: FleetPreworkProvisioner | None = None,
        standard_local: bool | None = None,
        terminal: CommandTerminal | None = None,
        standard_process_isolation: ProcessIsolationBackend | None = None,
        git_service: GitService | None = None,
        startup_purpose: RuntimeStartupPurpose = "application",
        run_resource_scope: ResolvedRunResourceScope | None = None,
        run_resource_envelope: RunResourceEnvelopeEvidence | None = None,
    ) -> ApplicationRuntime:
        # Concrete dependencies remain statically visible in the private application wiring
        # modules, but are materialized only when a process runtime is actually built.
        from oh_goon._application_foundation import build_runtime_foundation, standard_local_profile
        from oh_goon._application_services import build_product_services

        return build_product_services(
            build_runtime_foundation(
                fleet_provisioner=fleet_provisioner,
                terminal=terminal,
                standard_process_isolation=standard_process_isolation,
                git_service=git_service,
                startup_purpose=startup_purpose,
                run_resource_scope=run_resource_scope,
                run_resource_envelope=run_resource_envelope,
                standard_local=standard_local_profile(
                    standard_local, deployment_selected=fleet_provisioner is not None
                ),
            )
        )

    def start_background(self) -> None:
        if self._startup_purpose != "application":
            raise RuntimeQuiescing("Selected startup purpose cannot start background work")
        if self._lifecycle.state in {RuntimeLifecycleState.QUIESCING, RuntimeLifecycleState.CLOSED}:
            raise RuntimeQuiescing("Runtime is quiescing or closed")
        with self._lifecycle.shutdown_scope(timeout=0):
            state = self._lifecycle.mark_running()
            if state is not RuntimeLifecycleState.RUNNING:
                raise RuntimeQuiescing(f"Runtime cannot restart background work from {state.value}")
            self._schedule_scheduler.start()

    def shutdown(
        self,
        *,
        wait_for_runs: bool = True,
        drain_timeout_seconds: float | None = 30.0,
        cancel_grace_seconds: float = 5.0,
        termination_grace_seconds: float = 1.0,
        containment_timeout_seconds: float = 10.0,
    ) -> None:
        if type(wait_for_runs) is not bool:
            raise ValueError("Runtime shutdown wait selection must be boolean")
        policy = RuntimeDrainPolicy(
            drain_seconds=drain_timeout_seconds,
            cancel_grace_seconds=cancel_grace_seconds,
            termination_grace_seconds=termination_grace_seconds,
            containment_timeout_seconds=containment_timeout_seconds,
        )
        if not wait_for_runs:
            policy = replace(policy, drain_seconds=0.0)
        with self._lifecycle.shutdown_scope(timeout=policy.join_timeout_seconds):
            if self._lifecycle.state is RuntimeLifecycleState.CLOSED:
                return
            self._lifecycle.begin_quiesce()
            # Fence final admission before joining an in-flight scheduler tick; its
            # existing placement wait can now refuse promptly and retire its queue.
            self.runs.quiesce()
            scheduler_error: BaseException | None = None
            try:
                self._schedule_scheduler.stop()
            except BaseException as exc:
                scheduler_error = exc
            try:
                contained = self.runs.shutdown(wait=True, policy=policy)
                for run_id in contained.run_ids:
                    self.recovery.recover_worker_exit(
                        run_id,
                        quiescence_timeout_seconds=max(
                            0.0, contained.containment_deadline - monotonic()
                        ),
                    )
            except BaseException as exc:
                if scheduler_error is not None:
                    scheduler_error.add_note(
                        f"Run drain/containment also failed: {type(exc).__name__}"
                    )
                    raise scheduler_error from None
                raise
            if scheduler_error is not None:
                raise scheduler_error
            self._lifecycle.close()
