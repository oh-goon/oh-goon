from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import scout_analytical_database_path
from oh_goon.scout.evidence import (
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
)
from oh_goon.scout.search import ScoutSearchReader
from oh_goon.scout.store import ScoutObservationStore


def _snapshot(*, revision: int, run_id: str, started_at) -> ScoutEvidenceSnapshot:
    run = RepositoryRunObservation(
        run_id=run_id,
        started_at=started_at,
        run_status=GameTapeStatus.PASSED,
        repository_id="repo-1",
        repository_name="repository-one",
        repository_status=GameTapeStatus.PASSED,
        plan_id="plan-1",
        plan_name="Plan One",
        duration_seconds=12.5,
    )
    step = ScoutStepObservation(
        run_id=run_id,
        started_at=started_at,
        repository_id="repo-1",
        repository_name="repository-one",
        repository_status=GameTapeStatus.PASSED,
        plan_id="plan-1",
        plan_name="Plan One",
        goon_name="python/python-tests",
        goon_source_sha256="a" * 64,
        step_id="pytest",
        step_status=GameTapeStatus.PASSED,
        attempt_count=1,
        eligible_attempts=1,
        cpu_observed_attempts=1,
        memory_observed_attempts=1,
        observed_cpu_seconds=2.5,
        complete_cpu_seconds=2.5,
        observed_peak_memory_bytes=256 * 1024 * 1024,
        complete_peak_memory_bytes=256 * 1024 * 1024,
        environment_key=("local", "isolation", "c" * 64, "toolchain", ((2000, 536870912),)),
        duration_seconds=4.25,
    )
    return ScoutEvidenceSnapshot(journal_revision=revision, runs=(run,), steps=(step,))


