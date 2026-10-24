from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import LeagueTeamNotManaged, TeamPipelineNotConfigured
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services.league import LeagueService
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from tests.factories.local_state import local_state


def _git_team(root: Path, name: str) -> Path:
    repository = root / name
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    return repository


def test_league_scan_maps_every_git_team_and_only_root_goon_yml_is_configured(
    tmp_path: Path,
) -> None:
    root = tmp_path / "league"
    configured = _git_team(root, "configured")
    _git_team(root, "missing")
    nested_only = _git_team(root, "nested-only")
    (configured / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")
    (nested_only / "ci").mkdir()
    (nested_only / "ci" / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    league = LeagueService(state, RepositoryDiscoveryService()).map()

    by_name = {team.repository.name: team for team in league.teams}
    assert set(by_name) == {"configured", "missing", "nested-only"}
    assert by_name["configured"].pipeline.state == "CONFIGURED"
    assert by_name["configured"].pipeline.path == "goon.yml"
    assert len(by_name["configured"].pipeline.sha256) == 64
    assert by_name["missing"].pipeline.state == "MISSING"
    assert by_name["nested-only"].pipeline.state == "MISSING"
    assert league.summary.git_repositories == 3
    assert league.summary.configured_teams == 1
    assert league.summary.missing_goon_yml == 2
    assert league.summary.invalid_goon_yml == 0


def test_league_scan_refuses_goon_yml_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("symlink creation is permission-dependent on Windows")
    root = tmp_path / "league"
    repository = _git_team(root, "api")
    outside = tmp_path / "outside.yml"
    outside.write_text("steps:\n- run: 'true'\n", encoding="utf-8")
    (repository / "goon.yml").symlink_to(outside)

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    team = LeagueService(state, RepositoryDiscoveryService()).map().teams[0]

    assert team.pipeline.state == "INVALID"
    assert team.pipeline.reason == "ENTRYPOINT_SYMLINK"


def test_league_scan_is_derived_and_reflects_entrypoint_changes_without_persisted_team_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "league"
    repository = _git_team(root, "api")
    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    service = LeagueService(state, RepositoryDiscoveryService())

    before = service.map()
    assert before.teams[0].pipeline.state == "MISSING"

    (repository / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")
    after = service.map()

    assert after.teams[0].pipeline.state == "CONFIGURED"
    assert after.summary.configured_teams == 1


def test_configured_team_requires_managed_git_root_with_valid_entrypoint(tmp_path: Path) -> None:
    root = tmp_path / "league"
    configured = _git_team(root, "api")
    missing = _git_team(root, "worker")
    (configured / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")
    outside = _git_team(tmp_path / "outside", "rogue")
    (outside / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    service = LeagueService(state, RepositoryDiscoveryService())

    assert service.configured_team(configured).repository.path == configured.resolve()
    with pytest.raises(TeamPipelineNotConfigured, match="runnable goon.yml"):
        service.configured_team(missing)
    with pytest.raises(LeagueTeamNotManaged, match="not a Git team"):
        service.configured_team(outside)


def test_configured_team_resolves_nearest_managed_team_from_subdirectory(tmp_path: Path) -> None:
    root = tmp_path / "league"
    repository = _git_team(root, "api")
    nested = repository / "src" / "package"
    nested.mkdir(parents=True)
    (repository / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    service = LeagueService(state, RepositoryDiscoveryService())

    assert service.configured_team(nested).repository.path == repository.resolve()


def test_discovery_reaches_git_team_beyond_old_arbitrary_depth(tmp_path: Path) -> None:
    root = tmp_path / "league"
    repository = root / "group" / "platform" / "services" / "payments" / "python" / "api"
    repository.mkdir(parents=True)
    (repository / ".git").mkdir()
    work = repository / "src" / "package"
    work.mkdir(parents=True)

    discovery = RepositoryDiscoveryService()

    assert discovery.git_roots([root], require_pyproject=False) == (repository.resolve(),)
    assert discovery.containing_git_root([root], work) == repository.resolve()


def test_configured_team_matches_top_down_league_discovery_for_nested_git_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "league"
    outer = _git_team(root, "mono")
    inner = outer / "packages" / "api"
    inner.mkdir(parents=True)
    (inner / ".git").mkdir()
    (outer / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")
    (inner / "goon.yml").write_text("steps:\n- run: 'true'\n", encoding="utf-8")
    work = inner / "src"
    work.mkdir()

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    service = LeagueService(state, RepositoryDiscoveryService())

    assert service.configured_team(work).repository.path == outer.resolve()


def test_managed_repository_identity_does_not_inspect_repository_goon_yml(tmp_path: Path) -> None:
    root = tmp_path / "league"
    repository = _git_team(root, "api")
    goon = repository / "goon.yml"
    if os.name != "nt":
        outside = tmp_path / "outside.yml"
        outside.write_text("not: this team's pipeline\n", encoding="utf-8")
        goon.symlink_to(outside)
    else:
        goon.write_text("this is deliberately not a valid plan: [\n", encoding="utf-8")

    state = local_state(LocalDatabase(tmp_path / "state.db"))
    state.add_location(root)
    service = LeagueService(state, RepositoryDiscoveryService())

    # Internal-plan selection needs only current managed Git identity. The repo-owned
    # goon.yml is a different source and therefore cannot participate in this lookup.
    assert service.repository(repository / "src").path == repository.resolve()


def test_league_discovery_git_root_is_independent_of_ancestor_directory_name(
    tmp_path: Path,
) -> None:
    discovery = RepositoryDiscoveryService()
    for ancestor in ("build", "dist", ".hidden", "node_modules", "workspace"):
        root = tmp_path / ancestor.replace(".", "hidden-") / "league"
        repository = root / ancestor / "repo"
        repository.mkdir(parents=True)
        (repository / ".git").mkdir()

        assert discovery.git_roots([root], require_pyproject=False) == (repository.resolve(),)


def test_league_discovery_does_not_invent_team_from_ancestor_name(tmp_path: Path) -> None:
    root = tmp_path / "league"
    (root / "build" / "not-a-repo").mkdir(parents=True)

    assert RepositoryDiscoveryService().git_roots([root], require_pyproject=False) == ()
