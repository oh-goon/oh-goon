from __future__ import annotations

import hashlib
from datetime import datetime

from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityDiagnosticEvidence,
)
from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedExtensionStep,
    ResolvedGitStep,
    ResolvedKubernetesRolloutVerifyStep,
    ResolvedKubernetesSetImageStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    PlanArtifactEvidence,
    PlanEgressEvidence,
    PlanEgressRuleEvidence,
    PlanExecutionAuthorityEconomicsEvidence,
    PlanExecutionRecoveryEventEvidence,
    PlanExecutionSupervisorEvidence,
    PlanOutputEvidence,
    PlanResourceEvidence,
    PlanRetainedContentEvidence,
    PlanSnapshotEvidence,
    PlanStepAttemptEvidence,
    PlanStepExecution,
    PlanToolEvidence,
    PublishedPlanOutputEvidence,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_observation_steps import ResolvedDependencyInventoryStep
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationEvidence,
    PlanReleaseEffectEvidence,
)
from oh_goon.core.models.fleet_plan_retry import PlanRetryDecisionEvidence
from oh_goon.core.models.fleet_plan_step_contract import PlanStepEgress
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, step_succeeded


def step_evidence(
    *,
    step: ResolvedPlanStep,
    outcome: StepOutcome,
    repository_state_before: str,
    repository_state_after: str,
    worktree_files_after: tuple[str, ...],
    attempts: tuple[PlanStepAttemptEvidence, ...],
    artifacts: tuple[PlanArtifactEvidence, ...],
    outputs: tuple[PublishedPlanOutputEvidence, ...] = (),
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> PlanStepExecution:
    common = {
        "step_id": step.step_id,
        "kind": step.kind,
        "started_at": started_at,
        "finished_at": finished_at,
        "argv": canonical_argv(step, outcome),
        "stdout_evidence": outcome.stdout_evidence or output_evidence(outcome.stdout),
        "stderr_evidence": output_evidence(outcome.stderr),
        "duration_ms": outcome.duration_ms,
        "repository_state_before": repository_state_before,
        "repository_state_after": repository_state_after,
        "worktree_files_after": worktree_files_after,
        "attempts": attempts,
        "artifacts": artifacts,
        "outputs": outputs,
        "observations": outcome.observations,
    }
    if outcome.skipped:
        reason = outcome.stdout or outcome.stderr or "Step skipped"
        return SkippedPlanStepExecution(**common, reason=reason[:500])
    if not step_succeeded(step, outcome):
        return FailedPlanStepExecution(**common, returncode=outcome.returncode)
    return CompletedPlanStepExecution(**common, returncode=outcome.returncode)


def canonical_argv(step: ResolvedPlanStep, outcome: StepOutcome) -> tuple[str, ...]:
    if outcome.skipped:
        return ()
    if isinstance(step, ResolvedGitStep):
        return ("git", *step.argv)
    if hasattr(step, "argv"):
        return tuple(step.argv)
    if isinstance(step, ResolvedExtensionStep):
        return (step.command, *step.args)
    if isinstance(step, ResolvedPreCommitStep):
        return ("pre-commit", "run", "--all-files")
    if isinstance(step, ResolvedDependencyInventoryStep):
        return ("dependency-inventory",)
    if isinstance(step, ResolvedKubernetesSetImageStep):
        return (
            "kubernetes_set_image",
            f"deployment/{step.target.namespace}/{step.target.name}",
            f"container={step.target.container}",
            f"image={step.image}",
        )
    if isinstance(step, ResolvedKubernetesRolloutVerifyStep):
        return (
            "kubernetes_rollout_verify",
            f"deployment/{step.target.namespace}/{step.target.name}",
            f"container={step.target.container}",
            f"generation={step.expected_generation}",
            f"image={step.expected_image}",
        )
    return outcome.argv


def attempt_evidence(
    *,
    step: ResolvedPlanStep,
    attempt: int,
    outcome: StepOutcome,
    repository_state_before: str,
    repository_state_after: str,
    worktree_files_after: tuple[str, ...],
    tool: PlanToolEvidence | None = None,
    cache_input: PlanSnapshotEvidence | None = None,
    cache_output: PlanSnapshotEvidence | None = None,
    retained_cache: PlanRetainedContentEvidence | None = None,
    effective_egress: PlanStepEgress | None = None,
    resources: PlanResourceEvidence | None = None,
    retry: PlanRetryDecisionEvidence | None = None,
    release_effect: PlanReleaseEffectEvidence | None = None,
    release_verification: KubernetesRolloutVerificationEvidence | None = None,
    execution_authority_economics: tuple[ExecutionAuthorityEconomicsReceipt, ...] | None = None,
    execution_authority: PlanExecutionAuthorityDiagnosticEvidence | None = None,
) -> PlanStepAttemptEvidence:
    economics = (
        outcome.execution_authority_economics
        if execution_authority_economics is None
        else execution_authority_economics
    )
    return PlanStepAttemptEvidence(
        attempt=attempt,
        argv=canonical_argv(step, outcome),
        returncode=outcome.returncode,
        stdout_evidence=outcome.stdout_evidence or output_evidence(outcome.stdout),
        stderr_evidence=output_evidence(outcome.stderr),
        duration_ms=outcome.duration_ms,
        repository_state_before=repository_state_before,
        repository_state_after=repository_state_after,
        worktree_files_after=worktree_files_after,
        tool=tool,
        cache_input=cache_input,
        cache_output=cache_output,
        retained_cache=retained_cache,
        egress=PlanEgressEvidence(mode="none")
        if outcome.standard_isolation is not None
        else _egress_evidence(step, effective_egress),
        resources=outcome.standard_isolation.resources
        if outcome.standard_isolation is not None
        else resources,
        standard_isolation=outcome.standard_isolation,
        retry=retry,
        release_effect=release_effect,
        release_verification=release_verification,
        supervisor=(
            PlanExecutionSupervisorEvidence(
                leader_exited_before_group_quiescence=outcome.supervisor.leader_exited_before_group_quiescence,
                process_group_cleanup_requested=outcome.supervisor.process_group_cleanup_requested,
                cleanup_reason=outcome.supervisor.cleanup_reason,
                recovery_events=tuple(
                    PlanExecutionRecoveryEventEvidence(**event.as_dict())
                    for event in outcome.supervisor.recovery_events
                ),
            )
            if outcome.supervisor is not None
            else None
        ),
        execution_authority_economics=tuple(_economics_evidence(item) for item in economics),
        execution_authority=execution_authority,
    )


def _economics_evidence(
    receipt: ExecutionAuthorityEconomicsReceipt,
) -> PlanExecutionAuthorityEconomicsEvidence:
    return PlanExecutionAuthorityEconomicsEvidence(
        scope=receipt.scope,
        candidate_units=receipt.candidate_units,
        reused_units=receipt.reused_units,
        recomputed_units=receipt.recomputed_units,
        reused_bytes=receipt.reused_bytes,
        recomputed_bytes=receipt.recomputed_bytes,
        terminal_proof_bytes=receipt.terminal_proof_bytes,
        reuse_provenance=receipt.reuse_provenance,
        reuse_identities=receipt.reuse_identities,
        recompute_reasons=receipt.recompute_reasons,
        receipt_identity=receipt.receipt_identity,
    )


def _egress_evidence(
    step: ResolvedPlanStep, effective_policy: PlanStepEgress | None
) -> PlanEgressEvidence:
    declared = step.capabilities.egress
    policy = effective_policy or declared
    rules = tuple(
        PlanEgressRuleEvidence(cidr=rule.cidr, protocol=rule.protocol, ports=rule.ports)
        for rule in getattr(policy, "rules", ())
        if hasattr(rule, "cidr")
    )
    return PlanEgressEvidence(mode=declared.mode, rules=rules)


def output_evidence(text: str) -> PlanOutputEvidence:
    payload = text.encode("utf-8")
    return PlanOutputEvidence(
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        line_count=len(text.splitlines()),
    )
