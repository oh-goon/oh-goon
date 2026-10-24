from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from time import perf_counter
from typing import cast

from oh_goon.core.execution_observability import RepositoryRunObservation
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import ScoutAnalyticalDatabase
from oh_goon.scout.evidence import (
    ScoutEnvironmentKey,
    ScoutEvidenceSnapshot,
    ScoutStepObservation,
)
from oh_goon.scout.policy import SCOUT_MEASUREMENT_SCHEMA_VERSION

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scout_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    measurement_schema_version INTEGER NOT NULL,
    journal_revision INTEGER NOT NULL CHECK (journal_revision >= 0)
);

CREATE TABLE IF NOT EXISTS scout_run_observations (
    run_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    run_status TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    repository_status TEXT NOT NULL,
    plan_id TEXT,
    plan_name TEXT,
    duration_seconds REAL,
    PRIMARY KEY (run_id, repository_id)
);
CREATE INDEX IF NOT EXISTS scout_run_time
    ON scout_run_observations(started_at, run_id, repository_id);
CREATE INDEX IF NOT EXISTS scout_run_repository_time
    ON scout_run_observations(repository_id, started_at);
CREATE INDEX IF NOT EXISTS scout_run_repository_name_time
    ON scout_run_observations(repository_name COLLATE NOCASE, started_at);
CREATE INDEX IF NOT EXISTS scout_run_repository_id_search
    ON scout_run_observations(repository_id COLLATE NOCASE, started_at);
CREATE INDEX IF NOT EXISTS scout_run_plan_name_time
    ON scout_run_observations(plan_name COLLATE NOCASE, started_at);
CREATE INDEX IF NOT EXISTS scout_run_plan_id_search
    ON scout_run_observations(plan_id COLLATE NOCASE, started_at);

CREATE TABLE IF NOT EXISTS scout_step_observations (
    run_id TEXT NOT NULL,
    repository_id TEXT NOT NULL,
    goon_source_sha256 TEXT NOT NULL,
    step_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    repository_name TEXT NOT NULL,
    repository_status TEXT NOT NULL,
    plan_id TEXT,
    plan_name TEXT,
    goon_name TEXT NOT NULL,
    step_status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    eligible_attempts INTEGER NOT NULL,
    cpu_observed_attempts INTEGER NOT NULL,
    memory_observed_attempts INTEGER NOT NULL,
    observed_cpu_seconds REAL NOT NULL,
    complete_cpu_seconds REAL,
    observed_peak_memory_bytes INTEGER,
    complete_peak_memory_bytes INTEGER,
    duration_seconds REAL,
    environment_key_json TEXT NOT NULL,
    PRIMARY KEY (run_id, repository_id, goon_source_sha256, step_id)
);
CREATE INDEX IF NOT EXISTS scout_step_time
    ON scout_step_observations(started_at, run_id, repository_id);
CREATE INDEX IF NOT EXISTS scout_step_repository_time
    ON scout_step_observations(repository_id, started_at);
CREATE INDEX IF NOT EXISTS scout_step_goon_time
    ON scout_step_observations(goon_name, goon_source_sha256, started_at);
CREATE INDEX IF NOT EXISTS scout_step_goon_name_time
    ON scout_step_observations(goon_name COLLATE NOCASE, started_at);
