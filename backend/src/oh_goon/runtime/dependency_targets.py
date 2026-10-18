from __future__ import annotations

import json

from oh_goon.core.errors import DependencyTargetSelectionNotFound
from oh_goon.core.models.dependency_fleet import DependencyTargetMaterialization
from oh_goon.runtime.database import LocalDatabase


class DependencyTargetMaterializationStore:
    """Durable immutable reviewed repository populations derived from observations."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def save(self, selection: DependencyTargetMaterialization) -> DependencyTargetMaterialization:
        with self._database.transaction(write=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO dependency_target_materializations(
                    query_id, selected_at, filter_json, repository_ids_json, repository_instance_ids_json, total
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    selection.query_id,
                    selection.selected_at.isoformat(),
                    selection.filter.model_dump_json(exclude_none=True),
                    json.dumps(selection.repo_ids),
                    json.dumps(selection.repository_instance_ids, sort_keys=True),
                    selection.total,
                ),
            )
            row = connection.execute(
                "SELECT selected_at, filter_json, repository_ids_json, repository_instance_ids_json, total "
                "FROM dependency_target_materializations WHERE query_id=?",
                (selection.query_id,),
            ).fetchone()
        assert row is not None
        stored = DependencyTargetMaterialization.model_validate(
            {
                "query_id": selection.query_id,
                "selected_at": row[0],
                "filter": json.loads(str(row[1])),
                "repo_ids": json.loads(str(row[2])),
                "repository_instance_ids": json.loads(str(row[3])),
                "total": row[4],
            }
        )
        if stored != selection:
            raise RuntimeError("Dependency target selection identity collision")
        return stored

    def get(self, query_id: str) -> DependencyTargetMaterialization:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT selected_at, filter_json, repository_ids_json, repository_instance_ids_json, total "
                "FROM dependency_target_materializations WHERE query_id=?",
                (query_id,),
            ).fetchone()
        if row is None:
            raise DependencyTargetSelectionNotFound("Dependency target selection does not exist")
        return DependencyTargetMaterialization.model_validate(
            {
                "query_id": query_id,
                "selected_at": row[0],
                "filter": json.loads(str(row[1])),
                "repo_ids": json.loads(str(row[2])),
                "repository_instance_ids": json.loads(str(row[3])),
                "total": row[4],
            }
        )
