from __future__ import annotations

from oh_goon.core.errors import TargetSelectionError
from oh_goon.core.migration_detection import detect_migration_candidate
from oh_goon.core.models.migration import AdoptUvPreview
from oh_goon.core.models.repository import (
    IncludedRepositoryCatalogEntry,
)
from oh_goon.core.vocabulary import FleetScope, MigrationReadiness
from oh_goon.core.path_authority import navigation_path
from oh_goon.runtime.state import LocalState, LocalStateReadView
from oh_goon.services.repository_discovery import RepositoryDiscoveryService


class MigrationService:
    """Preview and select migration candidates from one pinned durable state cut."""

    def __init__(self, state: LocalState, discovery: RepositoryDiscoveryService) -> None:
        self._state = state
        self._discovery = discovery

    def preview(self) -> AdoptUvPreview:
        return self.preview_from(self._state.read_view())

    def preview_from(self, state: LocalStateReadView) -> AdoptUvPreview:
        candidates = [
            detect_migration_candidate(path)
            for path in self._discovery.git_roots(
                state.locations,
                require_pyproject=False,
            )
            if str(navigation_path(path)) not in state.exclusions
        ]
        candidates.sort(key=lambda item: item.repository.name.casefold())
        return AdoptUvPreview(
            repositories=candidates,
            discovered=len(candidates),
            already_uv=sum(item.readiness is MigrationReadiness.ALREADY_UV for item in candidates),
            safe_automatic=sum(
                item.readiness is MigrationReadiness.SAFE_AUTOMATIC for item in candidates
            ),
            review_required=sum(
                item.readiness is MigrationReadiness.REVIEW_REQUIRED for item in candidates
            ),
            unsupported=sum(
                item.readiness is MigrationReadiness.UNSUPPORTED for item in candidates
            ),
        )

    def safe_targets(
        self,
        state: LocalStateReadView,
        repository_ids: set[str] | None = None,
    ) -> list[IncludedRepositoryCatalogEntry]:
        selected = [
            item
            for item in self.preview_from(state).repositories
            if item.readiness is MigrationReadiness.SAFE_AUTOMATIC
            and (repository_ids is None or item.repository.repository_id in repository_ids)
        ]
        if repository_ids is not None:
            found = {item.repository.repository_id for item in selected}
            missing = repository_ids - found
            if missing:
                raise TargetSelectionError(
                    "Requested repositories are not safe automatic Adopt UV targets: "
                    + ", ".join(sorted(missing))
                )
        if not selected:
            raise TargetSelectionError(
                "No repositories are currently safe for automatic Adopt UV migration"
            )
        return [
            IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=item.repository)
            for item in selected
        ]
