from pathlib import Path

import pytest

from oh_goon.core.errors import LeaguePreferencesInvalid
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.league_folders import LeagueFolderStore
from tests.factories.local_state import local_state


def test_league_folders_are_atomic_human_readable_preferences(tmp_path: Path) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    first.mkdir()
    second.mkdir()
    store = LeagueFolderStore(tmp_path / "league.yml")

    assert store.replace((first, second)) == (first.resolve(), second.resolve())
    assert store.list() == (first.resolve(), second.resolve())

    text = store.path.read_text(encoding="utf-8")
    assert text.startswith("version: 1\nfolders:\n")
    assert str(first.resolve()) in text
    assert str(second.resolve()) in text
    assert not hasattr(store, "add")
    assert not hasattr(store, "remove")


def test_invalid_league_preferences_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "league.yml"
    path.write_text("version: 99\nfolders: []\n", encoding="utf-8")

    with pytest.raises(LeaguePreferencesInvalid, match="unsupported"):
        LeagueFolderStore(path).list()


def test_runtime_projection_restores_from_league_preferences_after_database_recreation(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repos"
    repository_root.mkdir()
    database_path = tmp_path / "state.db"

    first = local_state(LocalDatabase(database_path))
    first.add_location(repository_root)
    database_path.unlink()
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{database_path}{suffix}").unlink(missing_ok=True)

    restarted = local_state(LocalDatabase(database_path))
    assert restarted.read_view().locations == ()
    restarted.reconcile_league_folders()
    assert restarted.read_view().locations == (repository_root.resolve(),)


def test_reconcile_is_idempotent_when_preferences_match_runtime_projection(tmp_path: Path) -> None:
    repository_root = tmp_path / "repos"
    repository_root.mkdir()
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    state.add_location(repository_root)
    before = database.durable_revision()

    state.reconcile_league_folders()

    assert database.durable_revision() == before
    assert state.read_view().locations == (repository_root.resolve(),)


def test_missing_preferences_clear_stale_runtime_projection_without_recreating_file(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repos"
    repository_root.mkdir()
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, 1, ?)",
            (str(repository_root.resolve()), "2026-08-19T00:00:00+00:00"),
        )
    preferences = LeagueFolderStore(database.path.with_name("league.yml"))
    before = database.durable_revision()

    assert not preferences.path.exists()
    assert state.read_view().locations == (repository_root.resolve(),)

    assert state.reconcile_league_folders() == []

    assert state.read_view().locations == ()
    assert not preferences.path.exists()
    assert database.durable_revision() == before + 1


def test_missing_preferences_reconcile_is_idempotent_once_projection_is_empty(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.db")
    state = local_state(database)
    preferences = LeagueFolderStore(database.path.with_name("league.yml"))
    before = database.durable_revision()

    assert state.reconcile_league_folders() == []
    assert state.reconcile_league_folders() == []

    assert database.durable_revision() == before
    assert state.read_view().locations == ()
    assert not preferences.path.exists()


def test_exact_file_publication_syncs_parent_directory_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LeagueFolderStore(tmp_path / "league.yml")
    root = tmp_path / "repos"
    root.mkdir()
    observed: list[Path] = []

    monkeypatch.setattr(
        "oh_goon.integrations.exact_file_write._fsync_directory",
        lambda directory: observed.append(directory),
    )

    store.replace((root,))

    assert observed == [tmp_path]
    assert store.list() == (root.resolve(),)


def test_directory_sync_ignores_only_unsupported_filesystem_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import errno

    from oh_goon.integrations import exact_file_write

    monkeypatch.setattr(exact_file_write.os, "open", lambda *_args: 91)
    monkeypatch.setattr(exact_file_write.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(
        exact_file_write.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError(errno.EINVAL, "unsupported")),
    )

    exact_file_write._fsync_directory(tmp_path)


def test_directory_sync_surfaces_real_durability_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import errno

    from oh_goon.integrations import exact_file_write

    monkeypatch.setattr(exact_file_write.os, "open", lambda *_args: 92)
    monkeypatch.setattr(exact_file_write.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(
        exact_file_write.os,
        "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError(errno.EIO, "sync failed")),
    )

    with pytest.raises(OSError, match="sync failed"):
        exact_file_write._fsync_directory(tmp_path)
