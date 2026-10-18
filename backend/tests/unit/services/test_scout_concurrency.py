from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

import pytest

from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.service import ScoutService


class _StableJournal:
    def __init__(self, revision: int = 42) -> None:
        self.revision = revision
        self.calls = 0
        self._lock = Lock()

    def run_details_changed_after(self, _revision: int):
        with self._lock:
            self.calls += 1
        return self.revision, ()


@pytest.mark.scale
def test_five_hundred_concurrent_overview_reads_share_one_materialized_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("oh_goon.scout.service.utc_now", lambda: fixed_now)
    journal = _StableJournal()
    scout = ScoutService(  # type: ignore[arg-type]
        journal,
        database=LocalDatabase(tmp_path / "state.sqlite3"),
    )

    with ThreadPoolExecutor(max_workers=16) as executor:
        results = tuple(executor.map(lambda _: scout.overview(hours=24, limit=20), range(500)))

    assert len(results) == 500
    assert {result.journal_revision for result in results} == {42}
    assert {result.executions for result in results} == {0}
    assert all(result.enabled for result in results)

    health = scout.health()
    assert health.processed_journal_revision == 42
    assert health.snapshot_cache_entries == 1
    assert health.snapshot_cache_misses == 1
    assert health.snapshot_cache_hits >= 499
    assert journal.calls >= 500


@pytest.mark.scale
def test_revision_churn_keeps_concurrent_reads_on_incrementally_refreshed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    monkeypatch.setattr("oh_goon.scout.service.utc_now", lambda: fixed_now)
    journal = _StableJournal(revision=1)
    scout = ScoutService(  # type: ignore[arg-type]
        journal,
        database=LocalDatabase(tmp_path / "state.sqlite3"),
    )
    first = scout.overview(hours=24, limit=20)
    assert first.journal_revision == 1

    for revision in range(2, 12):
        journal.revision = revision
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = tuple(executor.map(lambda _: scout.overview(hours=24, limit=20), range(100)))
        assert {result.journal_revision for result in results} == {revision}
        assert {result.executions for result in results} == {0}

    health = scout.health()
    assert health.processed_journal_revision == 11
    assert health.snapshot_cache_entries == 1
    assert health.snapshot_cache_misses == 1
    assert health.snapshot_cache_hits >= 1000
