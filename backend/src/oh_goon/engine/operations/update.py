from __future__ import annotations

from oh_goon.core.file_digest import sha256_file
from oh_goon.core.models.operation import OperationReport, UpdateOperationEvidence
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitFinalState
from oh_goon.engine.operation_execution_context import OperationContext
from oh_goon.engine.operations.command_execution import run_operation_command
from oh_goon.engine.operations.git_evidence import mutation_git_result
from oh_goon.engine.operations.update_outcome import UpdateEvidence, classify_update_outcome
from oh_goon.engine.operations.validation import validate_repository
from oh_goon.engine.project import policy_snapshot
from oh_goon.engine.reporting import execution, provenance


def run(context: OperationContext):
    started = utc_now()
    before_observation = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    )
    before = before_observation.fingerprint
    policy = policy_snapshot(context.attempt.repository.path)
    lock_path = context.attempt.repository.path / "uv.lock"
    old_lock = sha256_file(lock_path) if lock_path.is_file() else None
    resolved = run_operation_command(
        context.attempt.repository.path,
        "uv",
        "lock",
        "--upgrade",
        timeout=context.timeout_seconds,
        cancel_requested=context.cancel_requested,
    )

    validation = None
    validation_out = ""
    validation_err = ""
    if resolved.returncode == 0:
        unexpected = [
            name
            for name in context.git.observe(
                context.attempt.repository.path, context.cancel_requested
            ).changed_files
            if name != "uv.lock"
        ]
        if not unexpected:
            validation, validation_out, validation_err = validate_repository(
                context.attempt.repository.path, policy, context.cancel_requested
            )

    return _finish(
        context=context,
        started=started,
        before=before,
        policy=policy,
        old_lock=old_lock,
        command_returncode=resolved.returncode,
        stdout="\n".join(part for part in (resolved.stdout, validation_out) if part),
        stderr="\n".join(part for part in (resolved.stderr, validation_err) if part),
        validation=validation,
    )


def _finish(
    *,
    context: OperationContext,
    started,
    before,
    policy,
    old_lock: str | None,
    command_returncode: int,
    stdout: str,
    stderr: str,
    validation,
):
    lock_path = context.attempt.repository.path / "uv.lock"
    current_lock = sha256_file(lock_path) if lock_path.is_file() else None
    changed = old_lock != current_lock
    after_observation = context.git.observe(
        context.attempt.repository.path, context.cancel_requested
    )
    after = after_observation.fingerprint
    unexpected = [name for name in after_observation.changed_files if name != "uv.lock"]
    git = mutation_git_result(
        before,
        after,
        final_state=(
            GitFinalState.UNEXPECTED_CHANGES
            if unexpected
            else GitFinalState.CHANGED_BY_UPDATE
            if changed
            else GitFinalState.CLEAN
        ),
        unexpected_files=tuple(unexpected),
    )
    outcome = classify_update_outcome(
        UpdateEvidence(
            command_returncode=command_returncode,
            changed=changed,
            validation=validation,
            policy=policy,
            git=git,
        )
    )
    report = OperationReport(
        capability=context.attempt.capability,
        target=context.attempt.target,
        provenance=provenance(context, started, before, after, policy),
        repository=context.attempt.repository,
        outcome=outcome.outcome,
        evidence=UpdateOperationEvidence(
            kind="UPDATE",
            policy=policy,
            git=git,
            dependencies=outcome.dependencies,
            validation=validation,
        ),
    )
    return execution(stdout, stderr, report)
