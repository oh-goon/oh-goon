from __future__ import annotations

from datetime import datetime
from typing import Any

from oh_goon.core.capability_model import CapabilityDefinition
from oh_goon.core.errors import CapabilityUnavailable
from oh_goon.core.models.effect_attempt import ClaimedRepositoryEffectAttempt
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    CompletedAttentionOperationOutcome,
    FailedAttentionOperationOutcome,
    FailedOperationOutcome,
    OperationExecution,
    OperationReport,
    RecipeOperationEvidence,
    SuccessfulOperationOutcome,
)
from oh_goon.core.models.provenance import OperationProvenance
from oh_goon.core.models.recipe import SecurityFinding, UvRecipeResult
from oh_goon.core.models.target import UvRecipeTarget
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import Verdict
from oh_goon.engine.command import CommandResult

MAX_PERSISTED_RECIPE_OUTPUT = 80_000


def build_recipe_execution(
    *,
    attempt: ClaimedRepositoryEffectAttempt,
    started_at: datetime,
    definition: CapabilityDefinition,
    argv: tuple[str, ...],
    result: CommandResult,
    payload: dict[str, Any] | list[Any] | None,
    findings: list[SecurityFinding],
) -> OperationExecution:
    target = attempt.target
    if not isinstance(target, UvRecipeTarget):
        raise TypeError("Recipe report requires a recipe target")
    reason, verdict = recipe_semantics(definition, result.returncode, findings)
    assert definition.safety is not None
    component_count = (
        len(payload.get("components", []))
        if definition.produces_sbom and isinstance(payload, dict)
        else None
    )
    recipe_result = UvRecipeResult(
        recipe=target.recipe,
        safety=definition.safety,
        argv=argv,
        exit_code=result.returncode,
        output=(result.stdout or result.stderr)[:MAX_PERSISTED_RECIPE_OUTPUT],
        findings=findings,
        structured_output=payload,
        sbom_component_count=component_count,
    )
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
        outcome=_recipe_outcome(
            returncode=result.returncode,
            verdict=verdict,
            reason=reason,
            has_findings=bool(findings),
        ),
        evidence=RecipeOperationEvidence(kind="RECIPE", recipe=recipe_result),
    )
    return OperationExecution(
        stdout=result.stdout,
        stderr=result.stderr,
        report=report,
    )


def _recipe_outcome(*, returncode: int, verdict: Verdict, reason: ReasonCode, has_findings: bool):
    if verdict.successful:
        return SuccessfulOperationOutcome(state="successful", verdict=verdict, reason_code=reason)
    if verdict is Verdict.MANUAL_ACTION_REQUIRED:
        if has_findings:
            return CompletedAttentionOperationOutcome(
                state="completed_attention", returncode=returncode, reason_code=reason
            )
        return FailedAttentionOperationOutcome(
            state="failed_attention", returncode=returncode, reason_code=reason
        )
    return FailedOperationOutcome(state="failed", returncode=returncode, reason_code=reason)


def recipe_semantics(
    definition: CapabilityDefinition,
    returncode: int,
    findings: list[SecurityFinding],
) -> tuple[ReasonCode, Verdict]:
    execution = definition.recipe_execution
    if execution is None:
        raise CapabilityUnavailable(
            f"Recipe capability has no execution policy: {definition.capability_id}"
        )
    if execution.audit_findings and findings:
        return ReasonCode.VULNERABILITIES_FOUND, Verdict.MANUAL_ACTION_REQUIRED
    if returncode == 0:
        return ReasonCode.READY, Verdict.READY
    return execution.failure_reason, execution.failure_verdict