def test_scout_store_round_trips_derived_observations_and_watermark(tmp_path: Path) -> None:
    now = utc_now()
    store = ScoutObservationStore(LocalDatabase(tmp_path / "state.sqlite3"))
    snapshot = _snapshot(revision=9, run_id="run-1", started_at=now)

    store.replace_changed_runs(
        run_ids=("run-1",),
        snapshot=snapshot,
        journal_revision=9,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    loaded = store.load_snapshot(
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        journal_revision=9,
    )

    assert store.journal_revision() == 9
    assert loaded == snapshot


def test_scout_store_replaces_one_changed_run_without_touching_other_runs(tmp_path: Path) -> None:
    now = utc_now()
    store = ScoutObservationStore(LocalDatabase(tmp_path / "state.sqlite3"))
    first = _snapshot(revision=1, run_id="run-1", started_at=now)
    second = _snapshot(revision=1, run_id="run-2", started_at=now)
    store.replace_changed_runs(
        run_ids=("run-1", "run-2"),
        snapshot=ScoutEvidenceSnapshot(
            journal_revision=1,
            runs=first.runs + second.runs,
            steps=first.steps + second.steps,
        ),
        journal_revision=1,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    changed_step = replace(first.steps[0], observed_cpu_seconds=7.0, complete_cpu_seconds=7.0)
    changed = ScoutEvidenceSnapshot(
        journal_revision=2,
        runs=first.runs,
        steps=(changed_step,),
    )
    store.replace_changed_runs(
        run_ids=("run-1",),
        snapshot=changed,
        journal_revision=2,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    loaded = store.load_snapshot(
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        journal_revision=2,
    )

    assert {item.run_id for item in loaded.runs} == {"run-1", "run-2"}
    cpu_by_run = {item.run_id: item.observed_cpu_seconds for item in loaded.steps}
    assert cpu_by_run == {"run-1": 7.0, "run-2": 2.5}


def test_scout_store_retains_run_history_after_detailed_step_retention(tmp_path: Path) -> None:
    now = utc_now()
    old = now - timedelta(days=91)
    store = ScoutObservationStore(LocalDatabase(tmp_path / "state.sqlite3"))
    snapshot = _snapshot(revision=3, run_id="run-old", started_at=old)

    store.replace_changed_runs(
        run_ids=("run-old",),
        snapshot=snapshot,
        journal_revision=3,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    loaded = store.load_snapshot(
        start_at=now - timedelta(days=100),
        end_at=now + timedelta(hours=1),
        journal_revision=3,
    )

    assert store.journal_revision() == 3
    assert [item.run_id for item in loaded.runs] == ["run-old"]
    assert loaded.steps == ()


def test_scout_store_prunes_run_history_after_run_retention(tmp_path: Path) -> None:
    now = utc_now()
    old = now - timedelta(days=366)
    store = ScoutObservationStore(LocalDatabase(tmp_path / "state.sqlite3"))
    snapshot = _snapshot(revision=4, run_id="run-very-old", started_at=old)

    store.replace_changed_runs(
        run_ids=("run-very-old",),
        snapshot=snapshot,
        journal_revision=4,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    loaded = store.load_snapshot(
        start_at=now - timedelta(days=400),
        end_at=now + timedelta(hours=1),
        journal_revision=4,
    )

    assert loaded.runs == ()
    assert loaded.steps == ()


def test_scout_store_writes_do_not_advance_canonical_durable_revision(tmp_path: Path) -> None:
    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    before = database.durable_revision()
    store = ScoutObservationStore(database)
    after_schema = database.durable_revision()

    store.replace_changed_runs(
        run_ids=("run-1",),
        snapshot=_snapshot(revision=5, run_id="run-1", started_at=now),
        journal_revision=5,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    assert before == after_schema == database.durable_revision()


def test_scout_store_searches_repository_plan_and_goon_by_bounded_prefix(tmp_path: Path) -> None:
    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    snapshot = _snapshot(revision=6, run_id="run-search", started_at=now)
    store.replace_changed_runs(
        run_ids=("run-search",),
        snapshot=snapshot,
        journal_revision=6,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    search = ScoutSearchReader(database)
    repository_total, repositories = search.search(
        query="repos",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=20,
    )
    plan_total, plans = search.search(
        query="Plan",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=20,
    )
    goon_total, goons = search.search(
        query="python/",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=20,
    )

    assert repository_total == 1
    assert [(item.entity_kind, item.key) for item in repositories] == [("REPOSITORY", "repo-1")]
    assert plan_total == 1
    assert [(item.entity_kind, item.key) for item in plans] == [("PLAN", "plan-1")]
    assert goon_total == 1
    assert [(item.entity_kind, item.key) for item in goons] == [("GOON", "python/python-tests")]


def test_scout_store_search_paginates_deterministically(tmp_path: Path) -> None:
    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    store.replace_changed_runs(
        run_ids=("run-search",),
        snapshot=_snapshot(revision=7, run_id="run-search", started_at=now),
        journal_revision=7,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    search = ScoutSearchReader(database)
    first_total, first = search.search(
        query="p",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=1,
        offset=0,
    )
    second_total, second = search.search(
        query="p",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=1,
        offset=1,
    )

    assert first_total == second_total == 2
    assert len(first) == len(second) == 1
    assert first[0].key != second[0].key


def test_scout_store_search_does_not_treat_sql_wildcards_as_user_wildcards(tmp_path: Path) -> None:
    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    snapshot = _snapshot(revision=7, run_id="run-search", started_at=now)
    store.replace_changed_runs(
        run_ids=("run-search",),
        snapshot=snapshot,
        journal_revision=7,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    search = ScoutSearchReader(database)
    total, items = search.search(
        query="%",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=20,
    )

    assert total == 0
    assert items == ()


def test_scout_store_reuses_exact_window_snapshot_until_materialized_state_changes(
    tmp_path: Path,
) -> None:
    now = utc_now().replace(second=0, microsecond=0)
    store = ScoutObservationStore(LocalDatabase(tmp_path / "state.sqlite3"))
    snapshot = _snapshot(revision=8, run_id="run-cache", started_at=now)
    store.replace_changed_runs(
        run_ids=("run-cache",),
        snapshot=snapshot,
        journal_revision=8,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    first = store.load_snapshot(
        start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1), journal_revision=8
    )
    second = store.load_snapshot(
        start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1), journal_revision=8
    )
    assert second is first
    stats = store.stats()
    assert stats.snapshot_cache_entries == 1
    assert stats.snapshot_cache_hits == 1
    assert stats.snapshot_cache_misses == 1
    assert stats.last_snapshot_load_ms is not None
    assert stats.last_snapshot_load_ms >= 0

    changed = ScoutEvidenceSnapshot(
        journal_revision=9,
        runs=snapshot.runs,
        steps=(replace(snapshot.steps[0], observed_cpu_seconds=9.0, complete_cpu_seconds=9.0),),
    )
    store.replace_changed_runs(
        run_ids=("run-cache",),
        snapshot=changed,
        journal_revision=9,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    third = store.load_snapshot(
        start_at=now - timedelta(hours=1), end_at=now + timedelta(hours=1), journal_revision=9
    )
    assert third is not first
    assert third.steps[0].complete_cpu_seconds == 9.0
    refreshed_stats = store.stats()
    assert refreshed_stats.snapshot_cache_misses == 1
    assert refreshed_stats.snapshot_cache_hits == 2


def test_scout_store_rebuilds_disposable_tables_when_measurement_schema_changes(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    sidecar = scout_analytical_database_path(database.path)
    with sqlite3.connect(sidecar) as connection:
        connection.executescript(
            """
            CREATE TABLE scout_metadata (
                singleton INTEGER PRIMARY KEY,
                measurement_schema_version INTEGER NOT NULL,
                journal_revision INTEGER NOT NULL
            );
            CREATE TABLE scout_run_observations (
                run_id TEXT NOT NULL, repository_id TEXT NOT NULL, started_at TEXT NOT NULL,
                run_status TEXT NOT NULL, repository_name TEXT NOT NULL, repository_status TEXT NOT NULL,
                plan_id TEXT, plan_name TEXT, PRIMARY KEY (run_id, repository_id)
            );
            CREATE TABLE scout_step_observations (
                run_id TEXT NOT NULL, repository_id TEXT NOT NULL, goon_source_sha256 TEXT NOT NULL,
                step_id TEXT NOT NULL, started_at TEXT NOT NULL, repository_name TEXT NOT NULL,
                repository_status TEXT NOT NULL, plan_id TEXT, plan_name TEXT, goon_name TEXT NOT NULL,
                step_status TEXT NOT NULL, attempt_count INTEGER NOT NULL, eligible_attempts INTEGER NOT NULL,
                cpu_observed_attempts INTEGER NOT NULL, memory_observed_attempts INTEGER NOT NULL,
                observed_cpu_seconds REAL NOT NULL, complete_cpu_seconds REAL,
                observed_peak_memory_bytes INTEGER, complete_peak_memory_bytes INTEGER,
                environment_key_json TEXT NOT NULL,
                PRIMARY KEY (run_id, repository_id, goon_source_sha256, step_id)
            );
            INSERT INTO scout_metadata(singleton, measurement_schema_version, journal_revision) VALUES (1, 1, 99);
            """
        )

    store = ScoutObservationStore(database)
    with sqlite3.connect(sidecar) as connection:
        connection.row_factory = sqlite3.Row
        run_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(scout_run_observations)")
        }
        step_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(scout_step_observations)")
        }

    assert store.journal_revision() == 0
    assert "duration_seconds" in run_columns
    assert "duration_seconds" in step_columns


def test_scout_search_deduplicates_renamed_repository_and_plan_by_stable_identity(
    tmp_path: Path,
) -> None:
    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    old = _snapshot(revision=9, run_id="run-old", started_at=now - timedelta(minutes=10))
    new = _snapshot(revision=9, run_id="run-new", started_at=now)
    old_run = replace(old.runs[0], repository_name="old-repo-name", plan_name="Old Plan Name")
    new_run = replace(new.runs[0], repository_name="new-repo-name", plan_name="New Plan Name")
    old_step = replace(old.steps[0], repository_name="old-repo-name", plan_name="Old Plan Name")
    new_step = replace(new.steps[0], repository_name="new-repo-name", plan_name="New Plan Name")
    store.replace_changed_runs(
        run_ids=("run-old", "run-new"),
        snapshot=ScoutEvidenceSnapshot(
            journal_revision=9, runs=(old_run, new_run), steps=(old_step, new_step)
        ),
        journal_revision=9,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    search = ScoutSearchReader(database)

    repo_total, repos = search.search(
        query="old-repo",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=20,
    )
    plan_total, plans = search.search(
        query="Old Plan",
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        limit=20,
    )

    assert repo_total == 1 and repos[0].key == "repo-1"
    assert repos[0].display_name == "new-repo-name"
    assert plan_total == 1 and plans[0].key == "plan-1"
    assert plans[0].display_name == "New Plan Name"


def test_scout_store_prunes_expired_rows_even_without_new_journal_revision(tmp_path: Path) -> None:
    now = utc_now()
    old = now - timedelta(days=400)
    store = ScoutObservationStore(LocalDatabase(tmp_path / "state.sqlite3"))
    snapshot = _snapshot(revision=20, run_id="run-expired", started_at=old)
    store.replace_changed_runs(
        run_ids=("run-expired",),
        snapshot=snapshot,
        journal_revision=20,
        run_retain_after=old - timedelta(days=1),
        step_retain_after=old - timedelta(days=1),
    )
    assert store.stats().run_observations == 1
    assert store.stats().step_observations == 1

    store.prune_if_due(
        now=now,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    stats = store.stats()
    assert stats.run_observations == 0
    assert stats.step_observations == 0


def test_scout_store_retention_prune_runs_at_most_once_per_hour(tmp_path: Path) -> None:
    now = utc_now().replace(minute=5, second=0, microsecond=0)
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    snapshot = _snapshot(revision=21, run_id="run-old", started_at=now - timedelta(days=100))
    store.replace_changed_runs(
        run_ids=("run-old",),
        snapshot=snapshot,
        journal_revision=21,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=365),
    )
    store.prune_if_due(
        now=now,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    assert store.stats().step_observations == 0

    # A second call in the same hour is intentionally a no-op, even with a stricter cutoff.
    fresh = _snapshot(revision=22, run_id="run-fresh", started_at=now - timedelta(days=1))
    store.replace_changed_runs(
        run_ids=("run-fresh",),
        snapshot=fresh,
        journal_revision=22,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    store.prune_if_due(
        now=now + timedelta(minutes=10),
        run_retain_after=now,
        step_retain_after=now,
    )
    assert store.stats().run_observations == 2


def test_scout_materialization_uses_sidecar_and_never_creates_tables_in_canonical_database(
    tmp_path: Path,
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    now = utc_now()
    store = ScoutObservationStore(database)
    store.replace_changed_runs(
        run_ids=("run-1",),
        snapshot=_snapshot(revision=30, run_id="run-1", started_at=now),
        journal_revision=30,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    with database.transaction(track_revision=False) as connection:
        scout_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'scout_%'"
        ).fetchall()
    assert scout_tables == []
    assert scout_analytical_database_path(database.path).exists()


def test_scout_sidecar_write_completes_while_canonical_database_write_lock_is_held(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    database = LocalDatabase(tmp_path / "state.sqlite3")
    now = utc_now()
    store = ScoutObservationStore(database)

    def materialize() -> None:
        store.replace_changed_runs(
            run_ids=("run-1",),
            snapshot=_snapshot(revision=31, run_id="run-1", started_at=now),
            journal_revision=31,
            run_retain_after=now - timedelta(days=365),
            step_retain_after=now - timedelta(days=90),
        )

    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations(path, position, added_at) VALUES (?, ?, ?)",
            (str(tmp_path / "repo"), 0, now.isoformat()),
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(materialize)
            future.result(timeout=2.0)

    assert store.journal_revision() == 31


def test_scout_sidecar_never_moves_processed_revision_backward(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite3")
    now = utc_now()
    first_worker = ScoutObservationStore(database)
    second_worker = ScoutObservationStore(database)
    revision_11 = _snapshot(revision=11, run_id="run-1", started_at=now)
    changed_step = replace(
        revision_11.steps[0], observed_cpu_seconds=11.0, complete_cpu_seconds=11.0
    )
    first_worker.replace_changed_runs(
        run_ids=("run-1",),
        snapshot=ScoutEvidenceSnapshot(
            journal_revision=11, runs=revision_11.runs, steps=(changed_step,)
        ),
        journal_revision=11,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    stale = _snapshot(revision=10, run_id="run-1", started_at=now)
    processed = second_worker.replace_changed_runs(
        run_ids=("run-1",),
        snapshot=stale,
        journal_revision=10,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )
    loaded = second_worker.load_snapshot(
        start_at=now - timedelta(hours=1),
        end_at=now + timedelta(hours=1),
        journal_revision=processed,
    )

    assert processed == 11
    assert second_worker.journal_revision() == 11
    assert loaded.journal_revision == 11
    assert loaded.steps[0].complete_cpu_seconds == 11.0


def test_scout_run_reader_uses_constant_query_count_for_multiple_steps(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from oh_goon.scout.database import ScoutAnalyticalDatabase
    from oh_goon.scout.run_reader import ScoutRunReader

    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    previous = _snapshot(revision=1, run_id="run-previous", started_at=now - timedelta(minutes=10))
    current = _snapshot(revision=2, run_id="run-current", started_at=now)
    previous_steps = tuple(
        replace(previous.steps[0], step_id=f"step-{index}") for index in range(4)
    )
    current_steps = tuple(replace(current.steps[0], step_id=f"step-{index}") for index in range(4))
    store.replace_changed_runs(
        run_ids=("run-previous", "run-current"),
        snapshot=ScoutEvidenceSnapshot(
            journal_revision=2,
            runs=previous.runs + current.runs,
            steps=previous_steps + current_steps,
        ),
        journal_revision=2,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    statements: list[str] = []
    original_connect = ScoutAnalyticalDatabase._connect

    def traced_connect(self, *, write: bool):
        connection = original_connect(self, write=write)
        connection.set_trace_callback(
            lambda statement: statements.append(statement)
            if statement.lstrip().upper().startswith(("SELECT", "WITH"))
            else None
        )
        return connection

    monkeypatch.setattr(ScoutAnalyticalDatabase, "_connect", traced_connect)
    evidence = ScoutRunReader(database).read("run-current", journal_revision=2)

    assert len(evidence.repositories[0].steps) == 4
    assert all(step.comparable_runs == 1 for step in evidence.repositories[0].steps)
    assert len(statements) == 3
    assert sum("ranked_peers" in statement for statement in statements) == 1


def test_scout_run_reader_exposes_exact_run_resources_and_prior_typical_values(
    tmp_path: Path,
) -> None:
    from oh_goon.scout.run_reader import ScoutRunReader

    now = utc_now()
    database = LocalDatabase(tmp_path / "state.sqlite3")
    store = ScoutObservationStore(database)
    previous = _snapshot(revision=1, run_id="run-previous", started_at=now - timedelta(minutes=10))
    current = _snapshot(revision=2, run_id="run-current", started_at=now)
    current_step = replace(
        current.steps[0],
        observed_cpu_seconds=5.0,
        complete_cpu_seconds=5.0,
        observed_peak_memory_bytes=512 * 1024 * 1024,
        complete_peak_memory_bytes=512 * 1024 * 1024,
        duration_seconds=8.5,
    )
    store.replace_changed_runs(
        run_ids=("run-previous", "run-current"),
        snapshot=ScoutEvidenceSnapshot(
            journal_revision=2,
            runs=previous.runs + current.runs,
            steps=previous.steps + (current_step,),
        ),
        journal_revision=2,
        run_retain_after=now - timedelta(days=365),
        step_retain_after=now - timedelta(days=90),
    )

    evidence = ScoutRunReader(database).read("run-current", journal_revision=2)

    assert evidence.run_id == "run-current"
    assert len(evidence.repositories) == 1
    step = evidence.repositories[0].steps[0]
    assert step.cpu_seconds == 5.0
    assert step.peak_memory_bytes == 512 * 1024 * 1024
    assert step.typical_cpu_seconds == 2.5
    assert step.typical_peak_memory_bytes == 256 * 1024 * 1024
    assert step.cpu_difference_percent == 100
    assert step.memory_difference_percent == 100
    assert step.comparable_runs == 1
