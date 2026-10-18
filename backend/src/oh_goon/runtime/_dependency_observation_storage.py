from __future__ import annotations

import json
import sqlite3

from oh_goon.core.models.dependency_fleet import DependencyRelationshipFilter
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


def package_search_rows(connection: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    pattern = f"%{query}%"
    return list(
        connection.execute(
            """
            SELECT package_name, repository_id, resolved_version
            FROM dependency_observations
            WHERE ecosystem='pypi' AND package_name LIKE ? ESCAPE '\\'
            ORDER BY package_name, repository_id
            """,
            (pattern,),
        ).fetchall()
    )


def package_search(connection: sqlite3.Connection, query: str, limit: int) -> list[sqlite3.Row]:
    pattern = f"%{query}%"
    return list(
        connection.execute(
            """
            SELECT package_name, COUNT(DISTINCT repository_id) AS repositories,
                   COUNT(DISTINCT resolved_version) AS resolved_versions
            FROM dependency_observations
            WHERE ecosystem='pypi' AND package_name LIKE ? ESCAPE '\\'
            GROUP BY package_name
            ORDER BY CASE WHEN package_name=? THEN 0 ELSE 1 END,
                     repositories DESC, package_name
            LIMIT ?
            """,
            (pattern, query, limit),
        ).fetchall()
    )


def package_rows(
    connection: sqlite3.Connection,
    package_name: str,
    *,
    resolved_version: str | None,
    version_prefix: str | None,
    relationship: DependencyRelationshipFilter,
) -> list[sqlite3.Row]:
    where, params = _package_where(
        package_name,
        resolved_version=resolved_version,
        version_prefix=version_prefix,
        relationship=relationship,
    )
    return list(
        connection.execute(
            f"""
            SELECT d.*, s.repository_state_id AS snapshot_state_id,
                   h.repository_state_id AS head_state_id,
                   h.repository_name, h.repository_path
            FROM dependency_observations d
            JOIN dependency_observation_snapshots s
              ON s.repository_id=d.repository_id AND s.ecosystem=d.ecosystem
            LEFT JOIN repository_observation_heads h ON h.repository_id=d.repository_id
            WHERE {where}
            ORDER BY lower(COALESCE(h.repository_name, d.repository_id)),
                     d.repository_id, d.resolved_version, d.declared_range
            """,
            params,
        ).fetchall()
    )


def repository_rows(connection: sqlite3.Connection, repository_id: str) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT d.*, s.repository_state_id AS snapshot_state_id,
                   h.repository_state_id AS head_state_id,
                   h.repository_name, h.repository_path
            FROM dependency_observations d
            JOIN dependency_observation_snapshots s
              ON s.repository_id=d.repository_id AND s.ecosystem=d.ecosystem
            LEFT JOIN repository_observation_heads h ON h.repository_id=d.repository_id
            WHERE d.repository_id=?
            ORDER BY d.package_name, d.resolved_version, d.declared_range
            """,
            (repository_id,),
        ).fetchall()
    )


def snapshot_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute(
            """
            SELECT s.*, s.repository_state_id AS snapshot_state_id,
                   h.repository_state_id AS head_state_id,
                   h.repository_name, h.repository_path
            FROM dependency_observation_snapshots s
            LEFT JOIN repository_observation_heads h ON h.repository_id=s.repository_id
            WHERE s.ecosystem='pypi'
            ORDER BY s.repository_id
            """
        ).fetchall()
    )


def observed_repository_ids(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT repository_id FROM dependency_observation_snapshots WHERE ecosystem='pypi'"
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
            repository_id, ecosystem, package_name, relationship,
            declared_range, resolved_version, declared_source, resolved_source,
            fleet_run_id, operation_id, producer_goon, step_id, observed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                repository_id,
                item.ecosystem,
                item.package_name,
                item.relationship,
                item.declared_range,
                item.resolved_version,
                item.declared_source,
                item.resolved_source,
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
    state = report.provenance.after or report.provenance.before
    fleet_run_id = report.provenance.fleet_run_id
    if state is None or fleet_run_id is None:
        return
    connection.execute(
        """
        INSERT INTO dependency_observation_snapshots(
            repository_id, ecosystem, repository_state_id, head_sha,
            pyproject_sha256, uv_lock_sha256, sources_json, fleet_run_id,
            operation_id, producer_goon, step_id, observed_at, dependency_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository_id, ecosystem) DO UPDATE SET
            repository_state_id=excluded.repository_state_id,
            head_sha=excluded.head_sha,
            pyproject_sha256=excluded.pyproject_sha256,
            uv_lock_sha256=excluded.uv_lock_sha256,
            sources_json=excluded.sources_json,
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
            state.state_id,
            state.head_sha,
            state.pyproject_sha256,
            state.uv_lock_sha256,
            json.dumps(
                [
                    item.model_dump(mode="json", exclude_computed_fields=True)
                    for item in snapshot.sources
                ],
                sort_keys=True,
            ),
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
    resolved_version: str | None,
    version_prefix: str | None,
    relationship: DependencyRelationshipFilter,
) -> tuple[str, tuple[object, ...]]:
    clauses = ["d.ecosystem='pypi'", "d.package_name=?"]
    params: list[object] = [package_name]
    if resolved_version is not None:
        clauses.append("d.resolved_version=?")
        params.append(resolved_version)
    if version_prefix is not None:
        clauses.append("d.resolved_version LIKE ?")
        params.append(f"{version_prefix}%")
    if relationship != "all":
        clauses.append("d.relationship=?")
        params.append(relationship)
    return " AND ".join(clauses), tuple(params)
