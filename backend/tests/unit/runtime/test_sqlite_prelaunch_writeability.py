from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.cli_local_state import check_local_state_for_startup
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION
from oh_goon.runtime.storage_health import probe_sqlite_prelaunch


def _set_database(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("OH_GOON_DB", str(path))


def test_missing_database_uses_disposable_sibling_sqlite_roundtrip_without_creating_real_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "oh-goon.db"

    probe_sqlite_prelaunch(path)

    assert not path.exists()
    assert path.parent.is_dir()
    assert not list(path.parent.glob(".oh-goon-sqlite-prelaunch-*"))


def test_existing_database_write_lock_probe_is_non_semantic_and_byte_stable(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    probe_sqlite_prelaunch(path)

    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == before
    assert not Path(f"{path}-journal").exists()
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_local_state_check_remains_read_only_while_prelaunch_check_proves_writeability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)

    def fail_probe(_path: Path) -> None:
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr("oh_goon.cli_local_state.probe_sqlite_prelaunch", fail_probe)
    assert main(["local-state", "check"]) == 0
    assert not path.exists()
    capsys.readouterr()

    assert main(["local-state", "prelaunch-check"]) == 2
    output = capsys.readouterr().out
    assert "Prelaunch SQLite write/lock proof: FAILED" in output
    assert "attempt to write a readonly database" in output
    assert not path.exists()


def test_built_startup_helper_stops_before_application_on_write_lock_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)

    def fail_probe(_path: Path) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr("oh_goon.cli_local_state.probe_sqlite_prelaunch", fail_probe)

    assert check_local_state_for_startup() == 2
    output = capsys.readouterr().out
    assert "Startup stopped before opening the application." in output
    assert "SQLite prelaunch write/lock proof failed" in output
    assert "database or disk is full" in output
    assert not path.exists()
