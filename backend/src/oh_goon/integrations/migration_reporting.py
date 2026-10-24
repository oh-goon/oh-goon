from __future__ import annotations

from datetime import datetime

from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.migration import MigrationResult
from oh_goon.core.models.operation import (
    FailedOperationOutcome,
    MigrationOperationEvidence,
    OperationExecution,
    OperationReport,
    SuccessfulOperationOutcome,
)
from oh_goon.core.models.provenance import OperationProvenance
from oh_goon.core.models.target import MigrationTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    MigrationReadiness,
    MigrationSource,
    Verdict,
)


def build_migration_execution(
    *,
    attempt: ClaimedRepositoryEffectAttempt,
    source: MigrationSource,
    started_at: datetime,
    verdict: Verdict,
    reason: ReasonCode,
    outputs: list[str],
    changed: list[str],
    validation_steps: tuple[str, ...],
    branch: str | None = None,
    commit: str | None = None,
) -> OperationExecution:
    target = attempt.target
    if not isinstance(target, MigrationTarget):
        raise TypeError("Migration report requires a migration target")
    report = OperationReport(
        capability=attempt.capability,
        target=target,
        provenance=OperationProvenance(
            operation_id=attempt.operation_id,
            fleet_run_id=attempt.fleet_run_id,
            started_at=started_at,
            finished_at=utc_now(),
            before=None,
            after=None,
        ),
        repository=attempt.repository,
        outcome=(
            SuccessfulOperationOutcome.model_validate(
                {"state": "successful", "verdict": verdict, "reason_code": reason}
            )
            if verdict.successful
            else FailedOperationOutcome(state="failed", returncode=1, reason_code=reason)
        ),
        evidence=MigrationOperationEvidence(
            kind="MIGRATION",
            migration=MigrationResult(
                source=source,
                readiness=MigrationReadiness.SAFE_AUTOMATIC,
                created_files=tuple(
                    name for name in changed if name in {"pyproject.toml", "uv.lock"}
                ),
                changed_files=tuple(changed),
                validation_steps=tuple(validation_steps),
                branch=branch,
                commit=commit,
            ),
        ),
    )
    output = "\n".join(part for part in outputs if part)
    return OperationExecution(
        stdout=output,
        stderr="",
        report=report,
    )
