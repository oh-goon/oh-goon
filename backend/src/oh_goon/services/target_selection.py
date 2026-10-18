from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from oh_goon.core.capability_model import CapabilityDefinition, CapabilityTargetSelector
from oh_goon.core.errors import TargetSelectionError
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry, RepositoryIdentity
from oh_goon.core.models.run_admission import (
    ExplicitRepositoryScope,
    FleetRepositoryScope,
)
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import FleetScope
from oh_goon.core.path_authority import navigation_path
from oh_goon.runtime.state import LocalStateReadView
from oh_goon.services.league import LeagueService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.target_selection_projection import ProjectionTargetSelection

Selector = Callable[
    [LocalStateReadView, OperationTarget | None], list[IncludedRepositoryCatalogEntry]
]


@dataclass(frozen=True, slots=True)
class FleetTargetSelector:
    """Thin selector registry; capability-family eligibility lives in focused owners."""

    repositories: RepositoryService
    migration: MigrationService
    league: LeagueService
    _selectors: Mapping[CapabilityTargetSelector, Selector] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        projection = ProjectionTargetSelection(self.repositories, self.migration)
        selectors = {
            CapabilityTargetSelector.INCLUDED: self._included,
            CapabilityTargetSelector.LEAGUE_TEAM: self._league_team,
            CapabilityTargetSelector.MIGRATION_SAFE: projection.migration_safe,
            CapabilityTargetSelector.REVIEW_READY: projection.review_ready,
        }
        if set(selectors) != set(CapabilityTargetSelector):
            missing = sorted(item.value for item in set(CapabilityTargetSelector) - set(selectors))
            extra = sorted(item.value for item in set(selectors) - set(CapabilityTargetSelector))
            raise AssertionError(
                "Fleet target selector wiring must exactly cover the canonical selector vocabulary; "
                f"missing={missing}, extra={extra}"
            )
        object.__setattr__(self, "_selectors", MappingProxyType(selectors))

    def select(
        self,
        capability: CapabilityDefinition,
        state: LocalStateReadView,
        target: OperationTarget | None,
        repository_scope: FleetRepositoryScope,
    ) -> list[IncludedRepositoryCatalogEntry]:
        selected = self._selectors[capability.selector](state, target)
        if (
            capability.selector is CapabilityTargetSelector.LEAGUE_TEAM
            and isinstance(repository_scope, ExplicitRepositoryScope)
        ):
            selected = self._include_explicit_configured_roots(
                selected,
                state=state,
                repository_scope=repository_scope,
            )
        selected = self._restrict_repository_scope(selected, repository_scope)
        if not selected:
            raise TargetSelectionError(
                f"No repositories are eligible for capability {capability.capability_id}"
            )
        return selected

    @staticmethod
    def _include_explicit_configured_roots(
        selected: list[IncludedRepositoryCatalogEntry],
        *,
        state: LocalStateReadView,
        repository_scope: ExplicitRepositoryScope,
    ) -> list[IncludedRepositoryCatalogEntry]:
        """Allow an exact configured source root to be selected without Git discovery.

        Broad League discovery remains Git-oriented. An explicit Plan request is different:
        the caller already names one canonical repository identity, so a configured root may
        participate directly even when Git is not an authority for that source tree.
        """

        requested = set(repository_scope.repository_ids)
        by_id = {entry.repository.repository_id: entry for entry in selected}
        for location in state.locations:
            repository = RepositoryIdentity(path=location)
            if repository.repository_id not in requested:
                continue
            if str(navigation_path(repository.path)) in state.exclusions:
                continue
            by_id.setdefault(
                repository.repository_id,
                IncludedRepositoryCatalogEntry(
                    scope=FleetScope.INCLUDED,
                    repository=repository,
                ),
            )
        return list(by_id.values())

    def _included(
        self, state: LocalStateReadView, _target: OperationTarget | None
    ) -> list[IncludedRepositoryCatalogEntry]:
        return list(self.repositories.included(state))

    def _league_team(
        self,
        state: LocalStateReadView,
        _target: OperationTarget | None,
    ) -> list[IncludedRepositoryCatalogEntry]:
        """Select current Git-team repositories that are not excluded."""
        return [
            IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=repository)
            for repository in self.league.repositories(state)
            if str(navigation_path(repository.path)) not in state.exclusions
        ]

    @staticmethod
    def _restrict_repository_scope(
        selected: list[IncludedRepositoryCatalogEntry],
        repository_scope: FleetRepositoryScope,
    ) -> list[IncludedRepositoryCatalogEntry]:
        if not isinstance(repository_scope, ExplicitRepositoryScope):
            return selected
        repository_ids = set(repository_scope.repository_ids)
        eligible_by_id = {entry.repository.repository_id: entry for entry in selected}
        missing = repository_ids - set(eligible_by_id)
        if missing:
            raise TargetSelectionError(
                "Requested repositories are not eligible fleet targets: "
                + ", ".join(sorted(missing))
            )
        return [entry for entry in selected if entry.repository.repository_id in repository_ids]
