from __future__ import annotations

from oh_goon.core.errors import TargetSelectionError
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.projections import FleetProjectionContext, build_review_preparation
from oh_goon.runtime.state import LocalStateReadView
from oh_goon.services.fleet_projection import repository_view
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService


class ProjectionTargetSelection:
    def __init__(self, repositories: RepositoryService, migration: MigrationService) -> None:
        self._repositories = repositories
        self._migration = migration

    def migration_safe(
        self, state: LocalStateReadView, _target: OperationTarget | None
    ) -> list[IncludedRepositoryCatalogEntry]:
        return self._migration.safe_targets(state, None)

    def review_ready(
        self, state: LocalStateReadView, target: OperationTarget | None
    ) -> list[IncludedRepositoryCatalogEntry]:
        catalog = self._repositories.list(state)
        projection = FleetProjectionContext.build(
            [repository_view(entry, state.journal) for entry in catalog],
            state.journal.active_run_detail(),
            state.journal.latest_run_details(),
        )
        review = build_review_preparation(projection)
        if review is None or review.target != target:
            raise TargetSelectionError(
                "No repositories have verified dependency changes ready for review"
            )
        reviewable_ids = {repository.repository_id for repository in review.repositories}
        selected = [
            entry
            for entry in catalog
            if isinstance(entry, IncludedRepositoryCatalogEntry)
            and entry.repository.repository_id in reviewable_ids
        ]
        if not selected:
            raise TargetSelectionError(
                "No repositories have verified dependency changes ready for review"
            )
        return selected