"""


_DROP_SCHEMA = """
DROP TABLE IF EXISTS scout_step_observations;
DROP TABLE IF EXISTS scout_run_observations;
DROP TABLE IF EXISTS scout_metadata;
"""


@dataclass(frozen=True, slots=True)
class ScoutStoreStats:
    journal_revision: int
    run_observations: int
    step_observations: int
    snapshot_cache_entries: int
    snapshot_cache_hits: int
    snapshot_cache_misses: int
    last_snapshot_load_ms: float | None
    analytical_storage_bytes: int


class ScoutSnapshotCache:
    """Bounded decoded-window cache refreshed from changed Scout observations."""

    def __init__(self) -> None:
        self._items: OrderedDict[tuple[str, str, int], ScoutEvidenceSnapshot] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.last_load_ms: float | None = None

    def lookup(self, key: tuple[str, str, int]) -> ScoutEvidenceSnapshot | None:
        cached = self._items.get(key)
        if cached is None:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return cached

    def store(
        self, key: tuple[str, str, int], snapshot: ScoutEvidenceSnapshot, *, load_ms: float
    ) -> None:
        self.last_load_ms = load_ms
        self._items[key] = snapshot
        self._items.move_to_end(key)
        while len(self._items) > 8:
            self._items.popitem(last=False)

    def refresh(
        self, *, run_ids: tuple[str, ...], snapshot: ScoutEvidenceSnapshot, journal_revision: int
    ) -> None:
        if not self._items:
            return
        changed = set(run_ids)
        refreshed: OrderedDict[tuple[str, str, int], ScoutEvidenceSnapshot] = OrderedDict()
        for (start, end, _previous_revision), cached in self._items.items():
            if not changed:
                refreshed[(start, end, journal_revision)] = ScoutEvidenceSnapshot(
                    journal_revision=journal_revision, runs=cached.runs, steps=cached.steps
                )
                continue
            start_at = datetime.fromisoformat(start)
            end_at = datetime.fromisoformat(end)
            runs = [item for item in cached.runs if item.run_id not in changed]
            runs.extend(item for item in snapshot.runs if start_at <= item.started_at < end_at)
            steps = [item for item in cached.steps if item.run_id not in changed]
            steps.extend(item for item in snapshot.steps if start_at <= item.started_at < end_at)
            refreshed[(start, end, journal_revision)] = ScoutEvidenceSnapshot(
                journal_revision=journal_revision,
                runs=tuple(sorted(runs, key=_run_sort_key)),
                steps=tuple(sorted(steps, key=_step_sort_key)),
            )
        self._items = refreshed

    def clear(self, *, reset_latency: bool = False) -> None:
        self._items.clear()
        if reset_latency:
            self.last_load_ms = None

    def entry_count(self) -> int:
        return len(self._items)


class ScoutObservationStore:
    """Disposable Scout-owned analytical rows inside the isolated analytical sidecar."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = ScoutAnalyticalDatabase(database.path)
        self._lock = Lock()
        self._snapshot_cache = ScoutSnapshotCache()
        self._last_prune_bucket: str | None = None
        self._initialize()

    def journal_revision(self) -> int:
        with self._database.transaction(track_revision=False) as connection:
            row = connection.execute(
                "SELECT measurement_schema_version, journal_revision FROM scout_metadata WHERE singleton = 1"
            ).fetchone()
        if row is None:
            return 0
        if int(row["measurement_schema_version"]) != SCOUT_MEASUREMENT_SCHEMA_VERSION:
            return 0
        return int(row["journal_revision"])

    def stats(self) -> ScoutStoreStats:
        with self._database.transaction(track_revision=False) as connection:
            metadata = connection.execute(
                "SELECT journal_revision FROM scout_metadata WHERE singleton = 1"
            ).fetchone()
            run_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM scout_run_observations"
                ).fetchone()["count"]
            )
            step_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM scout_step_observations"
                ).fetchone()["count"]
            )
        return ScoutStoreStats(
            journal_revision=int(metadata["journal_revision"]) if metadata is not None else 0,
            run_observations=run_count,
            step_observations=step_count,
            snapshot_cache_entries=self._snapshot_cache.entry_count(),
            snapshot_cache_hits=self._snapshot_cache.hits,
            snapshot_cache_misses=self._snapshot_cache.misses,
            last_snapshot_load_ms=self._snapshot_cache.last_load_ms,
            analytical_storage_bytes=self._database.storage_bytes(),
        )

    def reset(self) -> None:
        with self._lock, self._database.transaction(write=True, track_revision=False) as connection:
            connection.execute("DELETE FROM scout_step_observations")
            connection.execute("DELETE FROM scout_run_observations")
            connection.execute("DELETE FROM scout_metadata")
            self._snapshot_cache.clear(reset_latency=True)
            self._last_prune_bucket = None
        self._database.truncate_wal()

    def replace_changed_runs(
        self,
        *,
        run_ids: tuple[str, ...],
        snapshot: ScoutEvidenceSnapshot,
        journal_revision: int,
        run_retain_after: datetime,
        step_retain_after: datetime,
    ) -> int:
        retained = _retained_snapshot(
            snapshot,
            journal_revision=journal_revision,
            run_retain_after=run_retain_after,
            step_retain_after=step_retain_after,
        )
        with self._lock, self._database.transaction(write=True, track_revision=False) as connection:
            current_revision = _metadata_revision_in(connection)
            if current_revision >= journal_revision:
                return current_revision
            _replace_observation_rows(connection, run_ids=run_ids, snapshot=retained)
            _prune_rows(
                connection,
                run_retain_after=run_retain_after,
                step_retain_after=step_retain_after,
            )
            _write_metadata(connection, journal_revision=journal_revision)
            self._snapshot_cache.refresh(
                run_ids=run_ids, snapshot=retained, journal_revision=journal_revision
            )
            return journal_revision

    def prune_if_due(
        self, *, now: datetime, run_retain_after: datetime, step_retain_after: datetime
    ) -> None:
        bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
        with self._lock:
            if self._last_prune_bucket == bucket:
                return
            with self._database.transaction(write=True, track_revision=False) as connection:
                steps = connection.execute(
                    "DELETE FROM scout_step_observations WHERE started_at < ?",
                    (step_retain_after.isoformat(),),
                ).rowcount
                runs = connection.execute(
                    "DELETE FROM scout_run_observations WHERE started_at < ?",
                    (run_retain_after.isoformat(),),
                ).rowcount
            self._last_prune_bucket = bucket
            if steps or runs:
                self._snapshot_cache.clear()
            self._database.truncate_wal()

    def load_snapshot(
        self, *, start_at: datetime, end_at: datetime, journal_revision: int
    ) -> ScoutEvidenceSnapshot:
        start = start_at.isoformat()
        end = end_at.isoformat()
        cache_key = (start, end, journal_revision)
        with self._lock:
            cached = self._snapshot_cache.lookup(cache_key)
            if cached is not None:
                return cached
            load_started = perf_counter()
            with self._database.transaction(track_revision=False) as connection:
                runs = connection.execute(
                    """
                    SELECT run_id, repository_id, started_at, run_status, repository_name,
                           repository_status, plan_id, plan_name, duration_seconds
                    FROM scout_run_observations
                    WHERE started_at >= ? AND started_at < ?
                    ORDER BY started_at, run_id, repository_id
                    """,
                    (start, end),
                ).fetchall()
                steps = connection.execute(
                    """
                    SELECT run_id, repository_id, goon_source_sha256, step_id, started_at,
                           repository_name, repository_status, plan_id, plan_name, goon_name,
                           step_status, attempt_count, eligible_attempts, cpu_observed_attempts,
                           memory_observed_attempts, observed_cpu_seconds, complete_cpu_seconds,
                           observed_peak_memory_bytes, complete_peak_memory_bytes, duration_seconds, environment_key_json
                    FROM scout_step_observations
                    WHERE started_at >= ? AND started_at < ?
                    ORDER BY started_at, run_id, repository_id, goon_name, step_id
                    """,
                    (start, end),
                ).fetchall()
            snapshot = ScoutEvidenceSnapshot(
                journal_revision=journal_revision,
                runs=tuple(_decode_run(row) for row in runs),
                steps=tuple(_decode_step(row) for row in steps),
            )
            self._snapshot_cache.store(
                cache_key, snapshot, load_ms=(perf_counter() - load_started) * 1000
            )
            return snapshot

    def _initialize(self) -> None:
        try:
            self._initialize_once()
        except sqlite3.DatabaseError as error:
            if not self._database.recover_if_corrupt(error):
                raise
            self._initialize_once()

    def _initialize_once(self) -> None:
        with self._database.transaction(write=True, track_revision=False) as connection:
            connection.executescript(_SCHEMA)
            row = connection.execute(
                "SELECT measurement_schema_version FROM scout_metadata WHERE singleton = 1"
            ).fetchone()
            if (
                row is not None
                and int(row["measurement_schema_version"]) != SCOUT_MEASUREMENT_SCHEMA_VERSION
            ):
                connection.executescript(_DROP_SCHEMA)
                connection.executescript(_SCHEMA)


