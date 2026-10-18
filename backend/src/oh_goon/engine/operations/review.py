from __future__ import annotations

from oh_goon.core.product_identity import opaque_identity_token
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    FailedOperationOutcome,
    OperationOutcome,
    OperationReport,
    ReviewOperationEvidence,
    SuccessfulOperationOutcome,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitFinalState, Verdict
from oh_goon.engine.operation_execution_context import OperationContext
from oh_goon.engine.operations.git_evidence import mutation_git_result
from oh_goon.engine.project import policy_snapshot
from oh_goon.engine.reporting import execution, provenance


def run(context: OperationContext):
    started = utc_now()
    before_observation = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    )
    before = before_observation.fingerprint
    policy = policy_snapshot(context.attempt.repository.path)
    if before_observation.changed_files != ("uv.lock",):
        return _report(
            context,
            started,
            before,
            policy,
            "",
            "Prepare review requires uv.lock to be the only changed file",
            FailedOperationOutcome(
                state="failed", returncode=1, reason_code=ReasonCode.UNEXPECTED_FILE_CHANGES
            ),
        )
    diff_check = context.git.run(
        "diff",
        "--check",
        "--",
        "uv.lock",
        cwd=context.attempt.repository.path,
        timeout=30.0,
        cancel_requested=context.cancel_requested,
    )
    if diff_check.returncode != 0:
        return _report(
            context,
            started,
            before,
            policy,
            diff_check.stdout,
            diff_check.stderr,
            FailedOperationOutcome(
                state="failed",
                returncode=diff_check.returncode,
                reason_code=ReasonCode.VERIFICATION_FAILED,
            ),
        )
    review_branch, message = _review_identity(context)
    returncode, stdout, stderr = _create_review_commit(context, review_branch, message)
    if returncode != 0:
        return _report(
            context,
            started,
            before,
            policy,
            stdout,
            stderr,
            FailedOperationOutcome(
                state="failed", returncode=returncode, reason_code=ReasonCode.OPERATION_FAILED
            ),
        )
    return _report(
        context,
        started,
        before,
        policy,
        stdout,
        "",
        SuccessfulOperationOutcome(
            state="successful", verdict=Verdict.UPDATED, reason_code=ReasonCode.REVIEW_PREPARED
        ),
    )


def _review_identity(context: OperationContext) -> tuple[str, str]:
    suffix = opaque_identity_token(context.attempt.fleet_run_id or context.attempt.operation_id)
    branch_name = f"chore/oh-goon/dependencies-{suffix}"
    return branch_name, "chore(deps): update dependencies"


def _create_review_commit(
    context: OperationContext, review_branch: str, message: str
) -> tuple[int, str, str]:
    outputs: list[str] = []
    for argv in (
        ("switch", "-c", review_branch),
        ("add", "uv.lock"),
        ("diff", "--cached", "--check"),
        ("commit", "-m", message),
    ):
        result = context.git.run(
            *argv,
            cwd=context.attempt.repository.path,
            timeout=120.0,
            cancel_requested=context.cancel_requested,
        )
        if result.stdout or result.stderr:
            outputs.append(result.stdout or result.stderr)
        if result.returncode != 0:
            return result.returncode, "\n".join(outputs), result.stderr
    return 0, "\n".join(outputs), ""


def _report(
    context,
    started,
    before,
    policy,
    stdout,
    stderr,
    outcome: OperationOutcome,
):
    after_observation = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    )
    after = after_observation.fingerprint
    clean = not after_observation.changed_files
    git = mutation_git_result(
        before,
        after,
        final_state=GitFinalState.CLEAN if clean else GitFinalState.UNEXPECTED_CHANGES,
        unexpected_files=tuple(after_observation.changed_files),
    )
    report = OperationReport(
        capability=context.attempt.capability,
        target=context.attempt.target,
        provenance=provenance(context, started, before, after, policy),
        repository=context.attempt.repository,
        outcome=outcome,
        evidence=ReviewOperationEvidence(kind="REVIEW", policy=policy, git=git),
    )
    return execution(stdout, stderr, report)
