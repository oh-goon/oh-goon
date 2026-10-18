from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.reconciliation import (
    ConfirmedAppliedRepositoryReconciliation,
    ConfirmedNotAppliedRepositoryReconciliation,
    IndeterminateRepositoryReconciliation,
    PartiallyAppliedRepositoryReconciliation,
    RepositoryReconciliation,
)
from oh_goon.core.product_identity import repository_id
from oh_goon.core.vocabulary import CommandSafety, ReconciliationOutcome
from oh_goon.engine.git import GitService


class RepositoryReconciler:
    """Classify uncertain external effects from durable intent plus repository evidence."""

    def __init__(self, git: GitService) -> None:
        self._git = git

    def reconcile(
        self,
        repository: Path,
        operation_id: str,
        *,
        report: OperationReport | None,
        safety: CommandSafety,
        baseline: RepositoryStateFingerprint,
    ) -> RepositoryReconciliation:
        if report is not None and (
            report.provenance.operation_id != operation_id
            or report.repository.repository_id != repository_id(repository)
        ):
            raise StateConflict("Reconciliation report belongs to another repository effect")
        current = self._git.fingerprint(repository)
        if report is not None:
            return ConfirmedAppliedRepositoryReconciliation(
                outcome=ReconciliationOutcome.CONFIRMED_APPLIED,
                reason="Canonical Journal report is present for the claimed operation identity",
                baseline=baseline,
                current=current,
            )
        if safety is CommandSafety.READ_ONLY:
            return ConfirmedNotAppliedRepositoryReconciliation(
                outcome=ReconciliationOutcome.CONFIRMED_NOT_APPLIED,
                reason="Read-only capability has no repository mutation to replay",
                baseline=baseline,
                current=current,
            )
        if safety in {CommandSafety.ENVIRONMENT_MUTATION, CommandSafety.OPAQUE_MUTATION}:
            return IndeterminateRepositoryReconciliation(
                outcome=ReconciliationOutcome.INDETERMINATE,
                reason=(
                    "Repository files cannot prove whether an opaque/environment mutation completed; "
                    "automatic replay remains blocked"
                ),
                baseline=baseline,
                current=current,
            )
        if current.state_id == baseline.state_id:
            return ConfirmedNotAppliedRepositoryReconciliation(
                outcome=ReconciliationOutcome.CONFIRMED_NOT_APPLIED,
                reason="Repository fingerprint is unchanged from the durable pre-effect baseline",
                baseline=baseline,
                current=current,
            )
        return PartiallyAppliedRepositoryReconciliation(
            outcome=ReconciliationOutcome.PARTIALLY_APPLIED,
            reason=(
                "Repository differs from the pre-effect baseline but no canonical operation report exists; "
                "some external effects may have been applied"
            ),
            baseline=baseline,
            current=current,
        )
