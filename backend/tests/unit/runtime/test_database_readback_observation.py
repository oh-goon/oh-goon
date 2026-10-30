"""Readback reuses admitted native SQLite observations without write authority."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import RuntimeQuiescing, StateConflict
from oh_goon.runtime.database import DatabasePathIdentityLost, LocalDatabase
from oh_goon.runtime.journal import Journal


def test_observation_transactions_reject_domain_writes_and_keep_original_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.db"
    original = LocalDatabase(path, journal_mode="DELETE")
    revision = original.durable_revision()
    observer = LocalDatabase(path, read_only=True)
    assert observer.journal_mode == "DELETE"
    assert Journal(observer).read_view().revision == revision
    with pytest.raises(RuntimeError, match="cannot acquire write authority"):
        with observer.transaction(write=True):
            pytest.fail("Observation entered a domain write transaction")
    with observer.transaction() as connection:
        assert connection.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            connection.execute("DELETE FROM locations")
    assert original.durable_revision() == revision

    replacement = tmp_path / "replacement.db"
    LocalDatabase(replacement, journal_mode="DELETE")
    os.replace(replacement, path)
    with pytest.raises(DatabasePathIdentityLost):
        with observer.transaction():
            pytest.fail("Observation silently rebound to replacement state")


def test_observation_runtime_does_not_create_or_harden_unselected_stores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state.db"
    database = LocalDatabase(state, journal_mode="DELETE")
    monkeypatch.setenv("OH_GOON_DB", str(state))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(tmp_path / "league.yml"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "unavailable-inventory.yml"))
    plans = tmp_path / "plans"
    lineups = tmp_path / "lineups"
    plans.mkdir(mode=0o755)
    lineups.mkdir(mode=0o755)
    monkeypatch.setenv("OH_GOON_SHARED_PLAN_DIR", str(plans))
    monkeypatch.setenv("OH_GOON_LINEUP_DIR", str(lineups))
    before_modes = plans.stat().st_mode, lineups.stat().st_mode
    with closing(sqlite3.connect(state)) as connection:
        before = tuple(connection.iterdump())
    runtime = ApplicationRuntime.build(startup_purpose="observation")
    try:
        with pytest.raises(RuntimeQuiescing, match="cannot start background work"):
            runtime.start_background()
        assert runtime.locations.list() == []
    finally:
        runtime.shutdown()
    with closing(sqlite3.connect(state)) as connection:
        assert tuple(connection.iterdump()) == before
    assert (plans.stat().st_mode, lineups.stat().st_mode) == before_modes
    for directory in (
        "content-store",
        "extensions",
        "plan-tools",
        "run-workspaces",
        "config",
        "data",
    ):
        assert not (tmp_path / directory).exists()
    assert database.durable_revision() == 0


def test_observation_preserves_native_journal_admission_and_never_repairs_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    LocalDatabase(path, journal_mode="DELETE")
    assert Journal(LocalDatabase(path, read_only=True)).read_view().revision == 0

    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    monkeypatch.setattr(
        "oh_goon.runtime._database_policy._filesystem_type_for_path", lambda _path: "9p"
    )

    with pytest.raises(StateConflict, match="journal.*observation"):
        LocalDatabase(path, read_only=True)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_workflow_startup_initializes_saved_scope_once_and_preserves_later_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.db"
    league = tmp_path / "league.yml"
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    monkeypatch.setenv("OH_GOON_DB", str(path))
    monkeypatch.setenv("OH_GOON_LEAGUE_FILE", str(league))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    league.write_text(json.dumps({"version": 1, "folders": [str(original)]}))

    fresh = ApplicationRuntime.build(startup_purpose="workflow")
    try:
        assert fresh.locations.list() == [original]
    finally:
        fresh.shutdown()

    league.write_text(json.dumps({"version": 1, "folders": [str(replacement)]}))
    workflow = ApplicationRuntime.build(startup_purpose="workflow")
    try:
        assert workflow.locations.list() == [original]
    finally:
        workflow.shutdown()

    application = ApplicationRuntime.build()
    try:
        assert application.locations.list() == [replacement]
    finally:
        application.shutdown()
