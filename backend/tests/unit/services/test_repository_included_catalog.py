from __future__ import annotations

import json

import pytest
from datetime import UTC, datetime
from pathlib import Path

from oh_goon.core.errors import StateConflict
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.models.repository import (
    ExcludedRepositoryCatalogEntry,
    IncludedRepositoryCatalogEntry,
    RepositoryCatalogEntry,
    RepositoryIdentity,
)
from oh_goon.core.vocabulary import FleetScope
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.repositories import RepositoryService
from tests.factories.local_state import local_state


class _CatalogDiscovery:
    def __init__(self, entries: list[RepositoryCatalogEntry]) -> None:
        self._entries = entries
        self.calls = 0

    def discover(self, locations, exclusions):
        del locations, exclusions
        self.calls += 1
        return list(self._entries)


def test_included_catalog_filters_scope_once_and_narrows_entry_type(tmp_path: Path) -> None:
    included_path = tmp_path / "included"
    excluded_path = tmp_path / "excluded"
    included_path.mkdir()
    excluded_path.mkdir()
    discovery = _CatalogDiscovery(
        [
            IncludedRepositoryCatalogEntry(
                scope=FleetScope.INCLUDED,
                repository=RepositoryIdentity(path=included_path),
            ),
            ExcludedRepositoryCatalogEntry(
                scope=FleetScope.EXCLUDED,
                repository=RepositoryIdentity(path=excluded_path),
                exclusion_reason="maintenance",
                excluded_at=datetime.now(UTC),
            ),
        ]
    )
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    service = RepositoryService(state, discovery)  # type: ignore[arg-type]
    view = state.read_view()

    result = service.included(view)

    assert discovery.calls == 1
    assert len(result) == 1
    assert isinstance(result[0], IncludedRepositoryCatalogEntry)
    assert result[0].repository.path == included_path.resolve()


def test_repository_service_write_results_are_scope_specific() -> None:
    assert RepositoryService.exclude.__annotations__["return"] == "ExcludedRepositoryCatalogEntry"
    assert RepositoryService.include.__annotations__["return"] == "IncludedRepositoryCatalogEntry"


def test_repository_write_openapi_results_are_scope_specific() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "generated" / "openapi" / "openapi.json").read_text(encoding="utf-8")
    )
    exclude_schema = schema["paths"]["/api/repositories/exclude"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    include_schema = schema["paths"]["/api/repositories/include"]["post"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]

    assert exclude_schema == {"$ref": "#/components/schemas/ExcludedRepositoryCatalogEntry"}
    assert include_schema == {"$ref": "#/components/schemas/IncludedRepositoryCatalogEntry"}



def test_repository_scope_commands_fail_closed_when_displayed_scope_is_stale(tmp_path: Path) -> None:
    repository = tmp_path / "repo-stale"
    repository.mkdir()
    (repository / ".git").mkdir()
    identity = RepositoryIdentity(path=repository)

    class ScopeAwareDiscovery:
        def discover(self, locations, exclusions):
            del locations
            record = exclusions.get(str(repository.resolve()))
            if record is None:
                return [IncludedRepositoryCatalogEntry(scope=FleetScope.INCLUDED, repository=identity)]
            return [ExcludedRepositoryCatalogEntry(
                scope=FleetScope.EXCLUDED, repository=identity,
                exclusion_reason=record["reason"], excluded_at=record["excluded_at"],
            )]

    state = local_state(LocalDatabase(tmp_path / "stale-state.db"))
    service = RepositoryService(state, ScopeAwareDiscovery())  # type: ignore[arg-type]

    service.exclude(str(repository), "maintenance", repository_subject_instance_id(repository))
    with pytest.raises(StateConflict, match="include state was displayed"):
        service.exclude(str(repository), "again", repository_subject_instance_id(repository))

    service.include(str(repository), repository_subject_instance_id(repository))
    with pytest.raises(StateConflict, match="exclude state was displayed"):
        service.include(str(repository), repository_subject_instance_id(repository))
