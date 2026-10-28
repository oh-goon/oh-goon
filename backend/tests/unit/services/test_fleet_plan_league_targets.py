from pathlib import Path

import pytest

from oh_goon.core.capability_plan_definitions import PLAN_CAPABILITIES
from oh_goon.core.errors import TargetSelectionError
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    AllRepositoryScope,
    ExplicitRepositoryScope,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.league import LeagueService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.local_state import local_state


def test_fleet_plan_targets_git_team_without_pyproject(tmp_path: Path) -> None:
    root = tmp_path / "league"
    repository = root / "frontend"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "goon.yml").write_text("version: 1\nsteps: []\n", encoding="utf-8")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    discovery = RepositoryDiscoveryService()
    repositories = RepositoryService(state, discovery)
    migration = MigrationService(state, discovery)
    league = LeagueService(state, discovery)
    selector = FleetTargetSelector(repositories, migration, league)

    selected = selector.select(PLAN_CAPABILITIES[0], state.read_view(), None, AllRepositoryScope())

    assert [entry.repository.path for entry in selected] == [repository.resolve()]


def test_fleet_plan_selector_consumes_structural_explicit_scope(tmp_path: Path) -> None:
    root = tmp_path / "league"
    first = root / "first"
    second = root / "second"
    for repository in (first, second):
        repository.mkdir(parents=True)
        (repository / ".git").mkdir()
        (repository / "goon.yml").write_text("version: 1\nsteps: []\n", encoding="utf-8")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    discovery = RepositoryDiscoveryService()
    repositories = RepositoryService(state, discovery)
    migration = MigrationService(state, discovery)
    league = LeagueService(state, discovery)
    selector = FleetTargetSelector(repositories, migration, league)
    repository_id = league.repository(second).repository_id

    selected = selector.select(
        PLAN_CAPABILITIES[0],
        state.read_view(),
        None,
        ExplicitRepositoryScope(repository_ids=(repository_id,)),
    )

    assert [entry.repository.path for entry in selected] == [second.resolve()]


def test_fleet_plan_explicit_scope_can_target_configured_non_git_source_root(
    tmp_path: Path,
) -> None:
    source = tmp_path / "oh-goon-source"
    source.mkdir()
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(source)
    discovery = RepositoryDiscoveryService()
    repositories = RepositoryService(state, discovery)
    migration = MigrationService(state, discovery)
    league = LeagueService(state, discovery)
    selector = FleetTargetSelector(repositories, migration, league)
    repository_id = RepositoryIdentity.model_validate({"path": source}).repository_id

    selected = selector.select(
        PLAN_CAPABILITIES[0],
        state.read_view(),
        None,
        ExplicitRepositoryScope(repository_ids=(repository_id,)),
    )

    assert [entry.repository.path for entry in selected] == [source.resolve()]
    with pytest.raises(TargetSelectionError):
        selector.select(PLAN_CAPABILITIES[0], state.read_view(), None, AllRepositoryScope())


@pytest.mark.parametrize("boundary", ["excluded", "missing", "parent-only"])
def test_explicit_filesystem_scope_retains_registration_and_exclusion_boundaries(
    tmp_path: Path, boundary: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    repository_id = RepositoryIdentity.model_validate({"path": source}).repository_id
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(source.parent if boundary == "parent-only" else source)
    if boundary == "excluded":
        state.exclude(
            source, "excluded source", expected_durable_revision=state.read_view().durable_revision
        )
    elif boundary == "missing":
        source.rmdir()
    discovery = RepositoryDiscoveryService()
    selector = FleetTargetSelector(
        RepositoryService(state, discovery),
        MigrationService(state, discovery),
        LeagueService(state, discovery),
    )
    with pytest.raises(TargetSelectionError):
        selector.select(
            PLAN_CAPABILITIES[0],
            state.read_view(),
            None,
            ExplicitRepositoryScope(repository_ids=(repository_id,)),
        )
