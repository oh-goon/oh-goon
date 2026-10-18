from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.cli import main
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase, inspect_local_database
from oh_goon.runtime.local_state_maintenance import (
    LocalStateCompatibility,
    reset_local_state,
    sqlite_state_files,
)


def _set_database(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("OH_GOON_DB", str(path))


def test_local_state_check_reports_new_database_without_creating_it(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)

    assert main(["local-state", "check"]) == 0
    assert not path.exists()
    output = capsys.readouterr().out
    assert "Local state: NEW" in output
    assert str(path) in output


def test_local_state_diagnostics_path_uses_canonical_state_layout(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)

    assert main(["local-state", "diagnostics-path"]) == 0
    assert capsys.readouterr().out.strip() == str(
        (tmp_path / "diagnostics" / "diagnostics.jsonl").resolve()
    )


def test_local_state_check_gives_one_reset_command_for_stale_epoch(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    assert main(["local-state", "check"]) == 2
    output = capsys.readouterr().out
    assert "Local state: STALE" in output
    assert str(path) in output
    assert "Source checkout: make local-state-reset" in output
    assert "Built release:   oh-goon local-state reset --yes" in output


def test_local_state_reset_requires_explicit_yes(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    path.write_bytes(b"local-state")

    assert main(["local-state", "reset"]) == 2
    assert path.exists()
    assert "Nothing was deleted" in capsys.readouterr().out


def test_local_state_reset_deletes_database_and_sqlite_sidecars_only(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    for state_file in sqlite_state_files(path):
        state_file.write_bytes(b"state")
    unrelated = tmp_path / "keep.txt"
    unrelated.write_text("keep")

    assert main(["local-state", "reset", "--yes"]) == 0
    assert all(not state_file.exists() for state_file in sqlite_state_files(path))
    assert unrelated.read_text() == "keep"
    output = capsys.readouterr().out
    assert "make dev (source)" in output
    assert "oh-goon start (built release)" in output


def test_local_state_reset_durably_publishes_each_existing_file_absence(
    tmp_path, monkeypatch
) -> None:
    from oh_goon.integrations import exact_file_write

    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    for state_file in sqlite_state_files(path):
        state_file.write_bytes(b"state")
    synced: list[Path] = []
    monkeypatch.setattr(exact_file_write, "_fsync_directory", synced.append)

    deleted = reset_local_state(path)

    assert deleted == sqlite_state_files(path)
    assert synced == [tmp_path] * len(sqlite_state_files(path))
    assert all(not state_file.exists() for state_file in sqlite_state_files(path))


def test_schema_mismatch_exception_names_path_and_supported_reset_command(tmp_path) -> None:
    path = tmp_path / "state.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    try:
        LocalDatabase(path)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("stale database should fail")

    assert str(path) in message
    assert "Source checkout: make local-state-reset" in message
    assert "Built release:   oh-goon local-state reset --yes" in message
    assert "do not mix" in message.lower()


def test_inspection_accepts_current_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    LocalDatabase(path)

    inspection = inspect_local_database(expected_schema_version=DATABASE_SCHEMA_VERSION)
    assert inspection.compatible is True
    assert inspection.found_schema_version == DATABASE_SCHEMA_VERSION


def test_stateful_cli_turns_schema_mismatch_into_actionable_startup_error(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    assert main(["doctor"]) == 2
    output = capsys.readouterr().out
    assert "Local oh-goon database schema mismatch" in output
    assert str(path) in output
    assert "Source checkout: make local-state-reset" in output
    assert "Built release:   oh-goon local-state reset --yes" in output


def test_serve_stops_before_uvicorn_on_stale_local_state(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION - 1}")

    assert main(["serve"]) == 2
    output = capsys.readouterr().out
    assert "Startup stopped before opening the application." in output
    assert "Source checkout: make local-state-reset" in output
    assert "Built release:   oh-goon local-state reset --yes" in output


def test_make_dev_has_one_python_launch_frontdoor() -> None:
    script = (Path(__file__).resolve().parents[4] / "scripts" / "dev.sh").read_text()
    assert "oh-goon-local-dev-launch" in script
    assert "uvicorn" not in script
    assert "npm run dev" not in script
    assert "observe-ready" not in script


def test_reset_does_not_delete_existing_league_preferences(tmp_path, monkeypatch) -> None:
    path = tmp_path / "state.db"
    _set_database(monkeypatch, path)
    repository_root = tmp_path / "repos"
    repository_root.mkdir()

    from oh_goon.runtime.league_folders import LeagueFolderStore

    preferences = LeagueFolderStore()
    preferences.replace((repository_root,))
    LocalDatabase(path)

    assert main(["local-state", "reset", "--yes"]) == 0
    assert preferences.list() == (repository_root.resolve(),)


def test_source_dev_recovery_target_remains_repository_owned() -> None:
    root = Path(__file__).resolve().parents[4]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "local-state-reset:" in makefile
    assert "backend/.venv/bin/oh-goon-local-state reset" in makefile


def test_source_operator_docs_use_make_owned_local_state_recovery() -> None:
    root = Path(__file__).resolve().parents[4]
    docs = [
        root / "README.md",
        root / "docs" / "TROUBLESHOOTING.md",
        root / "docs" / "GETTING_STARTED.md",
        root / "docs" / "LOCAL_FIRST.md",
    ]
    for document in docs:
        text = document.read_text(encoding="utf-8")
        assert "make local-state-reset" in text, document
    troubleshooting = (root / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    assert "make local-state-check" in troubleshooting
    assert "make local-state-path" in troubleshooting
    assert "different/global binary" in troubleshooting


def test_make_owns_source_local_state_check_path_and_reset_commands() -> None:
    root = Path(__file__).resolve().parents[4]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    assert "local-state-check:" in makefile
    assert "local-state-path:" in makefile
    assert "diagnostics-path:" in makefile
    assert "diagnostics-log:" in makefile
    assert "backend/.venv/bin/oh-goon-local-state check" in makefile
    assert "backend/.venv/bin/oh-goon-local-state path" in makefile
    assert "backend/.venv/bin/oh-goon-local-state diagnostics-path" in makefile
    assert "backend/.venv/bin/oh-goon-local-state reset" in makefile
    local_owner = (root / "backend/src/oh_goon/repository_tooling/local_state_source.py").read_text(
        encoding="utf-8"
    )
    assert "from oh_goon.runtime.database import" in local_owner
    assert "from oh_goon.runtime.local_state_maintenance import reset_local_state" in local_owner


def test_local_state_inspection_distinguishes_future_schema_from_stale(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "future.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION + 1}")

    inspection = inspect_local_database()

    assert inspection.status is LocalStateCompatibility.FUTURE
    assert inspection.compatible is False
    assert "newer than this build supports" in inspection.detail


def test_local_database_future_schema_fails_closed_without_mutating_epoch(tmp_path) -> None:
    path = tmp_path / "future.db"
    future = DATABASE_SCHEMA_VERSION + 1
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {future}")

    with pytest.raises(Exception, match="newer than this build supports"):
        LocalDatabase(path)

    with closing(sqlite3.connect(path)) as connection, connection:
        assert int(connection.execute("PRAGMA user_version").fetchone()[0]) == future


def test_deep_inspection_rejects_foreign_key_damage_that_schema_and_quick_check_accept(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "foreign-key-damage.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child(parent_id) VALUES (999)")

    schema_only = inspect_local_database()
    quick_only = inspect_local_database(quick_check=True)
    deep = inspect_local_database(
        integrity_check=True,
        foreign_key_check=True,
    )

    assert schema_only.status is LocalStateCompatibility.READY
    assert quick_only.status is LocalStateCompatibility.READY
    assert deep.status is LocalStateCompatibility.INVALID
    assert "foreign_key_check failed" in deep.detail


def test_local_state_check_stops_before_launch_on_foreign_key_damage(
    tmp_path, monkeypatch, capsys
) -> None:
    path = tmp_path / "foreign-key-startup-damage.db"
    _set_database(monkeypatch, path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute("CREATE TABLE child(parent_id INTEGER REFERENCES parent(id))")
        connection.execute("INSERT INTO child(parent_id) VALUES (999)")

    assert main(["local-state", "check"]) == 2
    output = capsys.readouterr().out
    assert "Local state: INVALID" in output
    assert "foreign_key_check failed" in output
    assert "make local-state-reset" in output


def test_source_local_state_reset_entrypoint_accepts_make_confirmation(
    tmp_path, monkeypatch, capsys
) -> None:
    import sys

    from oh_goon.repository_tooling.local_state_source import main as source_local_state_main

    path = tmp_path / "source-reset.db"
    monkeypatch.setenv("OH_GOON_DB", str(path))
    LocalDatabase(path)
    assert path.is_file()
    monkeypatch.setattr(sys, "argv", ["oh-goon-local-state", "reset", "--yes"])

    assert source_local_state_main() == 0
    assert not path.exists()
    output = capsys.readouterr().out
    assert "Local state reset:" in output


def test_source_local_state_reset_entrypoint_refuses_unconfirmed_deletion(
    tmp_path, monkeypatch, capsys
) -> None:
    import sys

    from oh_goon.repository_tooling.local_state_source import main as source_local_state_main

    path = tmp_path / "source-reset-unconfirmed.db"
    monkeypatch.setenv("OH_GOON_DB", str(path))
    LocalDatabase(path)
    monkeypatch.setattr(sys, "argv", ["oh-goon-local-state", "reset"])

    assert source_local_state_main() == 2
    assert path.is_file()
    assert "Nothing was deleted." in capsys.readouterr().out


def test_source_make_local_state_reset_is_explicit_confirmed_recovery() -> None:
    root = Path(__file__).resolve().parents[4]
    makefile = (root / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("local-state-reset:", 1)[1].split("\n\n", 1)[0]
    assert "backend/.venv/bin/oh-goon-local-state reset --yes" in target
