from functools import lru_cache

from oh_goon.application import ApplicationRuntime
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.services.command_center import CommandCenterService
from oh_goon.services.dependencies import DependencyFleetService
from oh_goon.services.filesystem import FilesystemService
from oh_goon.services.fleet import FleetService
from oh_goon.services.goon import GoonService
from oh_goon.services.league import LeagueService
from oh_goon.services.league_lineups import LeagueLineupService
from oh_goon.services.locations import LocationService
from oh_goon.services.migration import MigrationService
from oh_goon.services.recovery import RecoveryService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
from oh_goon.services.schedules import ScheduleService
from oh_goon.scout.service import ScoutService
from oh_goon.services.security import SecurityService
from oh_goon.services.shared_plan_schedules import SharedPlanScheduleService
from oh_goon.services.shared_plans import SharedPlanService
from oh_goon.services.system_health import SystemHealthService


@lru_cache
def application() -> ApplicationRuntime:
    return ApplicationRuntime.build()


def filesystem_service() -> FilesystemService:
    return application().filesystem


def location_service() -> LocationService:
    return application().locations


def league_service() -> LeagueService:
    return application().league


def goon_service() -> GoonService:
    return application().goon


def shared_plan_service() -> SharedPlanService:
    return application().shared_plan


def league_lineup_service() -> LeagueLineupService:
    return application().lineups


def repository_service() -> RepositoryService:
    return application().repositories


def dependency_fleet_service() -> DependencyFleetService:
    return application().dependencies


def migration_service() -> MigrationService:
    return application().migration


def command_center_service() -> CommandCenterService:
    return application().command_center


def security_service() -> SecurityService:
    return application().security


def fleet_service() -> FleetService:
    return application().fleet


def fleet_run_control_plane() -> FleetRunControlPlane:
    return application().runs


def executor_pool() -> FleetExecutorPool:
    return application().executor_pool


def system_health_service() -> SystemHealthService:
    return application().system_health


def schedule_service() -> ScheduleService:
    return application().schedules


def shared_plan_schedule_service() -> SharedPlanScheduleService:
    return application().shared_plan_schedules


def recovery_service() -> RecoveryService:
    return application().recovery


def scout_service() -> ScoutService:
    return application().scout
