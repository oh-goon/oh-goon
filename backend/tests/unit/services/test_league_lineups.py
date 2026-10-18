import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from oh_goon.core.errors import LeagueLineupStale
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.league_lineups import LeagueLineupFileStore
from oh_goon.services.league import LeagueService
from oh_goon.services.league_lineups import LeagueLineupService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.local_state import local_state


def _team(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    return repository


def test_saved_lineup_resolves_current_managed_git_teams_without_goon_requirement(
    tmp_path: Path,
) -> None:
    root = tmp_path / "league"
    api = _team(root, "api")
    worker = _team(root, "worker")
    # An invalid repository goon.yml must not affect Lineup membership.
    (worker / "goon.yml").write_text("broken: [\n", encoding="utf-8")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    league = LeagueService(state, RepositoryDiscoveryService())
    store = LeagueLineupFileStore(tmp_path / "config" / "lineups")
    service = LeagueLineupService(store, league)

    saved = service.save(
        "backend",
        name="Backend",
        repositories=(api, worker),
    )
    inspection = service.inspect("backend")

    assert saved.repositories == (api.resolve(), worker.resolve())
    assert len(saved.revision) == 64
    assert service.read("backend").revision == saved.revision
    assert inspection.ready_count == 2
    assert inspection.unavailable_count == 0
    assert service.repository_ids("backend") == {
        league.repository(api).repository_id,
        league.repository(worker).repository_id,
    }


def test_lineup_does_not_silently_retarget_when_saved_team_disappears(tmp_path: Path) -> None:
    root = tmp_path / "league"
    api = _team(root, "api")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    league = LeagueService(state, RepositoryDiscoveryService())
    service = LeagueLineupService(LeagueLineupFileStore(tmp_path / "lineups"), league)
    service.save("backend", name="Backend", repositories=(api,))

    (api / ".git").rmdir()
    inspection = service.inspect("backend")

    assert inspection.ready_count == 0
    assert inspection.unavailable_count == 1
    assert inspection.teams[0].state == "UNAVAILABLE"


def test_existing_lineup_requires_the_exact_committed_revision(tmp_path: Path) -> None:
    store = LeagueLineupFileStore(tmp_path / "lineups")
    api = tmp_path / "api"
    worker = tmp_path / "worker"
    first = store.write("backend", name="Backend", repositories=(api,))

    with pytest.raises(LeagueLineupStale):
        store.write("backend", name="Stale", repositories=(worker,))

    second = store.write(
        "backend",
        name="Backend v2",
        repositories=(worker,),
        expected_revision=first.revision,
    )
    assert second.revision != first.revision
    assert store.read("backend") == second

    with pytest.raises(LeagueLineupStale):
        store.write(
            "backend",
            name="Lost update",
            repositories=(api,),
            expected_revision=first.revision,
        )
    assert store.read("backend") == second


def test_concurrent_lineup_writers_cannot_both_commit_the_same_base_revision(
    tmp_path: Path,
) -> None:
    store = LeagueLineupFileStore(tmp_path / "lineups")
    first = store.write("backend", name="Backend", repositories=(tmp_path / "base",))

    def save(name: str) -> str:
        try:
            result = store.write(
                "backend",
                name=name,
                repositories=(tmp_path / name.lower().replace(" ", "-"),),
                expected_revision=first.revision,
            )
        except LeagueLineupStale:
            return "stale"
        return result.revision

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(save, ("Writer A", "Writer B")))

    committed = tuple(outcome for outcome in outcomes if outcome != "stale")
    assert len(committed) == 1
    assert outcomes.count("stale") == 1
    assert store.read("backend").revision == committed[0]


def test_lineup_write_returns_exact_committed_document_without_second_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LeagueLineupFileStore(tmp_path / "lineups")
    api = tmp_path / "api"
    worker = tmp_path / "worker"

    def fail_second_read(lineup_key: str):
        raise AssertionError(f"write must not reread committed lineup {lineup_key}")

    monkeypatch.setattr(store, "read", fail_second_read)

    committed = store.write(
        "backend",
        name="Backend",
        repositories=(api, worker),
    )

    disk = LeagueLineupFileStore(store.root).read("backend")
    assert committed == disk


def test_lineup_save_resolves_all_team_paths_from_one_league_state_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    root = tmp_path / "league"
    api = _team(root, "api")
    worker = _team(root, "worker")
    nested = api / "src"
    nested.mkdir()
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    original_read_view = state.read_view
    read_view = Mock(wraps=original_read_view)
    monkeypatch.setattr(state, "read_view", read_view)
    league = LeagueService(state, RepositoryDiscoveryService())
    service = LeagueLineupService(LeagueLineupFileStore(tmp_path / "lineups"), league)

    saved = service.save(
        "backend",
        name="Backend",
        repositories=(nested, worker),
    )

    assert saved.repositories == (api.resolve(), worker.resolve())
    assert read_view.call_count == 1


def test_lineup_inspection_resolves_saved_teams_from_one_league_state_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import Mock

    root = tmp_path / "league"
    api = _team(root, "api")
    worker = _team(root, "worker")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    league = LeagueService(state, RepositoryDiscoveryService())
    service = LeagueLineupService(LeagueLineupFileStore(tmp_path / "lineups"), league)
    service.save("backend", name="Backend", repositories=(api, worker))
    original_read_view = state.read_view
    read_view = Mock(wraps=original_read_view)
    monkeypatch.setattr(state, "read_view", read_view)

    inspection = service.inspect("backend")

    assert inspection.ready_count == 2
    assert inspection.unavailable_count == 0
    assert read_view.call_count == 1


def test_lineup_state_is_owner_private_on_posix(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows confidentiality relies on the user profile/custom-path ACL")
    root = tmp_path / "config" / "lineups"
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    store = LeagueLineupFileStore(root)
    store.write("backend", name="Backend", repositories=(tmp_path / "api",))

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "backend").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "backend" / "lineup.yml").stat().st_mode) == 0o600
