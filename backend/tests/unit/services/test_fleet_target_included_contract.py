from pathlib import Path

from oh_goon.core.capability_plan_definitions import PLAN_CAPABILITIES
from oh_goon.core.models.repository import IncludedRepositoryCatalogEntry
from oh_goon.core.models.run_admission import AllRepositoryScope
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.league import LeagueService
from oh_goon.services.migration import MigrationService
from oh_goon.services.repositories import RepositoryService
from oh_goon.services.target_selection import FleetTargetSelector
from tests.factories.local_state import local_state


def _league_repository(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "goon.yml").write_text(f"name: {name}\nsteps: []\n", encoding="utf-8")
    return repository


def test_fleet_target_selector_returns_only_included_entries(tmp_path: Path) -> None:
    root = tmp_path / "league"
    included = _league_repository(root, "included")
    excluded = _league_repository(root, "excluded")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    state.exclude(
        excluded,
        "maintenance",
        expected_durable_revision=state.read_view().durable_revision,
    )
    discovery = RepositoryDiscoveryService()
    selector = FleetTargetSelector(
        RepositoryService(state, discovery),
        MigrationService(state, discovery),
        LeagueService(state, discovery),
    )

    selected = selector.select(PLAN_CAPABILITIES[0], state.read_view(), None, AllRepositoryScope())

    assert [entry.repository.path for entry in selected] == [included.resolve()]
    assert all(isinstance(entry, IncludedRepositoryCatalogEntry) for entry in selected)


def test_migration_safe_targets_are_included_entries(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    repository = root / "pep621"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    (repository / "pyproject.toml").write_text(
        "[project]\nname='pep621'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)

    selected = MigrationService(state, RepositoryDiscoveryService()).safe_targets(state.read_view())

    assert len(selected) == 1
    assert isinstance(selected[0], IncludedRepositoryCatalogEntry)
