from __future__ import annotations

from oh_goon.core.models.execution_assignment import ExecutionAssignment
from oh_goon.core.models.recovery_lifecycle import RecoveryAdmission, RecoveryFailureEvidence
from oh_goon.core.models.workspace import CandidateCheckpoint


class RecoveryLifecycle:
    """Single semantic owner for bounded retry/remediation admission.

    This class never launches work. It only decides whether another physical attempt may
    be issued against the exact immutable work/checkpoint lineage.
    """

    def admit(
        self,
        *,
        previous_assignment: ExecutionAssignment,
        checkpoint: CandidateCheckpoint,
        failure: RecoveryFailureEvidence,
        lineage_id: str,
        attempt_ordinal: int,
        max_attempts: int,
        exact_work_identity: str,
    ) -> RecoveryAdmission:
        if previous_assignment.workspace_id != checkpoint.workspace_id:
            raise ValueError("Recovery checkpoint belongs to another workspace")
        if previous_assignment.input_checkpoint_id != checkpoint.checkpoint_id:
            raise ValueError("Recovery cannot cross candidate checkpoints")
        if failure.work_identity != exact_work_identity:
            raise ValueError("Recovery evidence does not describe the admitted immutable work")
        if attempt_ordinal < 2:
            raise ValueError("Recovery attempt ordinal must follow an initial attempt")
        if max_attempts < 2 or max_attempts > 32:
            raise ValueError("Recovery max_attempts must be within 2..32")

        if attempt_ordinal > max_attempts:
            return self._decision(
                previous_assignment=previous_assignment,
                checkpoint=checkpoint,
                failure=failure,
                lineage_id=lineage_id,
                attempt_ordinal=attempt_ordinal,
                max_attempts=max_attempts,
                exact_work_identity=exact_work_identity,
                disposition="BLOCKED",
                reason="retry budget exhausted",
            )
        if failure.failure_class == "environment":
            disposition, reason = (
                "ADMITTED",
                "exact immutable work may be retried after environment failure",
            )
        elif failure.failure_class == "timeout":
            disposition, reason = (
                "ADMITTED",
                "exact immutable work may be retried after bounded timeout",
            )
        elif failure.failure_class == "product_assertion":
            disposition, reason = (
                "BLOCKED",
                "product/assertion failure requires remediation, not environment retry",
            )
        elif failure.failure_class == "cancelled":
            disposition, reason = "BLOCKED", "cancellation is terminal unless separately readmitted"
        else:
            disposition, reason = (
                "BLOCKED",
                "authority/integrity failure cannot be recovered by retry",
            )
        return self._decision(
            previous_assignment=previous_assignment,
            checkpoint=checkpoint,
            failure=failure,
            lineage_id=lineage_id,
            attempt_ordinal=attempt_ordinal,
            max_attempts=max_attempts,
            exact_work_identity=exact_work_identity,
            disposition=disposition,
            reason=reason,
        )

    @staticmethod
    def _decision(
        *,
        previous_assignment: ExecutionAssignment,
        checkpoint: CandidateCheckpoint,
        failure: RecoveryFailureEvidence,
        lineage_id: str,
        attempt_ordinal: int,
        max_attempts: int,
        exact_work_identity: str,
        disposition: str,
        reason: str,
    ) -> RecoveryAdmission:
        return RecoveryAdmission(
            lineage_id=lineage_id,
            parent_assignment_id=previous_assignment.assignment_id,
            workspace_id=checkpoint.workspace_id,
            checkpoint_id=checkpoint.checkpoint_id,
            work_identity=exact_work_identity,
            failure_evidence_id=failure.evidence_id,
            attempt_ordinal=attempt_ordinal,
            max_attempts=max_attempts,
            disposition=disposition,
            reason=reason,
        )
