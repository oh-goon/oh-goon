from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.core.models.dependency_fleet import (
    DependencyEvidenceFilter,
    DependencyEvidenceState,
)
from oh_goon.core.models.fleet_observation import (
    DependencyEvidenceAssessment,
    DependencyObservationSnapshot,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.integrations.hashmarks.dependencies import (
    LockedInventoryAssessment,
    assess_locked_dependencies,
)
from oh_goon.runtime import _dependency_observation_storage as storage
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal


@dataclass
class _DependencyRead:
    snapshots: dict[str, sqlite3.Row]
    assessments: dict[str, LockedInventoryAssessment] = field(default_factory=dict)


class DependencyObservationStore:
    """Rebuildable searchable projection over canonical typed Step observation evidence."""

    def __init__(self, database: LocalDatabase, journal: Journal) -> None:
        self._database = database
        self._journal = journal
        self._read: ContextVar[_DependencyRead | None] = ContextVar("dependency_read", default=None)

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
        evidence: DependencyEvidenceFilter = "all",
    ) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            rows = storage.package_rows(
                connection,
                package_name,
                resolved_version=resolved_version,
                version_prefix=version_prefix,
            )
        if evidence == "all":
            return rows
        return [row for row in rows if self.evidence_state(row) == evidence]

    def repository_rows(self, repository_id: str) -> list[sqlite3.Row]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.repository_rows(connection, repository_id)

    def snapshot_rows(self) -> list[sqlite3.Row]:
        read = self._read.get()
        if read is not None:
            return list(read.snapshots.values())
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.snapshot_rows(connection)

    def observed_repository_ids(self) -> set[str]:
        self.synchronize()
        with self._database.transaction() as connection:
            return storage.observed_repository_ids(connection)

    @contextmanager
    def assessment_scope(self) -> Iterator[None]:
        """Resolve each retained snapshot/source assessment once for this request."""
        if self._read.get() is not None:
            yield
            return
        snapshots = {str(row["repository_id"]): row for row in self.snapshot_rows()}
        token = self._read.set(_DependencyRead(snapshots))
        try:
            yield
        finally:
            self._read.reset(token)

    def assessment(self, row: sqlite3.Row) -> DependencyEvidenceAssessment:
        return self._assess_inventory(row).evidence

    def absent_from_lock(self, row: sqlite3.Row, package: str) -> bool:
        admitted = self._assess_inventory(row)
        return (
            admitted.evidence.absence_admissible
            and admitted.packages is not None
            and package not in admitted.packages
        )

    def _assess_inventory(self, row: sqlite3.Row) -> LockedInventoryAssessment:
        repository_id = str(row["repository_id"])
        read = self._read.get()
        if read is None:
            with self.assessment_scope():
                return self._assess_inventory(row)
        if repository_id not in read.assessments:
            snapshot_row = read.snapshots.get(repository_id)
            if snapshot_row is None:
                result = LockedInventoryAssessment(
                    DependencyEvidenceAssessment(
                        availability="missing", reason="inventory-not-observed"
                    )
                )
            else:
                snapshot = DependencyObservationSnapshot.model_validate_json(
                    str(snapshot_row["snapshot_json"])
                )
                repository_path = snapshot_row["repository_path"]
                result = (
                    assess_locked_dependencies(Path(str(repository_path)), snapshot)
                    if repository_path is not None
                    else LockedInventoryAssessment(
                        DependencyEvidenceAssessment(reason="repository-path-unavailable")
                    )
                )
            read.assessments[repository_id] = result
        return read.assessments[repository_id]

    def evidence_state(self, row: sqlite3.Row) -> DependencyEvidenceState:
        return self.assessment(row).freshness

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
