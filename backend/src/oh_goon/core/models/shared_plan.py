from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_composition import PlanInputDefinition, PlanInputValue
from oh_goon.core.models.fleet_plan_evidence import FleetPlanPreview
from oh_goon.core.models.operator_diagnostic import OperatorWhyNotDiagnostic
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.product_identity import stable_id
from oh_goon.core.template_identity import TemplateClassification
from oh_goon.core.vocabulary import FleetRunStatus


class SharedPlanLastRun(ImmutableModel):
    fleet_run_id: str = Field(min_length=1)
    accepted_at: AwareDatetime
    status: FleetRunStatus
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    team_count: int = Field(ge=1)


class SharedPlanSummary(ImmutableModel):
    plan_key: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=1)
    last_run: SharedPlanLastRun | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def shared_plan_id(self) -> str:
        return stable_id("shared-plan", {"plan_key": self.plan_key})

    @computed_field  # type: ignore[prop-decorator]
    @property
    def changed_since_last_run(self) -> bool:
        return self.last_run is not None and self.last_run.source_sha256 != self.sha256


class SharedPlanDocument(SharedPlanSummary):
    content: str = Field(min_length=1)
    inputs: tuple[PlanInputDefinition, ...] = Field(default_factory=tuple, max_length=32)


class SharedPlanCatalog(ImmutableModel):
    plans: tuple[SharedPlanSummary, ...] = Field(default_factory=tuple)


class SharedPlanRunHistory(ImmutableModel):
    plan_key: str = Field(min_length=1, max_length=64)
    current_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    runs: tuple[SharedPlanLastRun, ...] = Field(default_factory=tuple)
    next_before_revision: int | None = Field(default=None, ge=1)


class GoonTemplateSummary(ImmutableModel):
    template_key: str = Field(min_length=1, max_length=64)
    classification: TemplateClassification
    description: str = Field(min_length=1, max_length=1000)
    template_path: str = Field(min_length=1, max_length=300)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoonTemplateDocument(GoonTemplateSummary):
    content: str = Field(min_length=1)


class GoonTemplateCatalog(ImmutableModel):
    templates: tuple[GoonTemplateSummary, ...] = Field(default_factory=tuple)


class ReadySharedPlanRepositoryPreview(ImmutableModel):
    state: Literal["READY"] = "READY"
    repository: RepositoryIdentity
    repository_instance_id: str = Field(min_length=1)
    preview: FleetPlanPreview


class BlockedSharedPlanRepositoryPreview(ImmutableModel):
    state: Literal["BLOCKED"] = "BLOCKED"
    repository: RepositoryIdentity
    repository_instance_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)
    diagnostic: OperatorWhyNotDiagnostic


SharedPlanRepositoryPreview = Annotated[
    ReadySharedPlanRepositoryPreview | BlockedSharedPlanRepositoryPreview,
    Field(discriminator="state"),
]


class SharedPlanLockPreview(ImmutableModel):
    plan_key: str = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    content: str = Field(min_length=1)


class SharedPlanExplicitInput(ImmutableModel):
    """One ordinary non-secret value explicitly supplied for this preview."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    value: PlanInputValue = None


class SharedPlanPreview(ImmutableModel):
    plan_key: str = Field(min_length=1, max_length=64)
    plan_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mode: Literal["fresh"] = "fresh"
    explicit_inputs: tuple[SharedPlanExplicitInput, ...] = Field(default_factory=tuple)
    readiness: tuple[OperatorWhyNotDiagnostic, ...] = Field(default_factory=tuple)
    teams: tuple[SharedPlanRepositoryPreview, ...] = Field(min_length=1)
    ready_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> SharedPlanPreview:
        ready = sum(team.state == "READY" for team in self.teams)
        if self.ready_count != ready or self.blocked_count != len(self.teams) - ready:
            raise ValueError("Shared Plan preview counts must match Team preview states")
        if any(
            team.preview.plan_id != self.plan_id
            for team in self.teams
            if isinstance(team, ReadySharedPlanRepositoryPreview)
        ):
            raise ValueError(
                "Shared Plan repository previews must share the resolved plan identity"
            )
        return self


class LeagueLineupSummary(ImmutableModel):
    lineup_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    team_count: int = Field(ge=1)


class LeagueLineupDocument(LeagueLineupSummary):
    revision: str = Field(min_length=64, max_length=64)
    repositories: tuple[Path, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_team_count(self) -> LeagueLineupDocument:
        if self.team_count != len(self.repositories):
            raise ValueError("League Lineup team_count must match repositories")
        return self


class LeagueLineupCatalog(ImmutableModel):
    lineups: tuple[LeagueLineupSummary, ...] = Field(default_factory=tuple)


class ReadyLeagueLineupTeam(ImmutableModel):
    state: Literal["READY"] = "READY"
    configured_path: Path
    repository: RepositoryIdentity


class UnavailableLeagueLineupTeam(ImmutableModel):
    state: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    configured_path: Path
    reason: Literal["NOT_MANAGED_TEAM"] = "NOT_MANAGED_TEAM"


LeagueLineupTeam = Annotated[
    ReadyLeagueLineupTeam | UnavailableLeagueLineupTeam,
    Field(discriminator="state"),
]


class LeagueLineupInspection(ImmutableModel):
    lineup_key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    revision: str = Field(min_length=64, max_length=64)
    teams: tuple[LeagueLineupTeam, ...] = Field(min_length=1)
    ready_count: int = Field(ge=0)
    unavailable_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> LeagueLineupInspection:
        ready = sum(team.state == "READY" for team in self.teams)
        if self.ready_count != ready or self.unavailable_count != len(self.teams) - ready:
            raise ValueError("League Lineup inspection counts must match Team states")
        return self
