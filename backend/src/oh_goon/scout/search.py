from __future__ import annotations

from datetime import datetime

from oh_goon.scout.models import ScoutSearchResult
from oh_goon.runtime.database import LocalDatabase
from oh_goon.scout.database import ScoutAnalyticalDatabase


class ScoutSearchReader:
    """Bounded read-only identity search over already-materialized Scout observations."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = ScoutAnalyticalDatabase(database.path)

    def search(
        self, *, query: str, start_at: datetime, end_at: datetime, limit: int, offset: int = 0
    ) -> tuple[int, tuple[ScoutSearchResult, ...]]:
        prefix = _like_prefix(query)
        params = _search_params(start_at, end_at, prefix)
        cte = _SEARCH_CTE
        with self._database.transaction(track_revision=False) as connection:
            total = int(
                connection.execute(
                    cte + "SELECT COUNT(*) AS count FROM matches", params
                ).fetchone()["count"]
            )
            rows = connection.execute(
                cte + _SEARCH_RESULT_SQL,
                (*params, query, query, limit, offset),
            ).fetchall()
        return total, tuple(_result(row) for row in rows)


def _search_params(start_at: datetime, end_at: datetime, prefix: str) -> tuple[object, ...]:
    start = start_at.isoformat()
    end = end_at.isoformat()
    return (
        start,
        end,
        start,
        end,
        prefix,
        prefix,
        start,
        end,
        start,
        end,
        prefix,
        prefix,
        start,
        end,
        prefix,
    )


def _result(row) -> ScoutSearchResult:
    return ScoutSearchResult(
        entity_kind=row["entity_kind"],
        key=row["key"],
        display_name=row["display_name"],
        repository_id=row["repository_id"],
        plan_id=row["plan_id"],
        goon_name=row["goon_name"],
    )


def _like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


_SEARCH_CTE = """
    WITH matches AS (
        SELECT DISTINCT
            'REPOSITORY' AS entity_kind,
            r.repository_id AS key,
            (SELECT latest.repository_name FROM scout_run_observations AS latest
             WHERE latest.repository_id = r.repository_id AND latest.started_at >= ? AND latest.started_at < ?
             ORDER BY latest.started_at DESC, latest.run_id DESC LIMIT 1) AS display_name,
            r.repository_id, NULL AS plan_id, NULL AS goon_name
        FROM scout_run_observations AS r
        WHERE r.started_at >= ? AND r.started_at < ?
          AND (r.repository_name LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR r.repository_id LIKE ? ESCAPE '\\' COLLATE NOCASE)
        UNION
        SELECT DISTINCT
            'PLAN' AS entity_kind,
            r.plan_id AS key,
            (SELECT latest.plan_name FROM scout_run_observations AS latest
             WHERE latest.plan_id = r.plan_id AND latest.started_at >= ? AND latest.started_at < ?
             ORDER BY latest.started_at DESC, latest.run_id DESC LIMIT 1) AS display_name,
            NULL AS repository_id, r.plan_id, NULL AS goon_name
        FROM scout_run_observations AS r
        WHERE r.started_at >= ? AND r.started_at < ?
          AND r.plan_id IS NOT NULL AND r.plan_name IS NOT NULL
          AND (r.plan_name LIKE ? ESCAPE '\\' COLLATE NOCASE
               OR r.plan_id LIKE ? ESCAPE '\\' COLLATE NOCASE)
        UNION
        SELECT DISTINCT
            'GOON' AS entity_kind, goon_name AS key, goon_name AS display_name,
            NULL AS repository_id, NULL AS plan_id, goon_name
        FROM scout_step_observations
        WHERE started_at >= ? AND started_at < ?
          AND goon_name LIKE ? ESCAPE '\\' COLLATE NOCASE
    )
"""

_SEARCH_RESULT_SQL = """
    SELECT entity_kind, key, display_name, repository_id, plan_id, goon_name
    FROM matches
    ORDER BY
        CASE WHEN key = ? COLLATE NOCASE OR display_name = ? COLLATE NOCASE THEN 0 ELSE 1 END,
        entity_kind, display_name COLLATE NOCASE, key
    LIMIT ? OFFSET ?
"""
