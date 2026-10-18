from __future__ import annotations

import pytest

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    ChangedGitHead,
    DependenciesUpdated,
    DependencyUpdateFailed,
    GitMutationResult,
    NamedGitPosition,
    NoDependencyUpdates,
    PreservedGitHead,
    RepositoryLockInvalidValidation,
    RepositorySyncFailedValidation,
    RepositoryValidationCompleted,
    ValidGitWorktreeInvariant,
    ViolatedGitWorktreeInvariant,
)
from oh_goon.core.vocabulary import (
    AuditState,
    CheckState,
    GitFinalState,
    ValidationState,
    Verdict,
    WorktreeState,
)
from oh_goon.engine.operations.update_outcome import UpdateEvidence, classify_update_outcome
from tests.factories.policy import make_policy_snapshot


def valid_validation(**overrides: object) -> RepositoryValidationCompleted:
    values: dict[str, object] = {
        "state": "repository_completed",
        "environment": ValidationState.VALID,
        "security_audit": AuditState.SKIPPED,
        "tests": CheckState.PASSED,
    }
    values.update(overrides)
    return RepositoryValidationCompleted(**values)  # type: ignore[arg-type]


def git_result(
    *, head_changed: bool = False, unexpected_files: tuple[str, ...] = ()
) -> GitMutationResult:
    return GitMutationResult(
        position=NamedGitPosition(state="named", branch="main"),
        initial_state=WorktreeState.CLEAN,
        final_state=(
            GitFinalState.UNEXPECTED_CHANGES
            if unexpected_files
            else GitFinalState.CHANGED_BY_UPDATE
        ),
        head=(
            ChangedGitHead(state="changed", before="before", after="after")
            if head_changed
            else PreservedGitHead(state="preserved", head="before")
        ),
        worktree=(
            ViolatedGitWorktreeInvariant(state="failed", unexpected_files=unexpected_files)
            if unexpected_files
            else ValidGitWorktreeInvariant(state="passed")
        ),
    )


def evidence(**overrides: object) -> UpdateEvidence:
    values: dict[str, object] = {
        "command_returncode": 0,
        "changed": True,
        "validation": valid_validation(),
        "policy": make_policy_snapshot(),
        "git": git_result(),
    }
    values.update(overrides)
    return UpdateEvidence(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("validation", "reason"),
    [
        (valid_validation(tests=CheckState.FAILED), ReasonCode.TESTS_FAILED),
        (
            valid_validation(security_audit=AuditState.VULNERABILITIES_FOUND),
            ReasonCode.VULNERABILITIES_FOUND,
        ),
        (
            RepositorySyncFailedValidation(state="repository_sync_failed"),
            ReasonCode.VERIFICATION_FAILED,
        ),
        (
            RepositoryLockInvalidValidation(state="repository_lock_invalid"),
            ReasonCode.VERIFICATION_FAILED,
        ),
        (valid_validation(environment=ValidationState.INVALID), ReasonCode.VERIFICATION_FAILED),
    ],
)
def test_validation_failures_have_explicit_precedence(
    validation: object, reason: ReasonCode
) -> None:
    outcome = classify_update_outcome(evidence(validation=validation))
    assert outcome.outcome.reason_code is reason
    assert outcome.outcome.verdict is Verdict.FAILED
    assert outcome.outcome.returncode == 1
    assert isinstance(outcome.dependencies, DependenciesUpdated)


def test_required_unavailable_audit_is_failure() -> None:
    policy = make_policy_snapshot()
    policy = policy.model_copy(
        update={"values": policy.values.model_copy(update={"require_audit": True})}
    )
    outcome = classify_update_outcome(
        evidence(
            policy=policy,
            validation=valid_validation(security_audit=AuditState.UNAVAILABLE),
        )
    )
    assert outcome.outcome.reason_code is ReasonCode.AUDIT_UNAVAILABLE
    assert outcome.outcome.verdict is Verdict.FAILED


def test_git_integrity_outranks_command_and_validation_results() -> None:
    head = classify_update_outcome(
        evidence(
            command_returncode=2,
            validation=None,
            git=git_result(head_changed=True, unexpected_files=("uv.lock",)),
        )
    )
    worktree = classify_update_outcome(
        evidence(
            command_returncode=2, validation=None, git=git_result(unexpected_files=("uv.lock",))
        )
    )
    assert head.outcome.reason_code is ReasonCode.GIT_POSITION_CHANGED
    assert worktree.outcome.reason_code is ReasonCode.UNEXPECTED_FILE_CHANGES


def test_command_failure_preserves_command_returncode_and_failed_dependency_result() -> None:
    outcome = classify_update_outcome(evidence(command_returncode=7, validation=None))
    assert outcome.outcome.reason_code is ReasonCode.OPERATION_FAILED
    assert outcome.outcome.returncode == 7
    assert isinstance(outcome.dependencies, DependencyUpdateFailed)


def test_success_classifies_updated_and_unchanged_results() -> None:
    updated = classify_update_outcome(evidence(changed=True))
    unchanged = classify_update_outcome(evidence(changed=False))
    assert (updated.outcome.reason_code, updated.outcome.verdict, updated.dependencies.state) == (
        ReasonCode.DEPENDENCIES_UPDATED,
        Verdict.UPDATED,
        "updated",
    )
    assert isinstance(updated.dependencies, DependenciesUpdated)
    assert (
        unchanged.outcome.reason_code,
        unchanged.outcome.verdict,
        unchanged.dependencies.state,
    ) == (
        ReasonCode.NO_UPDATES,
        Verdict.UNCHANGED,
        "no_updates",
    )
    assert isinstance(unchanged.dependencies, NoDependencyUpdates)
