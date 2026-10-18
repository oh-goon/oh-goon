from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from oh_goon.core.effect_attempts import OPERATOR_REQUIRED_EFFECT_STATUSES
from oh_goon.core.errors import OperationNotFound
from oh_goon.core.models.effect_attempt import (
    EffectStartedRepositoryEffectAttempt,
    RepositoryEffectAttempt,
)
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.vocabulary import EffectAttemptStatus, EffectBoundaryKind
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.journal import Journal


class RepositoryEffectInterruptionDisposition(StrEnum):
    """Durable result of settling one interrupted repository effect."""

    RESOLVED = "RESOLVED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    OPERATOR_REQUIRED = "OPERATOR_REQUIRED"


@dataclass(frozen=True, slots=True)
class RepositoryEffectInterruptionEvidence:
    """Exact post-transaction evidence for one interruption settlement body."""

    operation_id: str
    disposition: RepositoryEffectInterruptionDisposition
    attempt: RepositoryEffectAttempt | None
    operation: OperationReport | None


class RepositoryEffectSettlement:
    """Own cross-store settlement for one repository-effect lifecycle.

    RepositoryEffectAttempt is the mutable coordination truth while work is unresolved;
    Journal is the immutable canonical history.  Any transition that must change both
    owners is committed here under the caller's transaction.  Execution and recovery
    therefore cannot invent competing commit/interruption/reconciliation policies.
    """

    def __init__(
        self,
        database: LocalDatabase,
        journal: Journal,
        attempts: RepositoryEffectAttemptStore,
    ) -> None:
        self._database = database
        self._journal = journal
        self._attempts = attempts

    def commit_success_in(
        self,
        connection: sqlite3.Connection,
        report: OperationReport,
        operation_id: str,
    ) -> None:
        self._journal.commit_operation_in(connection, report)
        self._attempts.retire_committed_in(connection, operation_id)

    def settle_interruption_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        unknown_note: str,
    ) -> RepositoryEffectInterruptionDisposition:
        return self.settle_interruption_evidence_in(
            connection, operation_id, unknown_note=unknown_note
        ).disposition

    def settle_interruption_evidence_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        unknown_note: str,
    ) -> RepositoryEffectInterruptionEvidence:
        disposition = self._settle_interruption_in(
            connection, operation_id, unknown_note=unknown_note
        )
        return self._interruption_evidence_in(connection, operation_id, disposition)

    def interruption_evidence_matches(self, expected: RepositoryEffectInterruptionEvidence) -> bool:
        """Verify one lost settlement response from one fresh SQLite read cut."""
        with self._database.transaction() as connection:
            observed = self._interruption_evidence_in(
                connection, expected.operation_id, expected.disposition
            )
        return observed == expected

    def _settle_interruption_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        unknown_note: str,
    ) -> RepositoryEffectInterruptionDisposition:
        view = self._journal.read_view_from_connection(connection)
        committed = view.operation(operation_id)
        if committed is not None:
            return self._retire_committed_coordination(connection, operation_id)

        current = self._attempts.get_in(connection, operation_id)
        if current.status is EffectAttemptStatus.CLAIMED:
            self._attempts.retire_unstarted_in(connection, operation_id)
            return RepositoryEffectInterruptionDisposition.RESOLVED
        if current.status is EffectAttemptStatus.EFFECT_STARTED:
            assert isinstance(current, EffectStartedRepositoryEffectAttempt)
            return self._settle_started_interruption(
                connection,
                current,
                unknown_note=unknown_note,
            )
        if current.status is EffectAttemptStatus.OUTCOME_UNKNOWN:
            return RepositoryEffectInterruptionDisposition.OUTCOME_UNKNOWN
        if current.status in OPERATOR_REQUIRED_EFFECT_STATUSES:
            return RepositoryEffectInterruptionDisposition.OPERATOR_REQUIRED
        raise AssertionError(f"Unhandled repository effect status: {current.status.value}")

    def _interruption_evidence_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        disposition: RepositoryEffectInterruptionDisposition,
    ) -> RepositoryEffectInterruptionEvidence:
        view = self._journal.read_view_from_connection(connection)
        try:
            attempt = self._attempts.get_in(connection, operation_id)
        except OperationNotFound:
            attempt = None
        return RepositoryEffectInterruptionEvidence(
            operation_id=operation_id,
            disposition=disposition,
            attempt=attempt,
            operation=view.operation(operation_id),
        )

    def apply_reconciliation_in(
        self,
        connection: sqlite3.Connection,
        current: RepositoryEffectAttempt,
        evidence: RepositoryEffectReconciliationEvidence,
    ) -> None:
        self._attempts.apply_reconciliation_in(
            connection,
            current.operation_id,
            evidence.reconciliation,
            expected_revision=current.record_version,
        )
        self._journal.commit_effect_reconciliation_in(connection, evidence)

    def acknowledge_in(
        self,
        connection: sqlite3.Connection,
        current: RepositoryEffectAttempt,
        evidence: RepositoryEffectAcknowledgementEvidence,
    ) -> None:
        self._attempts.retire_acknowledged_in(
            connection,
            current.operation_id,
            expected_revision=current.record_version,
        )
        self._journal.commit_effect_acknowledgement_in(connection, evidence)

    def _retire_committed_coordination(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> RepositoryEffectInterruptionDisposition:
        try:
            current = self._attempts.get_in(connection, operation_id)
        except OperationNotFound:
            return RepositoryEffectInterruptionDisposition.RESOLVED
        self._attempts.retire_canonical_committed_in(
            connection,
            operation_id,
            expected_revision=current.record_version,
        )
        return RepositoryEffectInterruptionDisposition.RESOLVED

    def _settle_started_interruption(
        self,
        connection: sqlite3.Connection,
        current: EffectStartedRepositoryEffectAttempt,
        *,
        unknown_note: str,
    ) -> RepositoryEffectInterruptionDisposition:
        if current.effect_boundary is EffectBoundaryKind.CONTAINED:
            self._attempts.retire_contained_started_in(connection, current.operation_id)
            return RepositoryEffectInterruptionDisposition.RESOLVED
        self._attempts.mark_unknown_in(
            connection,
            current.operation_id,
            note=unknown_note,
        )
        return RepositoryEffectInterruptionDisposition.OUTCOME_UNKNOWN
