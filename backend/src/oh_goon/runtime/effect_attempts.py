from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from oh_goon.core.effect_attempt_transitions import (
    apply_effect_reconciliation,
    begin_effect_attempt,
    bind_effect_execution_input,
    mark_effect_unknown,
    start_effect_attempt,
)
from oh_goon.core.effect_attempts import (
    AUTOMATIC_RECOVERY_EFFECT_STATUSES,
    OPERATOR_REQUIRED_EFFECT_STATUSES,
    RECONCILABLE_EFFECT_STATUSES,
)
from oh_goon.core.errors import StateConflict
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_attempt import RepositoryEffectAttempt
from oh_goon.core.models.effect_origin import (
    DirectEffectOrigin,
    FleetEffectOrigin,
    RepositoryEffectOrigin,
)
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.reconciliation import RepositoryReconciliation
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.product_identity import repository_id, validate_repository_attempt_operation_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import EffectAttemptOwner, EffectAttemptStatus, EffectBoundaryKind
from oh_goon.runtime import _effect_attempt_storage as storage
from oh_goon.runtime.database import LocalDatabase

EffectAttemptTransition = Callable[..., RepositoryEffectAttempt]


class RepositoryEffectAttemptStore:
    """The one durable repository-effect lifecycle for Direct and Fleet execution."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def get(self, operation_id: str) -> RepositoryEffectAttempt:
        with self._database.transaction() as connection:
            return storage.current(connection, operation_id)

    def get_in(self, connection: sqlite3.Connection, operation_id: str) -> RepositoryEffectAttempt:
        return storage.current(connection, operation_id)

    def recovery_candidates(self) -> list[RepositoryEffectAttempt]:
        with self._database.transaction() as connection:
            return storage.by_statuses_in(
                connection,
                AUTOMATIC_RECOVERY_EFFECT_STATUSES,
            )

    def by_fleet_run_in(
        self, connection: sqlite3.Connection, fleet_run_id: str
    ) -> list[RepositoryEffectAttempt]:
        return storage.by_fleet_run_in(connection, fleet_run_id)

    def all(self) -> list[RepositoryEffectAttempt]:
        with self._database.transaction() as connection:
            return storage.all_in(connection)

    def all_in(self, connection: sqlite3.Connection) -> list[RepositoryEffectAttempt]:
        return storage.all_in(connection)

    def begin_direct_in(
        self,
        connection: sqlite3.Connection,
        repository: Path,
        capability: CapabilitySnapshot,
        *,
        target: OperationTarget | None,
        baseline: RepositoryStateFingerprint,
        effect_boundary: EffectBoundaryKind,
        operation_id: str | None = None,
    ) -> RepositoryEffectAttempt:
        return _begin_attempt_in(
            connection,
            repository,
            capability,
            origin=DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT),
            target=target,
            baseline=baseline,
            effect_boundary=effect_boundary,
            operation_id=operation_id,
        )

    def begin_fleet_in(
        self,
        connection: sqlite3.Connection,
        repository: Path,
        capability: CapabilitySnapshot,
        *,
        fleet_run_id: str,
        operation_id: str,
        target: OperationTarget | None,
        baseline: RepositoryStateFingerprint,
        effect_boundary: EffectBoundaryKind,
    ) -> RepositoryEffectAttempt:
        try:
            validate_repository_attempt_operation_id(
                fleet_run_id, repository_id(repository), operation_id
            )
        except ValueError as exc:
            raise StateConflict(str(exc)) from exc
        return _begin_attempt_in(
            connection,
            repository,
            capability,
            origin=FleetEffectOrigin(kind=EffectAttemptOwner.FLEET, fleet_run_id=fleet_run_id),
            target=target,
            baseline=baseline,
            effect_boundary=effect_boundary,
            operation_id=operation_id,
        )

    def retire_committed_in(self, connection: sqlite3.Connection, operation_id: str) -> None:
        """Retire an EFFECT_STARTED row in the same transaction as canonical commit."""
        current = storage.current(connection, operation_id)
        if current.status is not EffectAttemptStatus.EFFECT_STARTED:
            raise StateConflict(
                f"Canonical commit requires EFFECT_STARTED attempt: {operation_id} ({current.status.value})"
            )
        storage.delete_current(connection, current)

    def bind_execution_input_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        execution_input_id: str,
    ) -> RepositoryEffectAttempt:
        return _transition_in(
            connection,
            operation_id,
            lambda current, *, transitioned_at: bind_effect_execution_input(
                current,
                execution_input_id=execution_input_id,
                transitioned_at=transitioned_at,
            ),
        )

    def start_effect_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> RepositoryEffectAttempt:
        return _transition_in(connection, operation_id, start_effect_attempt)

    def mark_unknown_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        note: str,
    ) -> RepositoryEffectAttempt:
        return _transition_in(
            connection,
            operation_id,
            lambda current, *, transitioned_at: mark_effect_unknown(
                current,
                note=note,
                transitioned_at=transitioned_at,
            ),
        )

    def retire_contained_started_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> None:
        current = storage.current(connection, operation_id)
        if (
            current.status is not EffectAttemptStatus.EFFECT_STARTED
            or current.effect_boundary is not EffectBoundaryKind.CONTAINED
        ):
            raise StateConflict(
                f"Only a contained EFFECT_STARTED attempt can retire without reconciliation: {operation_id}"
            )
        storage.delete_current(connection, current)

    def retire_unstarted_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> None:
        current = storage.current(connection, operation_id)
        if current.status is not EffectAttemptStatus.CLAIMED:
            raise StateConflict(
                f"Only a CLAIMED effect can be retired before start: {operation_id}"
            )
        storage.delete_current(connection, current)

    def apply_reconciliation_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        result: RepositoryReconciliation,
        *,
        expected_revision: int,
    ) -> RepositoryEffectAttempt | None:
        current = storage.current(connection, operation_id)
        if current.status not in RECONCILABLE_EFFECT_STATUSES:
            raise StateConflict(f"Effect attempt is not reconcilable: {operation_id}")
        if current.record_version != expected_revision:
            raise StateConflict(f"Effect attempt reconciliation is stale: {operation_id}")
        next_attempt = apply_effect_reconciliation(
            current,
            result,
            transitioned_at=utc_now(),
        )
        if next_attempt is None:
            storage.delete_current(connection, current)
            return None
        return storage.compare_and_set(connection, current, next_attempt)

    def retire_canonical_committed_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        expected_revision: int,
    ) -> None:
        """Retire coordination when immutable canonical Journal evidence already won."""
        current = storage.current(connection, operation_id)
        if current.record_version != expected_revision:
            raise StateConflict(f"Effect recovery is stale: {operation_id}")
        storage.delete_current(connection, current)

    def retire_acknowledged_in(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        *,
        expected_revision: int,
    ) -> None:
        current = storage.current(connection, operation_id)
        if current.status not in OPERATOR_REQUIRED_EFFECT_STATUSES:
            raise StateConflict(
                f"Effect attempt is not awaiting operator acknowledgement: {operation_id}"
            )
        if current.record_version != expected_revision:
            raise StateConflict(f"Effect acknowledgement is stale: {operation_id}")
        storage.delete_current(connection, current)


def _begin_attempt_in(
    connection: sqlite3.Connection,
    repository: Path,
    capability: CapabilitySnapshot,
    *,
    origin: RepositoryEffectOrigin,
    target: OperationTarget | None,
    baseline: RepositoryStateFingerprint,
    effect_boundary: EffectBoundaryKind,
    operation_id: str | None,
) -> RepositoryEffectAttempt:
    attempt = begin_effect_attempt(
        repository,
        capability,
        origin=origin,
        target=target,
        baseline=baseline,
        effect_boundary=effect_boundary,
        started_at=utc_now(),
        operation_id=operation_id,
    )
    storage.insert(connection, attempt)
    return attempt


def _transition_in(
    connection: sqlite3.Connection,
    operation_id: str,
    transition: EffectAttemptTransition,
) -> RepositoryEffectAttempt:
    current = storage.current(connection, operation_id)
    next_attempt = transition(current, transitioned_at=utc_now())
    if next_attempt is current:
        return current
    return storage.compare_and_set(connection, current, next_attempt)
