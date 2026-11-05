from __future__ import annotations

import json
import sqlite3

from oh_goon.core.dependency_identity import DependencyEcosystem
from oh_goon.core.models.fleet_observation import DependencyObservationSnapshot
from oh_goon.core.models.operation import OperationReport


def watermark(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT journal_revision FROM observation_index_metadata WHERE singleton=1"
    ).fetchone()
    return int(row[0]) if row is not None else 0


def set_watermark(connection: sqlite3.Connection, revision: int) -> None:
    connection.execute(
        "UPDATE observation_index_metadata SET journal_revision=? WHERE singleton=1",
        (revision,),
    )


def package_search_rows(
    connection: sqlite3.Connection, query: str, ecosystem: DependencyEcosystem | None = None
) -> list[sqlite3.Row]:
    pattern = f"%{_like_text(query)}%"
    return list(
        connection.execute(
            """
            SELECT ecosystem, package_name, repository_id, resolved_version
            FROM dependency_observations
            WHERE (? IS NULL OR ecosystem=?) AND package_name LIKE ? ESCAPE '\\'
            ORDER BY ecosystem, package_name, repository_id
            """,
            (ecosystem, ecosystem, pattern),
        ).fetchall()
    )


def package_search(
    connection: sqlite3.Connection,
    query: str,
    limit: int,
    ecosystem: DependencyEcosystem | None = None,
) -> list[sqlite3.Row]:
    pattern = f"%{_like_text(query)}%"
    return list(
        connection.execute(
            """
            SELECT ecosystem, package_name, COUNT(DISTINCT repository_id) AS repositories,
                   COUNT(DISTINCT resolved_version) AS resolved_versions
            FROM dependency_observations
            WHERE (? IS NULL OR ecosystem=?) AND package_name LIKE ? ESCAPE '\\'
            GROUP BY ecosystem, package_name
            ORDER BY CASE WHEN package_name=? THEN 0 ELSE 1 END,
                     repositories DESC, package_name
            LIMIT ?
            """,
            (ecosystem, ecosystem, pattern, query, limit),
        ).fetchall()
    )


def _like_text(value: str) -> str:
    """Keep native SQLite wildcards separate from literal caller query text."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def package_rows(
    connection: sqlite3.Connection,
    package_name: str,
    *,
    ecosystem: DependencyEcosystem = "pypi",
    resolved_version: str | None,
    version_prefix: str | None,
) -> list[sqlite3.Row]:
    where, params = _package_where(
        package_name,
        ecosystem=ecosystem,
        resolved_version=resolved_version,
        version_prefix=version_prefix,
    )
    return list(
        connection.execute(
            f"""
            SELECT d.*, h.repository_name, h.repository_path
            FROM dependency_observations d
            JOIN dependency_observation_snapshots s
              ON s.repository_id=d.repository_id AND s.ecosystem=d.ecosystem
            LEFT JOIN repository_observation_heads h ON h.repository_id=d.repository_id
            WHERE {where}
            ORDER BY lower(COALESCE(h.repository_name, d.repository_id)),
                     d.repository_id, d.resolved_version
            """,
            params,
        ).fetchall()
    )


def repository_rows(connection: sqlite3.Connection, repository_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT d.*, h.repository_name, h.repository_path
            FROM dependency_observations d
            JOIN dependency_observation_snapshots s
              ON s.repository_id=d.repository_id AND s.ecosystem=d.ecosystem
            LEFT JOIN repository_observation_heads h ON h.repository_id=d.repository_id
            WHERE d.repository_id=?
            ORDER BY d.package_name, d.resolved_version
            """,
            (repository_id,),
        ).fetchall()
    )


