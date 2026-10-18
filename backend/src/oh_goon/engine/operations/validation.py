from __future__ import annotations

import shlex
from pathlib import Path

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    RepositoryLockInvalidValidation,
    RepositorySyncFailedValidation,
    RepositoryValidationCompleted,
    RepositoryValidationResult,
)
from oh_goon.core.models.policy import PolicySnapshot
from oh_goon.core.vocabulary import AuditState, CheckState, ValidationState
from oh_goon.engine.command import CommandResult
from oh_goon.engine.operations.command_execution import Cancel, run_operation_command


def validate_repository(
    repository: Path,
    policy: PolicySnapshot,
    cancel_requested: Cancel = None,
) -> tuple[RepositoryValidationResult, str, str]:
    output = _ValidationOutput()

    sync = run_operation_command(
        repository, "uv", "sync", "--frozen", cancel_requested=cancel_requested
    )
    output.capture(sync)
    if sync.returncode != 0:
        return (
            RepositorySyncFailedValidation(state="repository_sync_failed"),
            output.stdout(),
            output.stderr(),
        )

    lock = run_operation_command(
        repository, "uv", "lock", "--check", cancel_requested=cancel_requested
    )
    output.capture(lock)
    if lock.returncode != 0:
        return (
            RepositoryLockInvalidValidation(state="repository_lock_invalid"),
            output.stdout(),
            output.stderr(),
        )

    environment = run_operation_command(
        repository, "uv", "sync", "--check", cancel_requested=cancel_requested
    )
    output.capture(environment)
    environment_state = (
        ValidationState.VALID if environment.returncode == 0 else ValidationState.INVALID
    )

    audit_state = AuditState.SKIPPED
    if policy.values.require_audit:
        audit = run_operation_command(
            repository,
            "uv",
            "--preview",
            "audit",
            "--frozen",
            timeout=float(policy.values.audit_timeout_seconds),
            cancel_requested=cancel_requested,
        )
        output.capture(audit)
        if audit.returncode == 0:
            audit_state = AuditState.PASSED
        elif "vulnerab" in (audit.stdout + audit.stderr).lower():
            audit_state = AuditState.VULNERABILITIES_FOUND
        else:
            audit_state = AuditState.UNAVAILABLE

    tests = run_operation_command(
        repository,
        *shlex.split(policy.values.test_command),
        cancel_requested=cancel_requested,
    )
    output.capture(tests)
    return (
        RepositoryValidationCompleted(
            state="repository_completed",
            environment=environment_state,
            security_audit=audit_state,
            tests=CheckState.PASSED if tests.returncode == 0 else CheckState.FAILED,
        ),
        output.stdout(),
        output.stderr(),
    )


def repository_validation_failure_reason(
    validation: RepositoryValidationResult,
    policy: PolicySnapshot,
) -> ReasonCode | None:
    """Classify a completed repository-validation attempt from its structural outcome."""
    if isinstance(validation, (RepositorySyncFailedValidation, RepositoryLockInvalidValidation)):
        return ReasonCode.VERIFICATION_FAILED
    if validation.tests is CheckState.FAILED:
        return ReasonCode.TESTS_FAILED
    if (
        validation.security_audit is AuditState.VULNERABILITIES_FOUND
        and policy.values.fail_on_audit
    ):
        return ReasonCode.VULNERABILITIES_FOUND
    if validation.security_audit is AuditState.UNAVAILABLE and policy.values.require_audit:
        return ReasonCode.AUDIT_UNAVAILABLE
    if validation.environment is ValidationState.INVALID:
        return ReasonCode.VERIFICATION_FAILED
    return None


class _ValidationOutput:
    def __init__(self) -> None:
        self._stdout: list[str] = []
        self._stderr: list[str] = []

    def capture(self, result: CommandResult) -> None:
        if result.stdout:
            self._stdout.append(result.stdout)
        if result.stderr:
            self._stderr.append(result.stderr)

    def stdout(self) -> str:
        return "\n".join(part for part in self._stdout if part)

    def stderr(self) -> str:
        return "\n".join(part for part in self._stderr if part)
