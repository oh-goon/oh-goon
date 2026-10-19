from pathlib import Path

from oh_goon.core.capability_plan_definitions import PLAN_CAPABILITIES
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
    repository_id = RepositoryIdentity(path=source).repository_id

    selected = selector.select(
        PLAN_CAPABILITIES[0],
        state.read_view(),
        None,
        ExplicitRepositoryScope(repository_ids=(repository_id,)),
    )

    assert [entry.repository.path for entry in selected] == [source.resolve()]
