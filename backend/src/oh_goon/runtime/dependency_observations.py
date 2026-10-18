from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from oh_goon.core.models.dependency_fleet import (
    DependencyEvidenceFilter,
    DependencyEvidenceState,
    DependencyRelationshipFilter,
)
from oh_goon.core.models.fleet_observation import DependencyObservationSnapshot
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.runtime import _dependency_observation_storage as storage
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal

AGED_AFTER = timedelta(days=7)


class DependencyObservationStore:
    """Rebuildable searchable projection over canonical typed Step observation evidence."""

    def __init__(self, database: LocalDatabase, journal: Journal) -> None:
        self._database = database
        self._journal = journal

    def synchronize(self) -> int:
        with self._database.transaction() as connection:
            start = storage.watermark(connection)
        current, reports = self._journal.operation_reports_after(start)
        if current <= start:
            return current
        with self._database.transaction(write=True) as connection:
            watermark = storage.watermark(connection)
            if watermark > start:
                return watermark
            for _revision, report in reports:
                self._index_report(connection, report)
            storage.set_watermark(connection, current)
        return current

    def package_search_rows(self, query: str) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.package_search_rows(connection, query)

    def package_search(self, query: str, *, limit: int) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.package_search(connection, query, limit)

    def package_rows(
        self,
        package_name: str,
        *,
        resolved_version: str | None = None,
        version_prefix: str | None = None,
        relationship: DependencyRelationshipFilter = "all",
        evidence: DependencyEvidenceFilter = "all",
    ) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            rows = storage.package_rows(
                connection,
                package_name,
                resolved_version=resolved_version,
                version_prefix=version_prefix,
                relationship=relationship,
            )
        if evidence == "all":
            return rows
        return [row for row in rows if self.evidence_state(row) == evidence]

    def repository_rows(self, repository_id: str) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.repository_rows(connection, repository_id)

    def snapshot_rows(self) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.snapshot_rows(connection)

    def observed_repository_ids(self) -> set[str]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.observed_repository_ids(connection)

    @staticmethod
    def evidence_state(row: sqlite3.Row, *, now: datetime | None = None) -> DependencyEvidenceState:
        if row["head_state_id"] is not None and row["head_state_id"] != row["snapshot_state_id"]:
            return "stale"
        observed = datetime.fromisoformat(str(row["observed_at"]))
        if (now or datetime.now(UTC)) - observed > AGED_AFTER:
            return "aged"
        return "current"

    def _index_report(self, connection: sqlite3.Connection, report: OperationReport) -> None:
        storage.upsert_repository_head(connection, report)
        evidence = report.evidence
        target = report.target
        if not isinstance(evidence, FleetPlanOperationEvidence) or not isinstance(
            target, FleetPlanTarget
        ):
            return
        for step in evidence.plan.steps:
            for observation in step.observations:
                if isinstance(observation, DependencyObservationSnapshot):
                    storage.replace_snapshot(
                        connection,
                        report,
                        producer_goon=_producer_goon(target, step.step_id),
                        step_id=step.step_id,
                        snapshot=observation,
                    )


def _producer_goon(target: FleetPlanTarget, step_id: str) -> str | None:
    matches = [item for item in target.plan.imports if step_id in item.step_ids]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item.import_id)).name
