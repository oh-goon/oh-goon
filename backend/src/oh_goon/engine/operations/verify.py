from __future__ import annotations

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    FailedOperationOutcome,
    OperationReport,
    SuccessfulOperationOutcome,
    VerifyOperationEvidence,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import Verdict
from oh_goon.engine.operation_execution_context import OperationContext
from oh_goon.engine.operations.validation import (
    repository_validation_failure_reason,
    validate_repository,
)
from oh_goon.engine.project import policy_snapshot
from oh_goon.engine.reporting import execution, provenance


def run(context: OperationContext):
    started = utc_now()
    before = context.git.fingerprint(context.attempt.repository.path, context.cancel_requested)
    policy = policy_snapshot(context.attempt.repository.path)
    validation, stdout, stderr = validate_repository(
        context.attempt.repository.path, policy, context.cancel_requested
    )
    reason = repository_validation_failure_reason(validation, policy)
    if reason is None:
        reason, verdict = ReasonCode.READY, Verdict.READY
    else:
        verdict = Verdict.FAILED
    returncode = 0 if verdict.successful else 1
    report = OperationReport(
        capability=context.attempt.capability,
        target=context.attempt.target,
        provenance=provenance(
            context,
            started,
            before,
            context.git.fingerprint(context.attempt.repository.path, context.cancel_requested),
            policy,
        ),
        repository=context.attempt.repository,
        outcome=(
            SuccessfulOperationOutcome(state="successful", verdict=verdict, reason_code=reason)
            if verdict.successful
            else FailedOperationOutcome(state="failed", returncode=returncode, reason_code=reason)
        ),
        evidence=VerifyOperationEvidence(kind="VERIFY", policy=policy, validation=validation),
    )
    return execution(stdout, stderr, report)
