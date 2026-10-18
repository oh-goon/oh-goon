from __future__ import annotations

from oh_goon.core.errors import FleetPlanInvalid, FleetPlanSourceStale
from oh_goon.core.models.goon_plan_source import SharedPlanSource
from oh_goon.core.models.schedule import ScheduleCreate, ScheduleDefinition
from oh_goon.core.vocabulary import ScheduleState
from oh_goon.runtime.shared_plans import SharedPlanFileStore
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.league import LeagueService
from oh_goon.services.schedules import ScheduleService


class SharedPlanScheduleService:
    def __init__(
        self,
        store: SharedPlanFileStore,
        league: LeagueService,
        plans: FleetPlanService,
        schedules: ScheduleService,
    ) -> None:
        self._store = store
        self._league = league
        self._plans = plans
        self._schedules = schedules

    def create(
        self,
        schedule_id: str,
        *,
        name: str,
        repository_ids: tuple[str, ...],
        expected_repository_instance_ids: dict[str, str],
        plan_key: str,
        expected_plan_revision: str,
        expected_plan_id: str,
        expected_execution_digest: str,
        interval_minutes: int,
        state: ScheduleState = ScheduleState.ACTIVE,
        inputs: dict[str, str] | None = None,
    ) -> ScheduleDefinition:
        current_repositories = {item.repository_id: item for item in self._league.repositories()}
        available = set(current_repositories)
        missing = [item for item in repository_ids if item not in available]
        if missing:
            raise FleetPlanInvalid(
                f"Schedule repositories are not current managed League Teams: {', '.join(missing)}"
            )
        expected_ids = set(repository_ids)
        if set(expected_repository_instance_ids) != expected_ids:
            raise FleetPlanInvalid(
                "Schedule repository instance authority must exactly cover selected repositories"
            )
        stale_instances = [
            repository_id
            for repository_id in repository_ids
            if current_repositories[repository_id].repository_instance_id
            != expected_repository_instance_ids[repository_id]
        ]
        if stale_instances:
            raise FleetPlanSourceStale(
                "Schedule repository selection changed after review; reload the current League "
                f"before creating the Schedule: {', '.join(stale_instances)}"
            )
        document = self._store.read(plan_key)
        if document.sha256 != expected_plan_revision:
            raise FleetPlanSourceStale(
                "Shared Plan changed after Schedule review; reload the current Plan "
                "before creating the Schedule"
            )
        target = self._plans.prepare_reviewed_execution_target(
            self._store.path(document.plan_key),
            expected_execution_digest=expected_execution_digest,
            inputs=inputs,
            source=SharedPlanSource(plan_key=document.plan_key, sha256=document.sha256),
            expected_plan_id=expected_plan_id,
            root_expected_name=document.plan_key,
        )
        repository_instance_ids = self._plans.repository_instance_ids_for_target(
            target, repository_ids=set(repository_ids)
        )
        if repository_instance_ids != expected_repository_instance_ids:
            raise FleetPlanSourceStale(
                "Schedule repository selection changed during preparation; reload the current "
                "League before creating the Schedule"
            )
        return self._schedules.create_prepared(
            schedule_id,
            ScheduleCreate(
                name=name,
                repository_ids=tuple(sorted(repository_ids)),
                repository_instance_ids=repository_instance_ids,
                target=target,
                interval_minutes=interval_minutes,
                state=state,
            ),
        )
