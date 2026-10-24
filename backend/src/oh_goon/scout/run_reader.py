from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from statistics import median
from typing import Iterable, Sequence

from oh_goon.core.errors import OperationNotFound
from oh_goon.core.vocabulary import GameTapeStatus
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import ScoutAnalyticalDatabase
from oh_goon.scout.models import (
    ScoutRunEvidence,
    ScoutRunRepositoryEvidence,
    ScoutRunStepEvidence,
)

PeerKey = tuple[str, str, str, str, str]


class ScoutRunReader:
    """Read one canonical Run's retained resource evidence from the disposable Scout sidecar."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = ScoutAnalyticalDatabase(database.path)

    def read(self, run_id: str, *, journal_revision: int) -> ScoutRunEvidence:
        with self._database.transaction(track_revision=False) as connection:
            run_rows = connection.execute(
                "SELECT * FROM scout_run_observations WHERE run_id=? ORDER BY repository_id",
                (run_id,),
            ).fetchall()
            if not run_rows:
                raise OperationNotFound(f"No retained Scout evidence for Run {run_id!r}")
            step_rows = connection.execute(
                "SELECT * FROM scout_step_observations WHERE run_id=? ORDER BY repository_id, started_at, step_id",
                (run_id,),
            ).fetchall()
            peer_rows = connection.execute(
                """
                WITH current_steps AS (
                    SELECT DISTINCT repository_id, goon_name, goon_source_sha256, step_id, environment_key_json
                    FROM scout_step_observations
                    WHERE run_id=?
                ), ranked_peers AS (
                    SELECT
                        peer.repository_id,
                        peer.goon_name,
                        peer.goon_source_sha256,
                        peer.step_id,
                        peer.environment_key_json,
                        peer.complete_cpu_seconds,
                        peer.complete_peak_memory_bytes,
                        peer.duration_seconds,
                        ROW_NUMBER() OVER (
                            PARTITION BY peer.repository_id, peer.goon_name, peer.goon_source_sha256,
                                         peer.step_id, peer.environment_key_json
                            ORDER BY peer.started_at DESC, peer.run_id DESC
                        ) AS peer_rank
                    FROM scout_step_observations AS peer
                    INNER JOIN current_steps AS current
                        ON current.repository_id = peer.repository_id
                       AND current.goon_name = peer.goon_name
                       AND current.goon_source_sha256 = peer.goon_source_sha256
                       AND current.step_id = peer.step_id
                       AND current.environment_key_json = peer.environment_key_json
                    WHERE peer.run_id<>?
                )
                SELECT repository_id, goon_name, goon_source_sha256, step_id, environment_key_json,
                       complete_cpu_seconds, complete_peak_memory_bytes, duration_seconds
                FROM ranked_peers
                WHERE peer_rank <= 20
                ORDER BY repository_id, goon_name, goon_source_sha256, step_id, environment_key_json, peer_rank
                """,
                (run_id, run_id),
            ).fetchall()
            steps_by_repository = _group_rows_by_repository(step_rows)
            peers_by_key = _group_peer_rows(peer_rows)
            repositories = tuple(
                _repository_evidence(
                    row,
                    steps_by_repository.get(str(row["repository_id"]), ()),
                    peers_by_key,
                )
                for row in run_rows
            )
        return ScoutRunEvidence(
            enabled=True,
            journal_revision=journal_revision,
            run_id=run_id,
            repositories=repositories,
        )


def _group_rows_by_repository(rows: Iterable[sqlite3.Row]) -> dict[str, tuple[sqlite3.Row, ...]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["repository_id"])].append(row)
    return {repository_id: tuple(items) for repository_id, items in grouped.items()}


def _peer_key(row: sqlite3.Row) -> PeerKey:
    return (
        str(row["repository_id"]),
        str(row["goon_name"]),
        str(row["goon_source_sha256"]),
        str(row["step_id"]),
        str(row["environment_key_json"]),
    )


def _group_peer_rows(rows: Iterable[sqlite3.Row]) -> dict[PeerKey, tuple[sqlite3.Row, ...]]:
    grouped: dict[PeerKey, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[_peer_key(row)].append(row)
    return {key: tuple(items) for key, items in grouped.items()}


def _repository_evidence(
    run_row: sqlite3.Row,
    rows: Iterable[sqlite3.Row],
    peers_by_key: dict[PeerKey, tuple[sqlite3.Row, ...]],
) -> ScoutRunRepositoryEvidence:
    steps = tuple(_step_evidence(row, peers_by_key.get(_peer_key(row), ())) for row in rows)
    cpu_values = [item.cpu_seconds for item in steps if item.cpu_seconds is not None]
    memory_values = [item.peak_memory_bytes for item in steps if item.peak_memory_bytes is not None]
    return ScoutRunRepositoryEvidence(
        repository_id=str(run_row["repository_id"]),
        repository_name=str(run_row["repository_name"]),
        status=GameTapeStatus(str(run_row["repository_status"])),
        duration_seconds=_float_or_none(run_row["duration_seconds"]),
        cpu_seconds=sum(cpu_values) if cpu_values else None,
        peak_memory_bytes=max(memory_values) if memory_values else None,
        steps=steps,
    )


def _step_evidence(row: sqlite3.Row, peers: Sequence[sqlite3.Row]) -> ScoutRunStepEvidence:
    cpu = _float_or_none(row["complete_cpu_seconds"])
    memory = _int_or_none(row["complete_peak_memory_bytes"])
    duration = _float_or_none(row["duration_seconds"])
    peer_cpu = tuple(
        value
        for item in peers
        if (value := _float_or_none(item["complete_cpu_seconds"])) is not None
    )
    peer_memory = tuple(
        value
        for item in peers
        if (value := _int_or_none(item["complete_peak_memory_bytes"])) is not None
    )
    peer_duration = tuple(
        value for item in peers if (value := _float_or_none(item["duration_seconds"])) is not None
    )
    typical_cpu = float(median(peer_cpu)) if peer_cpu else None
    typical_memory = float(median(peer_memory)) if peer_memory else None
    typical_duration = float(median(peer_duration)) if peer_duration else None
    return ScoutRunStepEvidence(
        step_id=str(row["step_id"]),
        goon_name=str(row["goon_name"]),
        goon_source_sha256=str(row["goon_source_sha256"]),
        status=GameTapeStatus(str(row["step_status"])),
        cpu_seconds=cpu,
        peak_memory_bytes=memory,
        duration_seconds=duration,
        comparable_runs=len(peers),
        typical_cpu_seconds=typical_cpu,
        typical_peak_memory_bytes=typical_memory,
        typical_duration_seconds=typical_duration,
        cpu_difference_percent=_difference_percent(cpu, typical_cpu),
        memory_difference_percent=_difference_percent(
            float(memory) if memory is not None else None,
            typical_memory,
        ),
        duration_difference_percent=_difference_percent(duration, typical_duration),
        comparison_environment=json.loads(str(row["environment_key_json"])),
    )


def _difference_percent(current: float | None, typical: float | None) -> int | None:
    if current is None or typical is None or typical <= 0:
        return None
    return round(((current - typical) / typical) * 100)


def _float_or_none(value: str | float | int | None) -> float | None:
    return float(value) if value is not None else None


def _int_or_none(value: str | float | int | None) -> int | None:
    return int(value) if value is not None else None
