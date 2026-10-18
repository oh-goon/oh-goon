from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import Field, field_validator

from oh_goon.core.models.base import ImmutableModel

_PLAN_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class PlanFileSource(ImmutableModel):
    """Explicit standalone plan file invoked outside Team/League-plan convenience flows."""

    kind: Literal["plan_file"] = "plan_file"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TeamGoonSource(ImmutableModel):
    """Repository-owned root goon.yml; the owning Team is the run's sole target."""

    kind: Literal["team_goon"] = "team_goon"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SharedPlanSource(ImmutableModel):
    """oh-goon-owned goon.yml applied to an explicit set of League Teams."""

    kind: Literal["shared_plan"] = "shared_plan"
    plan_key: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("plan_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        candidate = value
        if not _PLAN_KEY.fullmatch(candidate):
            raise ValueError("Shared Plan names use only lowercase letters, numbers, and '-'")
        return candidate


FleetPlanSource = Annotated[
    PlanFileSource | TeamGoonSource | SharedPlanSource,
    Field(discriminator="kind"),
]


def validate_plan_source_repository_count(source: FleetPlanSource, repository_count: int) -> None:
    if isinstance(source, TeamGoonSource) and repository_count != 1:
        raise ValueError("Team Goon source requires exactly one repository")
