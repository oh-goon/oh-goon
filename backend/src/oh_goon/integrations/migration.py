from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from oh_goon.core.errors import CapabilityUnavailable, MigrationPreconditionFailed
from oh_goon.core.migration_detection import detect_migration_candidate
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.migration import MigrationCandidate
from oh_goon.core.models.operation import OperationExecution
from oh_goon.core.models.target import MigrationTarget, OperationTarget
from oh_goon.core.ports.capability_runner import (
    CapabilityExecutionRequest
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import EffectBoundaryKind, MigrationReadiness, MigrationSource, Verdict
from oh_goon.engine.git import GitService
from oh_goon.integrations.migration_execution import execute_migration_plan
from oh_goon.integrations.migration_git import publish_migration_commit
from oh_goon.integrations.migration_plan import automatic_migration_plan
from oh_goon.integrations.migration_reporting import build_migration_execution


class MigrationRunner:
    """Coordinate one safe automatic Adopt UV migration from canonical plan to local review commit."""

    def __init__(self, git: GitService) -> None:
        self._git = git

    def effect_boundary(self, target: OperationTarget | None) -> EffectBoundaryKind:
        del target
        return EffectBoundaryKind.EXTERNAL

    def prepare(self, request: CapabilityExecutionRequest) -> MigrationRunner:
        if not isinstance(request.attempt.target, MigrationTarget):
            raise CapabilityUnavailable("Migration capability requires a migration target")
        return self

    @property
    def execution_input_id(self) -> None:
        return None

    def close(self) -> None:
        return None

    def execute(self, request: CapabilityExecutionRequest) -> OperationExecution:
        attempt = request.attempt
        target = cast(MigrationTarget, attempt.target)
        identity = attempt.repository
        repository = identity.path
        candidate = _require_safe_candidate(self._git, repository, request.cancel_requested)
        plan = automatic_migration_plan(
            candidate.source,
            has_compiled_requirements=(repository / "requirements.txt").exists(),
        )
        started_at = utc_now()
        steps = execute_migration_plan(repository, plan, request.cancel_requested)
        outputs = list(steps.output)
        if not steps.succeeded:
            return self._failed(
                request,
                target,
                candidate.source,
                started_at,
                outputs,
                [],
                plan.validation_steps,
                ReasonCode.OPERATION_FAILED,
            )

        changed = list(self._git.observe(repository, request.cancel_requested).changed_files)
        unexpected = sorted(set(changed) - plan.allowed_changed_files)
        if unexpected or not changed:
            reason = (
                ReasonCode.UNEXPECTED_FILE_CHANGES if unexpected else ReasonCode.OPERATION_FAILED
            )
            return self._failed(
                request,
                target,
                candidate.source,
                started_at,
                outputs,
                changed,
                plan.validation_steps,
                reason,
            )

        return self._publish(
            request,
            target,
            candidate.source,
            started_at,
            outputs,
            changed,
            plan.validation_steps,
        )

    def _publish(
        self,
        request: CapabilityExecutionRequest,
        target: MigrationTarget,
        source: MigrationSource,
        started_at: datetime,
        outputs: list[str],
        changed: list[str],
        validation_steps: tuple[str, ...],
    ) -> OperationExecution:
        attempt = request.attempt
        published = publish_migration_commit(
            self._git,
            attempt.repository.path,
            operation_id=attempt.operation_id,
            changed=changed,
            cancel_requested=request.cancel_requested,
        )
        outputs.extend(published.output)
        if published.commit is None:
            return self._failed(
                request,
                target,
                source,
                started_at,
                outputs,
                changed,
                validation_steps,
                ReasonCode.OPERATION_FAILED,
                branch=published.branch,
            )
        return build_migration_execution(
            attempt=attempt,
            source=source,
            started_at=started_at,
            verdict=Verdict.UPDATED,
            reason=ReasonCode.MIGRATION_APPLIED,
            outputs=outputs,
            changed=changed,
            validation_steps=validation_steps,
            branch=published.branch,
            commit=published.commit,
        )

    @staticmethod
    def _failed(
        request: CapabilityExecutionRequest,
        target: MigrationTarget,
        source: MigrationSource,
        started_at: datetime,
        outputs: list[str],
        changed: list[str],
        validation_steps: tuple[str, ...],
        reason: ReasonCode,
        *,
        branch: str | None = None,
    ) -> OperationExecution:
        attempt = request.attempt
        return build_migration_execution(
            attempt=attempt,
            source=source,
            started_at=started_at,
            verdict=Verdict.FAILED,
            reason=reason,
            outputs=outputs,
            changed=changed,
            validation_steps=validation_steps,
            branch=branch,
        )


def _require_safe_candidate(
    git: GitService, repository: Path, cancel_requested: Callable[[], bool] | None
) -> MigrationCandidate:
    candidate = detect_migration_candidate(repository)
    if candidate.readiness is not MigrationReadiness.SAFE_AUTOMATIC:
        raise MigrationPreconditionFailed(
            f"Repository is not safe for automatic Adopt UV migration: {candidate.readiness.value}"
        )
    if git.observe(repository, cancel_requested).changed_files:
        raise MigrationPreconditionFailed("Adopt UV requires a clean Git worktree")
    return candidate