def snapshot_rows(
    connection: sqlite3.Connection, ecosystem: DependencyEcosystem | None = None
) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT s.*, h.repository_name, h.repository_path
            FROM dependency_observation_snapshots s
            LEFT JOIN repository_observation_heads h ON h.repository_id=s.repository_id
            WHERE (? IS NULL OR s.ecosystem=?)
            ORDER BY s.repository_id, s.ecosystem
            """,
            (ecosystem, ecosystem),
        ).fetchall()
    )


def observed_repository_ids(
    connection: sqlite3.Connection, ecosystem: DependencyEcosystem | None = None
) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT repository_id FROM dependency_observation_snapshots WHERE (? IS NULL OR ecosystem=?)",
            (ecosystem, ecosystem),
        ).fetchall()
    }


def upsert_repository_head(connection: sqlite3.Connection, report: OperationReport) -> None:
    state = report.provenance.after or report.provenance.before
    if state is None:
        return
    connection.execute(
        """
        INSERT INTO repository_observation_heads(
            repository_id, repository_name, repository_path, repository_state_id,
            head_sha, pyproject_sha256, uv_lock_sha256, fleet_run_id,
            operation_id, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository_id) DO UPDATE SET
            repository_name=excluded.repository_name,
            repository_path=excluded.repository_path,
            repository_state_id=excluded.repository_state_id,
            head_sha=excluded.head_sha,
            pyproject_sha256=excluded.pyproject_sha256,
            uv_lock_sha256=excluded.uv_lock_sha256,
            fleet_run_id=excluded.fleet_run_id,
            operation_id=excluded.operation_id,
            observed_at=excluded.observed_at
        """,
        (
            report.repository.repository_id,
            report.repository.name,
            str(report.repository.path),
            state.state_id,
            state.head_sha,
            state.pyproject_sha256,
            state.uv_lock_sha256,
            report.provenance.fleet_run_id,
            report.provenance.operation_id,
            report.provenance.finished_at.isoformat(),
        ),
    )


def replace_snapshot(
    connection: sqlite3.Connection,
    report: OperationReport,
    *,
    producer_goon: str | None,
    step_id: str,
    snapshot: DependencyObservationSnapshot,
) -> None:
    state = report.provenance.after or report.provenance.before
    fleet_run_id = report.provenance.fleet_run_id
    if state is None or fleet_run_id is None:
        return
    repository_id = report.repository.repository_id
    observed_at = report.provenance.finished_at.isoformat()
    connection.execute(
        "DELETE FROM dependency_observations WHERE repository_id=? AND ecosystem=?",
        (repository_id, snapshot.ecosystem),
    )
    _upsert_snapshot_row(
        connection,
        report,
        producer_goon=producer_goon,
        step_id=step_id,
        snapshot=snapshot,
    )
    connection.executemany(
        """
        INSERT INTO dependency_observations(
            repository_id, ecosystem, package_name, component_id, node_id,
            resolved_version, resolved_source, package_source, contexts_json,
            fleet_run_id, operation_id, producer_goon, step_id, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                repository_id,
                item.ecosystem,
                item.package_name,
                item.component_id,
                item.node_id,
                item.resolved_version,
                item.resolved_source,
                item.package_source,
                json.dumps(item.contexts),
                fleet_run_id,
                report.provenance.operation_id,
                producer_goon,
                step_id,
                observed_at,
            )
            for item in snapshot.dependencies
        ),
    )


def _upsert_snapshot_row(
    connection: sqlite3.Connection,
    report: OperationReport,
    *,
    producer_goon: str | None,
    step_id: str,
    snapshot: DependencyObservationSnapshot,
) -> None:
    fleet_run_id = report.provenance.fleet_run_id
    if fleet_run_id is None:
        return
    connection.execute(
        """
        INSERT INTO dependency_observation_snapshots(
            repository_id, ecosystem, snapshot_json, fleet_run_id,
            operation_id, producer_goon, step_id, observed_at, dependency_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository_id, ecosystem) DO UPDATE SET
            snapshot_json=excluded.snapshot_json,
            fleet_run_id=excluded.fleet_run_id,
            operation_id=excluded.operation_id,
            producer_goon=excluded.producer_goon,
            step_id=excluded.step_id,
            observed_at=excluded.observed_at,
            dependency_count=excluded.dependency_count
        """,
        (
            report.repository.repository_id,
            snapshot.ecosystem,
            snapshot.model_dump_json(),
            fleet_run_id,
            report.provenance.operation_id,
            producer_goon,
            step_id,
            report.provenance.finished_at.isoformat(),
            len(snapshot.dependencies),
        ),
    )


def _package_where(
    package_name: str,
    *,
    ecosystem: DependencyEcosystem,
    resolved_version: str | None,
    version_prefix: str | None,
) -> tuple[str, tuple[object, ...]]:
    clauses = ["d.ecosystem=?", "d.package_name=?"]
    params: list[object] = [ecosystem, package_name]
    if resolved_version is not None:
        clauses.append("d.resolved_version=?")
        params.append(resolved_version)
    if version_prefix is not None:
        clauses.append("d.resolved_version LIKE ? ESCAPE '\\'")
        params.append(f"{_like_text(version_prefix)}%")
    return " AND ".join(clauses), tuple(params)
