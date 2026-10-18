from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from oh_goon.core.errors import LeagueTeamNotManaged, TeamPipelineNotConfigured
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.models.league import (
    ConfiguredTeamPipeline,
    InvalidTeamPipeline,
    LeagueMap,
    LeagueScanSummary,
    LeagueTeam,
    MissingTeamPipeline,
    TeamPipeline,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.team_pipeline import TEAM_PIPELINE_FILENAME, team_pipeline_path
from oh_goon.runtime.state import LocalState, LocalStateReadView
from oh_goon.services.repository_discovery import RepositoryDiscoveryService

_HASH_CHUNK_BYTES = 1024 * 1024


class LeagueService:
    """Project the local League from durable scan folders and current filesystem facts."""

    def __init__(self, state: LocalState, discovery: RepositoryDiscoveryService) -> None:
        self._state = state
        self._discovery = discovery

    def repositories(
        self, view: LocalStateReadView | None = None
    ) -> tuple[RepositoryIdentity, ...]:
        """Return current managed Git Team identities without inspecting repository goon.yml files."""
        captured = view or self._state.read_view()
        return tuple(
            RepositoryIdentity(path=repository)
            for repository in self._discovery.git_roots(captured.locations, require_pyproject=False)
        )

    def map(self, view: LocalStateReadView | None = None) -> LeagueMap:
        captured = view or self._state.read_view()
        repositories = tuple(item.path for item in self.repositories(captured))
        teams = tuple(
            LeagueTeam(
                repository=RepositoryIdentity(path=repository),
                repository_instance_id=repository_subject_instance_id(repository),
                pipeline=_inspect_team_pipeline(repository),
            )
            for repository in repositories
        )
        return LeagueMap(
            folders=captured.locations,
            teams=teams,
            summary=LeagueScanSummary(
                folders=len(captured.locations),
                git_repositories=len(teams),
                configured_teams=sum(team.pipeline.state == "CONFIGURED" for team in teams),
                missing_goon_yml=sum(team.pipeline.state == "MISSING" for team in teams),
                invalid_goon_yml=sum(team.pipeline.state == "INVALID" for team in teams),
            ),
        )

    def repository(
        self,
        repository: Path | str,
        view: LocalStateReadView | None = None,
    ) -> RepositoryIdentity:
        """Resolve one managed Git Team identity without inspecting repository goon.yml."""
        requested = Path(repository).expanduser().resolve()
        captured = view or self._state.read_view()
        root = self._discovery.containing_git_root(captured.locations, requested)
        if root is None:
            raise LeagueTeamNotManaged("Path is not a Git team inside a configured League folder")
        return RepositoryIdentity(path=root)

    def resolve_repositories(
        self, repositories: tuple[Path, ...]
    ) -> tuple[RepositoryIdentity, ...]:
        """Resolve explicit Team paths against one captured League state cut."""
        captured = self._state.read_view()
        return tuple(self.repository(repository, captured) for repository in repositories)

    def team(self, repository: Path | str) -> LeagueTeam:
        """Resolve one managed Git Team and project its repository-owned goon.yml status."""
        identity = self.repository(repository)
        return LeagueTeam(
            repository=identity,
            repository_instance_id=repository_subject_instance_id(identity.path),
            pipeline=_inspect_team_pipeline(identity.path),
        )

    def configured_team(self, repository: Path | str) -> LeagueTeam:
        """Resolve a managed Team and require its repository-owned goon.yml."""
        match = self.team(repository)
        if match.pipeline.state != "CONFIGURED":
            raise TeamPipelineNotConfigured(
                f"Team {match.repository.name} does not have a runnable {TEAM_PIPELINE_FILENAME}; state={match.pipeline.state}"
            )
        return match


def _inspect_team_pipeline(repository: Path) -> TeamPipeline:
    entrypoint = team_pipeline_path(repository)
    try:
        before = entrypoint.lstat()
    except FileNotFoundError:
        return MissingTeamPipeline(state="MISSING", path=TEAM_PIPELINE_FILENAME)
    except OSError:
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_UNREADABLE"
        )
    if stat.S_ISLNK(before.st_mode):
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_SYMLINK"
        )
    if not stat.S_ISREG(before.st_mode):
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_NOT_FILE"
        )

    digest = hashlib.sha256()
    byte_count = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(entrypoint, flags)
    except OSError:
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_UNREADABLE"
        )
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return InvalidTeamPipeline(
                state="INVALID",
                path=TEAM_PIPELINE_FILENAME,
                reason="ENTRYPOINT_CHANGED_DURING_SCAN",
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            while chunk := stream.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
                byte_count += len(chunk)
        after = entrypoint.lstat()
    except OSError:
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_UNREADABLE"
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_CHANGED_DURING_SCAN"
        )
    if (
        (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        or after.st_size != byte_count
    ):
        return InvalidTeamPipeline(
            state="INVALID", path=TEAM_PIPELINE_FILENAME, reason="ENTRYPOINT_CHANGED_DURING_SCAN"
        )
    return ConfiguredTeamPipeline(
        state="CONFIGURED",
        path=TEAM_PIPELINE_FILENAME,
        sha256=digest.hexdigest(),
        byte_count=byte_count,
    )
