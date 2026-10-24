from __future__ import annotations

import sqlite3
from pathlib import Path

from oh_goon.core.capability_model import ResolvedCapabilityIntent
from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import CapabilityUnavailable, OperationNotFound, StateConflict
from oh_goon.core.models.effect_attempt import (
    ClaimedRepositoryEffectAttempt,
    RepositoryEffectAttempt,
)
from oh_goon.core.models.operation import OperationExecution, OperationReport
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.product_identity import new_operation_id, repository_id
from oh_goon.core.vocabulary import OperationAction
from oh_goon.engine.git import GitService
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution
from oh_goon.services.runtime_safety import RuntimeSafetyService


class _CommittedDirectReplay(Exception):
    def __init__(self, report: OperationReport) -> None:
        super().__init__(report.provenance.operation_id)
        self.report = report


class DirectEffectAdmission:
    """Own Direct command identity, safety admission, and the exact execution lease.

    Physical repository-effect transitions are deliberately not implemented here;
    once the attempt is CLAIMED this owner delegates to RepositoryEffectExecution.
    """

    def __init__(
        self,
        journal: Journal,
        execution: ExecutionCoordinator,
        attempts: RepositoryEffectAttemptStore,
        runtime_safety: RuntimeSafetyService,
        effects: RepositoryEffectExecution,
        git: GitService,
    ) -> None:
        self._journal = journal
        self._execution = execution
        self._attempts = attempts
        self._runtime_safety = runtime_safety
        self._effects = effects
        self._git = git

    def run(
        self,
        repository: Path,
        action: OperationAction,
        *,
        target: OperationTarget | None = None,
        operation_id: str | None = None,
    ) -> OperationExecution:
        intent = resolve_capability_intent(action, target)
        if not intent.definition.direct_supported:
            raise CapabilityUnavailable(
                f"Action {action.value} is not executable through the Direct effect boundary"
            )
        operation_id = operation_id or new_operation_id()
        committed = self._journal.operation(operation_id)
        if committed is not None:
            _require_matching_direct_identity(committed, repository, action, intent.target)
            return OperationExecution(stdout="", stderr="", report=committed)
        _require_no_unresolved_direct_attempt(
            self._attempts, operation_id, repository, action, intent.target
        )

        cut = FleetAdmissionPlanner.admit_repository_effect_cut(
            self._runtime_safety,
            safety=intent.definition.safety,
            repository_ids=(repository_id(repository),),
        )
        baseline = self._git.fingerprint(repository)
        try:
            ticket, attempt = self._execution.reserve_direct_with(
                expected_durable_revision=cut.durable_revision,
                mutate=lambda connection: self._claim_direct_in(
                    connection, repository, intent, baseline, operation_id
                ),
            )
        except _CommittedDirectReplay as replay:
            return OperationExecution(stdout="", stderr="", report=replay.report)
        return self._effects.execute_direct_claimed(
            attempt=attempt,
            intent=intent,
            session=self._execution.activate(ticket),
        )

    def _claim_direct_in(
        self,
        connection: sqlite3.Connection,
        repository: Path,
        intent: ResolvedCapabilityIntent,
        baseline: RepositoryStateFingerprint,
        operation_id: str,
    ) -> ClaimedRepositoryEffectAttempt:
        committed = self._journal.read_view_from_connection(connection).operation(operation_id)
        if committed is not None:
            _require_matching_direct_identity(committed, repository, intent.action, intent.target)
            raise _CommittedDirectReplay(committed)
        _require_no_unresolved_direct_attempt_in(
            self._attempts, connection, operation_id, repository, intent.action, intent.target
        )
        return self._attempts.begin_direct_in(
            connection,
            repository,
            intent.definition.durable_snapshot(),
            target=intent.target,
            baseline=baseline,
            effect_boundary=self._effects.effect_boundary(intent),
            operation_id=operation_id,
        )


def _require_no_unresolved_direct_attempt(
    attempts: RepositoryEffectAttemptStore,
    operation_id: str,
    repository: Path,
    action: OperationAction,
    target: OperationTarget | None,
) -> None:
    try:
        unresolved = attempts.get(operation_id)
    except OperationNotFound:
        return
    _raise_unresolved_direct_attempt(unresolved, operation_id, repository, action, target)


def _require_no_unresolved_direct_attempt_in(
    attempts: RepositoryEffectAttemptStore,
    connection: sqlite3.Connection,
    operation_id: str,
    repository: Path,
    action: OperationAction,
    target: OperationTarget | None,
) -> None:
    try:
        unresolved = attempts.get_in(connection, operation_id)
    except OperationNotFound:
        return
    _raise_unresolved_direct_attempt(unresolved, operation_id, repository, action, target)


def _raise_unresolved_direct_attempt(
    unresolved: RepositoryEffectAttempt,
    operation_id: str,
    repository: Path,
    action: OperationAction,
    target: OperationTarget | None,
) -> None:
    if (
        unresolved.fleet_run_id is not None
        or unresolved.repository.repository_id != repository_id(repository)
        or unresolved.action is not action
        or unresolved.target != target
    ):
        raise StateConflict(
            f"Operation identity {operation_id} is already bound to another repository effect"
        )
    raise StateConflict(
        f"Operation identity {operation_id} has an unresolved repository effect; "
        "reconcile it before retry"
    )


def _require_matching_direct_identity(
    report: OperationReport,
    repository: Path,
    action: OperationAction,
    target: OperationTarget | None,
) -> None:
    if (
        report.provenance.fleet_run_id is not None
        or report.repository.repository_id != repository_id(repository)
        or report.action is not action
        or report.target != target
    ):
        raise StateConflict(
            f"Operation identity {report.provenance.operation_id} is already bound to another repository effect"
        )