def _retained_snapshot(
    snapshot: ScoutEvidenceSnapshot,
    *,
    journal_revision: int,
    run_retain_after: datetime,
    step_retain_after: datetime,
) -> ScoutEvidenceSnapshot:
    return ScoutEvidenceSnapshot(
        journal_revision=journal_revision,
        runs=tuple(item for item in snapshot.runs if item.started_at >= run_retain_after),
        steps=tuple(item for item in snapshot.steps if item.started_at >= step_retain_after),
    )


def _replace_observation_rows(
    connection: sqlite3.Connection, *, run_ids: tuple[str, ...], snapshot: ScoutEvidenceSnapshot
) -> None:
    for run_id in run_ids:
        connection.execute("DELETE FROM scout_step_observations WHERE run_id = ?", (run_id,))
        connection.execute("DELETE FROM scout_run_observations WHERE run_id = ?", (run_id,))
    connection.executemany(
        """
        INSERT INTO scout_run_observations(
            run_id, repository_id, started_at, run_status, repository_name,
            repository_status, plan_id, plan_name, duration_seconds
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [_run_row(item) for item in snapshot.runs],
    )
    connection.executemany(
        """
        INSERT INTO scout_step_observations(
            run_id, repository_id, goon_source_sha256, step_id, started_at,
            repository_name, repository_status, plan_id, plan_name, goon_name,
            step_status, attempt_count, eligible_attempts, cpu_observed_attempts,
            memory_observed_attempts, observed_cpu_seconds, complete_cpu_seconds,
            observed_peak_memory_bytes, complete_peak_memory_bytes, duration_seconds, environment_key_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [_step_row(item) for item in snapshot.steps],
    )


def _prune_rows(
    connection: sqlite3.Connection, *, run_retain_after: datetime, step_retain_after: datetime
) -> None:
    connection.execute(
        "DELETE FROM scout_step_observations WHERE started_at < ?",
        (step_retain_after.isoformat(),),
    )
    connection.execute(
        "DELETE FROM scout_run_observations WHERE started_at < ?",
        (run_retain_after.isoformat(),),
    )


def _metadata_revision_in(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT journal_revision FROM scout_metadata WHERE singleton = 1"
    ).fetchone()
    return int(row["journal_revision"]) if row is not None else 0


def _write_metadata(connection: sqlite3.Connection, *, journal_revision: int) -> None:
    connection.execute(
        """
        INSERT INTO scout_metadata(singleton, measurement_schema_version, journal_revision)
        VALUES (1, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET
            measurement_schema_version = excluded.measurement_schema_version,
            journal_revision = excluded.journal_revision
        """,
        (SCOUT_MEASUREMENT_SCHEMA_VERSION, journal_revision),
    )


def _run_sort_key(item: RepositoryRunObservation) -> tuple[object, ...]:
    return (item.started_at, item.run_id, item.repository_id)


def _step_sort_key(item: ScoutStepObservation) -> tuple[object, ...]:
    return (item.started_at, item.run_id, item.repository_id, item.goon_name, item.step_id)


def _run_row(item: RepositoryRunObservation) -> tuple[object, ...]:
    return (
        item.run_id,
        item.repository_id,
        item.started_at.isoformat(),
        item.run_status.value,
        item.repository_name,
        item.repository_status.value,
        item.plan_id,
        item.plan_name,
        item.duration_seconds,
    )


def _step_row(item: ScoutStepObservation) -> tuple[object, ...]:
    return (
        item.run_id,
        item.repository_id,
        item.goon_source_sha256,
        item.step_id,
        item.started_at.isoformat(),
        item.repository_name,
        item.repository_status.value,
        item.plan_id,
        item.plan_name,
        item.goon_name,
        item.step_status.value,
        item.attempt_count,
        item.eligible_attempts,
        item.cpu_observed_attempts,
        item.memory_observed_attempts,
        item.observed_cpu_seconds,
        item.complete_cpu_seconds,
        item.observed_peak_memory_bytes,
        item.complete_peak_memory_bytes,
        item.duration_seconds,
        json.dumps(item.environment_key, separators=(",", ":")),
    )


def _decode_run(row: sqlite3.Row) -> RepositoryRunObservation:
    return RepositoryRunObservation(
        run_id=str(row["run_id"]),
        started_at=datetime.fromisoformat(str(row["started_at"])),
        run_status=GameTapeStatus(str(row["run_status"])),
        repository_id=str(row["repository_id"]),
        repository_name=str(row["repository_name"]),
        repository_status=GameTapeStatus(str(row["repository_status"])),
        plan_id=str(row["plan_id"]) if row["plan_id"] is not None else None,
        plan_name=str(row["plan_name"]) if row["plan_name"] is not None else None,
        duration_seconds=(
            float(row["duration_seconds"]) if row["duration_seconds"] is not None else None
        ),
    )


def _decode_step(row: sqlite3.Row) -> ScoutStepObservation:
    return ScoutStepObservation(
        run_id=str(row["run_id"]),
        started_at=datetime.fromisoformat(str(row["started_at"])),
        repository_id=str(row["repository_id"]),
        repository_name=str(row["repository_name"]),
        repository_status=GameTapeStatus(str(row["repository_status"])),
        plan_id=str(row["plan_id"]) if row["plan_id"] is not None else None,
        plan_name=str(row["plan_name"]) if row["plan_name"] is not None else None,
        goon_name=str(row["goon_name"]),
        goon_source_sha256=str(row["goon_source_sha256"]),
        step_id=str(row["step_id"]),
        step_status=GameTapeStatus(str(row["step_status"])),
        attempt_count=int(row["attempt_count"]),
        eligible_attempts=int(row["eligible_attempts"]),
        cpu_observed_attempts=int(row["cpu_observed_attempts"]),
        memory_observed_attempts=int(row["memory_observed_attempts"]),
        observed_cpu_seconds=float(row["observed_cpu_seconds"]),
        complete_cpu_seconds=(
            float(row["complete_cpu_seconds"]) if row["complete_cpu_seconds"] is not None else None
        ),
        observed_peak_memory_bytes=(
            int(row["observed_peak_memory_bytes"])
            if row["observed_peak_memory_bytes"] is not None
            else None
        ),
        complete_peak_memory_bytes=(
            int(row["complete_peak_memory_bytes"])
            if row["complete_peak_memory_bytes"] is not None
            else None
        ),
        duration_seconds=(
            float(row["duration_seconds"]) if row["duration_seconds"] is not None else None
        ),
        # The sidecar's sole writer serializes ScoutStepObservation.environment_key.
        environment_key=cast(
            ScoutEnvironmentKey, _deep_tuple(json.loads(str(row["environment_key_json"])))
        ),
    )


def _deep_tuple(value: object) -> tuple[object, ...] | object:
    if isinstance(value, list):
        return tuple(_deep_tuple(item) for item in value)
    return value
