from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import TypedDict

from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityDiagnosticEvidence,
)
from oh_goon.core.models.execution_authority_economics import (
    ExecutionAuthorityEconomicsReceipt,
)
from oh_goon.core.models.fleet_observation import FleetObservationEvidence, NativeAgentObservation
from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    ResolvedAgentStep,
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
    PlanStepCancellationEvidence,
    PlanStepExecution,
    PlanStepSettlementEvidence,
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
from oh_goon.engine.command import CommandCancelledError, CommandSupervisorReceipt
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations._fleet_plan_step_outcome import (
    StepOutcome,
    process_boundary_settled,
    settle_step_execution,
)


class _StepEvidenceFields(TypedDict):
    step_id: str
    kind: str
    started_at: datetime | None
    finished_at: datetime | None
    argv: tuple[str, ...]
    stdout_evidence: PlanOutputEvidence
    stderr_evidence: PlanOutputEvidence
    duration_ms: int
    repository_state_before: str
    repository_state_after: str
    worktree_files_after: tuple[str, ...]
    attempts: tuple[PlanStepAttemptEvidence, ...]
    artifacts: tuple[PlanArtifactEvidence, ...]
    outputs: tuple[PublishedPlanOutputEvidence, ...]
    observations: tuple[FleetObservationEvidence, ...]
    settlement: PlanStepSettlementEvidence


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
    settlement = settle_step_execution(step, outcome)
    common: _StepEvidenceFields = {
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
        "settlement": settlement,
    }
    if settlement.outcome == "SKIPPED":
        reason = outcome.stdout or outcome.stderr or "Step skipped"
        return SkippedPlanStepExecution(**common, reason=reason[:500])
    if settlement.outcome != "SUCCESS":
        return FailedPlanStepExecution(
            **common, returncode=outcome.returncode or PLAN_CONTRACT_RETURN_CODE
        )
    return CompletedPlanStepExecution(**common, returncode=outcome.returncode)


def cancellation_evidence(
    step: ResolvedPlanStep, error: OperationCancelledError
) -> PlanStepCancellationEvidence:
    """Retain the physical owner's facts without launching post-cancellation work."""
    outcome = getattr(error, "outcome", None)
    if not isinstance(outcome, StepOutcome):
        physical = error.__cause__
        supervisor = physical.supervisor if isinstance(physical, CommandCancelledError) else None
        if (
            not isinstance(supervisor, CommandSupervisorReceipt)
            or supervisor.cleanup_reason
            not in {"none", "cancel", "timeout", "authority", "output_cleanup", "output_limit"}
            or supervisor.process_group_cleanup_requested == (supervisor.cleanup_reason == "none")
        ):
            supervisor = None
        outcome = StepOutcome(
            argv=(),
            returncode=PLAN_CONTRACT_RETURN_CODE,
            supervisor=supervisor,
            standard_isolation=physical.standard_isolation
            if isinstance(physical, CommandCancelledError)
            else None,
            physical_execution_id=physical.physical_execution_id
            if isinstance(physical, CommandCancelledError)
            else None,
            duration_ms=physical.duration_ms if isinstance(physical, CommandCancelledError) else 0,
        )
    elif outcome.supervisor is not None and (
        outcome.supervisor.process_group_cleanup_requested
        == (outcome.supervisor.cleanup_reason == "none")
    ):
        outcome = replace(outcome, supervisor=None)
    settlement = settle_step_execution(step, outcome)
    if not (
        settlement.outcome == "CANCELLED"
        and settlement.cleanup == "VERIFIED"
        and outcome.physical_execution_id is not None
    ):
        unverified = process_boundary_settled(outcome) is False
        settlement = PlanStepSettlementEvidence(
            outcome="INCOMPLETE",
            cleanup="UNVERIFIED" if unverified else "UNAVAILABLE",
            reason="CLEANUP_UNVERIFIED" if unverified else "SUPERVISION_UNAVAILABLE",
            observed_returncode=outcome.observed_returncode,
        )
    return PlanStepCancellationEvidence(
        settlement=settlement,
        supervisor=_supervisor_evidence(outcome.supervisor),
        standard_isolation=outcome.standard_isolation,
        physical_execution_id=outcome.physical_execution_id,
        duration_ms=outcome.duration_ms,
        native_agent_observations=tuple(
            item
            for item in getattr(error, "native_observations", ())
            if isinstance(item, NativeAgentObservation)
        ),
    )


def canonical_argv(step: ResolvedPlanStep, outcome: StepOutcome) -> tuple[str, ...]:
    if outcome.skipped:
        return ()
    if isinstance(step, ResolvedAgentStep) and step.workload.harness == "native":
        return outcome.argv
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
        observed_returncode=outcome.observed_returncode,
        stdout_evidence=outcome.stdout_evidence or output_evidence(outcome.stdout),
        stderr_evidence=output_evidence(outcome.stderr),
        duration_ms=outcome.duration_ms,
        repository_state_before=repository_state_before,
        repository_state_after=repository_state_after,
        worktree_files_after=worktree_files_after,
        tool=tool,
        uv_preparation=outcome.uv_preparation,
        cache_input=cache_input,
        cache_output=cache_output,
        retained_cache=retained_cache,
        egress=PlanEgressEvidence(
            mode="host_unrestricted"
            if outcome.standard_isolation.host_and_external_ip_networking == "host_unrestricted"
            else "none"
        )
        if outcome.standard_isolation is not None
        else _egress_evidence(step, effective_egress),
        resources=outcome.standard_isolation.resources
        if outcome.standard_isolation is not None
        else resources,
        standard_isolation=outcome.standard_isolation,
        retry=retry,
        release_effect=release_effect,
        release_verification=release_verification,
        supervisor=_supervisor_evidence(outcome.supervisor),
        execution_authority_economics=tuple(_economics_evidence(item) for item in economics),
        execution_authority=execution_authority,
    )


def _supervisor_evidence(
    supervisor: CommandSupervisorReceipt | None,
) -> PlanExecutionSupervisorEvidence | None:
    if supervisor is None:
        return None
    return PlanExecutionSupervisorEvidence(
        leader_exited_before_group_quiescence=supervisor.leader_exited_before_group_quiescence,
        process_group_cleanup_requested=supervisor.process_group_cleanup_requested,
        cleanup_reason=supervisor.cleanup_reason,
        final_process_group_quiescent=supervisor.final_process_group_quiescent,
        forced_termination=supervisor.forced_termination,
        recovery_events=tuple(
            PlanExecutionRecoveryEventEvidence.model_validate(event.as_dict())
            for event in supervisor.recovery_events
        ),
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
