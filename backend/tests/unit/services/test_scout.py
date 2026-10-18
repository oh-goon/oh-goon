from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from oh_goon.core.errors import InvalidRequest
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import scout_analytical_database_path
from oh_goon.scout.service import ScoutService, _window


class _Journal:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.revision = 12

    def run_details_changed_after(self, revision: int):
        self.calls.append(revision)
        return self.revision, ()


def test_disabled_scout_never_reads_execution_journal_or_creates_store(tmp_path: Path) -> None:
    journal = _Journal()
    database_path = tmp_path / "state.sqlite3"
    scout = ScoutService(journal, database=LocalDatabase(database_path), enabled=False)  # type: ignore[arg-type]

    overview = scout.overview()

    assert overview.enabled is False
    assert journal.calls == []
    with pytest.raises(InvalidRequest, match="Scout is disabled"):
        scout.repository("repo")
    assert journal.calls == []
    health = scout.health()
    assert health.enabled is False
    assert health.retained_run_observations == 0
    assert health.analytical_storage_bytes == 0
    assert journal.calls == []
    with sqlite3.connect(database_path) as connection:
        scout_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'scout_%'"
        ).fetchall()
    assert scout_tables == []
    assert not scout_analytical_database_path(database_path).exists()


def test_scout_advances_store_watermark_and_reuses_materialized_state(tmp_path: Path) -> None:
    journal = _Journal()
    scout = ScoutService(  # type: ignore[arg-type]
        journal, database=LocalDatabase(tmp_path / "state.sqlite3")
    )

    first = scout.overview()
    second = scout.overview()

    assert first.journal_revision == 12
    assert second.journal_revision == 12
    assert journal.calls == [0, 12]
    health = scout.health()
    assert health.journal_revision == 12
    assert health.processed_journal_revision == 12
    assert health.lag_revisions == 0
    assert health.analytical_storage_bytes > 0
    assert health.detail_retention_days == 90
    assert health.run_retention_days == 365


def test_scout_resets_derived_watermark_when_canonical_journal_moves_back(tmp_path: Path) -> None:
    journal = _Journal()
    scout = ScoutService(  # type: ignore[arg-type]
        journal, database=LocalDatabase(tmp_path / "state.sqlite3")
    )

    first = scout.health()
    journal.revision = 3
    second = scout.health()

    assert first.processed_journal_revision == 12
    assert second.journal_revision == 3
    assert second.processed_journal_revision == 3
    assert second.lag_revisions == 0
    assert journal.calls == [0, 12, 0]


def test_disabled_scout_search_returns_disabled_without_reading_journal(tmp_path: Path) -> None:
    journal = _Journal()
    scout = ScoutService(journal, database=LocalDatabase(tmp_path / "state.sqlite3"), enabled=False)  # type: ignore[arg-type]

    result = scout.search(query="repo")

    assert result.enabled is False
    assert result.query == "repo"
    assert result.items == ()
    assert journal.calls == []


def test_scout_search_rejects_blank_query_before_synchronizing(tmp_path: Path) -> None:
    journal = _Journal()
    scout = ScoutService(journal, database=LocalDatabase(tmp_path / "state.sqlite3"))  # type: ignore[arg-type]

    with pytest.raises(InvalidRequest, match="between 1 and 120"):
        scout.search(query="   ")

    assert journal.calls == []


def test_scout_analytical_windows_use_canonical_minute_boundaries() -> None:
    window = _window(24)

    assert window.end_at.second == 0
    assert window.end_at.microsecond == 0
    assert window.start_at.second == 0
    assert window.start_at.microsecond == 0


def test_scout_detail_rejects_irrelevant_section_before_reading_journal(tmp_path: Path) -> None:
    journal = _Journal()
    scout = ScoutService(journal, database=LocalDatabase(tmp_path / "state.sqlite3"))  # type: ignore[arg-type]

    with pytest.raises(InvalidRequest, match="Plan detail section 'versions' is not valid"):
        scout.plan("plan-a", section="versions")  # type: ignore[arg-type]

    assert journal.calls == []


def test_scout_detail_rejects_offset_without_section_before_reading_journal(tmp_path: Path) -> None:
    journal = _Journal()
    scout = ScoutService(journal, database=LocalDatabase(tmp_path / "state.sqlite3"))  # type: ignore[arg-type]

    with pytest.raises(InvalidRequest, match="offset requires an explicit section"):
        scout.repository("repo-a", offset=100)

    assert journal.calls == []


def test_scout_run_requires_canonical_journal_identity_before_sidecar_read(tmp_path: Path) -> None:
    from oh_goon.core.errors import RunNotFound

    class View:
        def required_run_detail(self, run_id: str):
            raise RunNotFound(f"Unknown fleet run: {run_id}")

    class JournalWithReadBoundary(_Journal):
        def read_view(self):
            return View()

    scout = ScoutService(
        JournalWithReadBoundary(),
        database=LocalDatabase(tmp_path / "state.sqlite3"),
    )  # type: ignore[arg-type]

    with pytest.raises(RunNotFound, match="Unknown fleet run: stale-run"):
        scout.run("stale-run")
