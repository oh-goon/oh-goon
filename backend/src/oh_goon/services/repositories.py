from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import RepositoryNotManaged, StateConflict
from oh_goon.core.models.repository import (
    ExcludedRepositoryCatalogEntry,
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
)
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary import FleetScope
from oh_goon.runtime.state import LocalState, LocalStateReadView
from oh_goon.services.repository_discovery import RepositoryDiscoveryService


class RepositoryService:
    def __init__(self, state: LocalState, discovery: RepositoryDiscoveryService) -> None:
        self._state = state
        self._discovery = discovery

    def list(self, view: LocalStateReadView | None = None) -> list[RepositoryCatalogEntry]:
        captured = view or self._state.read_view()
        return self._discovery.discover(captured.locations, captured.exclusions)

    def included(
        self, view: LocalStateReadView | None = None
    ) -> list[IncludedRepositoryCatalogEntry]:
        """Return the included catalog from one captured local-state view."""
        return [
            entry for entry in self.list(view) if isinstance(entry, IncludedRepositoryCatalogEntry)
        ]

    def exclude(
        self, raw_path: str, reason: str, expected_repository_instance_id: str
    ) -> ExcludedRepositoryCatalogEntry:
        entry, revision, currently_excluded = self._managed_repository(
            raw_path, expected_repository_instance_id
        )
        if currently_excluded:
            raise StateConflict("Repository scope changed since the include state was displayed")
        record = self._state.exclude(
            entry.repository.path,
            reason,
            expected_durable_revision=revision,
        )
        return ExcludedRepositoryCatalogEntry(
            scope=FleetScope.EXCLUDED,
            repository=entry.repository,
            exclusion_reason=record["reason"],
            excluded_at=record["excluded_at"],
        )

    def include(
        self, raw_path: str, expected_repository_instance_id: str
    ) -> IncludedRepositoryCatalogEntry:
        entry, revision, currently_excluded = self._managed_repository(
            raw_path, expected_repository_instance_id
        )
        if not currently_excluded:
            raise StateConflict("Repository scope changed since the exclude state was displayed")
        self._state.include(
            entry.repository.path,
            expected_durable_revision=revision,
        )
        return IncludedRepositoryCatalogEntry(
            scope=FleetScope.INCLUDED, repository=entry.repository
        )

    def _managed_repository(
        self, raw_path: str, expected_repository_instance_id: str
    ) -> tuple[RepositoryCatalogEntry, int, bool]:
        requested = Path(raw_path).expanduser().resolve()
        captured = self._state.read_view()
        entry = next(
            (item for item in self.list(captured) if item.repository.path.resolve() == requested),
            None,
        )
        if entry is None:
            raise RepositoryNotManaged("Repository is not inside a configured oh-goon location")
        if repository_subject_instance_id(entry.repository.path) != expected_repository_instance_id:
            raise StateConflict("Repository instance changed since the scope state was displayed")
        return entry, captured.durable_revision, str(entry.repository.path) in captured.exclusions

    def exclusions(self) -> list[dict[str, str]]:
        return self._state.exclusions()
