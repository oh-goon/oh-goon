from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import LeagueTeamNotManaged
from oh_goon.core.models.shared_plan import (
    LeagueLineupCatalog,
    LeagueLineupDocument,
    LeagueLineupInspection,
    LeagueLineupSummary,
    ReadyLeagueLineupTeam,
    UnavailableLeagueLineupTeam,
)
from oh_goon.runtime.league_lineups import LeagueLineupFileStore
from oh_goon.services.league import LeagueService


class LeagueLineupService:
    """Own reusable local Team selections; resolve them against the live League only on demand."""

    def __init__(self, store: LeagueLineupFileStore, league: LeagueService) -> None:
        self._store = store
        self._league = league

    @property
    def root(self) -> Path:
        return self._store.root

    def catalog(self) -> LeagueLineupCatalog:
        return LeagueLineupCatalog(
            lineups=tuple(self._summary(self._store.read(key)) for key in self._store.list_keys())
        )

    def read(self, lineup_key: str) -> LeagueLineupDocument:
        return self._store.read(lineup_key)

    def save(
        self,
        lineup_key: str,
        *,
        name: str,
        repositories: tuple[Path, ...],
        expected_revision: str | None = None,
    ) -> LeagueLineupDocument:
        # Only managed Git Team roots may enter a saved Lineup. Repository goon.yml is irrelevant.
        identities = self._league.resolve_repositories(repositories)
        canonical = tuple(identity.path for identity in identities)
        return self._store.write(
            lineup_key,
            name=name,
            repositories=canonical,
            expected_revision=expected_revision,
        )

    def inspect(self, lineup_key: str) -> LeagueLineupInspection:
        document = self.read(lineup_key)
        current = {repository.path: repository for repository in self._league.repositories()}
        teams = []
        for configured_path in document.repositories:
            repository = current.get(configured_path)
            if repository is None:
                teams.append(
                    UnavailableLeagueLineupTeam(
                        configured_path=configured_path,
                        reason="NOT_MANAGED_TEAM",
                    )
                )
            else:
                teams.append(
                    ReadyLeagueLineupTeam(
                        configured_path=configured_path,
                        repository=repository,
                    )
                )
        ready = sum(item.state == "READY" for item in teams)
        return LeagueLineupInspection(
            lineup_key=document.lineup_key,
            name=document.name,
            revision=document.revision,
            teams=tuple(teams),
            ready_count=ready,
            unavailable_count=len(teams) - ready,
        )

    def repository_ids(self, lineup_key: str) -> set[str]:
        inspection = self.inspect(lineup_key)
        if inspection.unavailable_count:
            raise LeagueTeamNotManaged(
                f"League Lineup {lineup_key} has {inspection.unavailable_count} unavailable Team(s); inspect the Lineup before running"
            )
        return {item.repository.repository_id for item in inspection.teams if item.state == "READY"}

    @staticmethod
    def _summary(document: LeagueLineupDocument) -> LeagueLineupSummary:
        return LeagueLineupSummary(
            lineup_key=document.lineup_key,
            name=document.name,
            team_count=document.team_count,
        )
