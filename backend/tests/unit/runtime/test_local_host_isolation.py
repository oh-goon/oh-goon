from __future__ import annotations

import os
import stat
from pathlib import Path

from oh_goon.integrations import exact_file_write
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.scout.store import ScoutObservationStore


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_operational_state_root_and_database_are_rehardened_owner_only(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    root = tmp_path / "state"
    root.mkdir(mode=0o755)
    root.chmod(0o755)

    database = LocalDatabase(root / "oh-goon.db")

    assert _mode(root) == 0o700
    assert _mode(database.path) == 0o600


def test_league_preferences_root_and_file_are_rehardened_owner_only(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    root = tmp_path / "config" / "oh-goon"
    root.mkdir(parents=True, mode=0o755)
    root.chmod(0o755)
    repository = tmp_path / "repo"
    repository.mkdir()
    store = LeagueFolderStore(root / "league.yml")

    store.replace((repository,))

    assert _mode(root) == 0o700
    assert _mode(store.path) == 0o600


def test_scout_rehardens_existing_analytical_database_parent_owner_only(tmp_path: Path) -> None:
    if os.name == "nt":
        return
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    database = LocalDatabase(root / "state.sqlite3")
    root.chmod(0o755)

    ScoutObservationStore(database)

    assert _mode(root) == 0o700


def test_exact_file_lock_root_is_per_user_and_rehardened_owner_only(
    tmp_path: Path, monkeypatch
) -> None:
    if os.name == "nt":
        return
    monkeypatch.setattr(exact_file_write.tempfile, "gettempdir", lambda: str(tmp_path))
    expected = tmp_path / f"oh-goon-exact-file-locks-{os.getuid()}"
    expected.mkdir(mode=0o755)
    expected.chmod(0o755)

    observed = exact_file_write._exact_file_lock_root()

    assert observed == expected.resolve()
    assert _mode(observed) == 0o700


def test_exact_file_lock_is_created_inside_private_user_scope(tmp_path: Path, monkeypatch) -> None:
    if os.name == "nt":
        return
    monkeypatch.setattr(exact_file_write.tempfile, "gettempdir", lambda: str(tmp_path))
    target = tmp_path / "state" / "league.yml"
    target.parent.mkdir()

    with exact_file_write.exact_file_write_lock(target):
        root = exact_file_write._exact_file_lock_root()
        locks = tuple(root.glob("*.lock"))
        assert len(locks) == 1
        assert _mode(root) == 0o700
