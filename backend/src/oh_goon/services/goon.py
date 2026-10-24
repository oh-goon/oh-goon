from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.fleet_plan_evidence import FleetPlanPreview
from oh_goon.core.models.fleet_plan_migration import GitLabGoonMigration
from oh_goon.core.models.goon_plan_source import TeamGoonSource
from oh_goon.core.models.league import ConfiguredTeamPipeline, LeagueTeam
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.team_pipeline import team_pipeline_path
from oh_goon.integrations.gitlab_ci_goon import GitLabCiGoonMigrator
from oh_goon.services.fleet_plans import FleetPlanService
from oh_goon.services.league import LeagueService


@dataclass(frozen=True, slots=True)
class GoonInspection:
    """One Team inspected through the exact repository-owned CI/CD entrypoint."""

    team: LeagueTeam
    preview: FleetPlanPreview


class GoonService:
    """Select a managed Team's goon.yml; canonical FleetPlanService does the rest."""

    def __init__(
        self,
        league: LeagueService,
        plans: FleetPlanService,
        migrator: GitLabCiGoonMigrator,
    ) -> None:
        self._league = league
        self._plans = plans
        self._migrator = migrator

    def inspect(
        self,
        repository: Path,
        *,
        inputs: Mapping[str, PlanInputValue] | None = None,
    ) -> GoonInspection:
        team = self._league.configured_team(repository)
        preview = self._plans.preview(
            team_pipeline_path(team.repository.path),
            team.repository.path,
            inputs=dict(inputs) if inputs is not None else None,
        )
        return GoonInspection(team=team, preview=preview)

    def run(
        self,
        repository: Path,
        *,
        expected_pipeline_sha256: str,
        expected_repository_instance_id: str | None = None,
        inputs: Mapping[str, PlanInputValue] | None = None,
        fleet_run_id: str | None = None,
        requested_priority: SchedulingPriority = SchedulingPriority.NORMAL,
    ) -> FleetRunState:
        # M181: the Team lookup can read repository-owned goon.yml. Require
        # exact enrollment through the original Fleet lifecycle *before* that
        # first read. This membership fence is not final issuer authorization.
        self._plans.require_plan_ingress_preparation(fleet_run_id)
        team = self._league.configured_team(repository)
        assert isinstance(team.pipeline, ConfiguredTeamPipeline)
        if (
            expected_repository_instance_id is not None
            and team.repository_instance_id != expected_repository_instance_id
        ):
            raise StateConflict("Team repository instance changed since it was displayed")
        if team.pipeline.sha256 != expected_pipeline_sha256:
            raise StateConflict("Team pipeline changed since it was displayed")
        return self._plans.run(
            team_pipeline_path(team.repository.path),
            repository_ids={team.repository.repository_id},
            fleet_run_id=fleet_run_id,
            requested_priority=requested_priority,
            inputs=dict(inputs) if inputs is not None else None,
            source=TeamGoonSource(sha256=team.pipeline.sha256),
        )

    def import_gitlab(
        self,
        source: Path,
        *,
        output: Path | None = None,
        import_variables: dict[str, str] | None = None,
        token: str | None = None,
        expected_revision: str | None = None,
    ) -> GitLabGoonMigration:
        migration = self._migrator.migrate(source, import_variables=import_variables, token=token)
        if output is not None and migration.ready:
            self._migrator.write_goon(output, migration, expected_revision=expected_revision)
        return migration
