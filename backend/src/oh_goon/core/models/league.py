from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.repository import RepositoryIdentity


class ConfiguredTeamPipeline(ImmutableModel):
    state: Literal["CONFIGURED"]
    path: Literal["goon.yml"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)


class MissingTeamPipeline(ImmutableModel):
    state: Literal["MISSING"]
    path: Literal["goon.yml"]


class InvalidTeamPipeline(ImmutableModel):
    state: Literal["INVALID"]
    path: Literal["goon.yml"]
    reason: Literal[
        "ENTRYPOINT_SYMLINK",
        "ENTRYPOINT_NOT_FILE",
        "ENTRYPOINT_UNREADABLE",
        "ENTRYPOINT_CHANGED_DURING_SCAN",
    ]


TeamPipeline = Annotated[
    ConfiguredTeamPipeline | MissingTeamPipeline | InvalidTeamPipeline,
    Field(discriminator="state"),
]


class LeagueTeam(ImmutableModel):
    repository: RepositoryIdentity
    repository_instance_id: str = Field(min_length=1)
    pipeline: TeamPipeline


class LeagueScanSummary(ImmutableModel):
    folders: int = Field(ge=0)
    git_repositories: int = Field(ge=0)
    configured_teams: int = Field(ge=0)
    missing_goon_yml: int = Field(ge=0)
    invalid_goon_yml: int = Field(ge=0)


class LeagueMap(ImmutableModel):
    folders: tuple[Path, ...] = Field(default_factory=tuple)
    teams: tuple[LeagueTeam, ...] = Field(default_factory=tuple)
    summary: LeagueScanSummary
