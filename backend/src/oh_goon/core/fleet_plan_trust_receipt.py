from __future__ import annotations

from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PresentPlanArtifact,
    SkippedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationOutcome,
    KubernetesSetImageReleaseEvidence,
    OciPublishReleaseEvidence,
)
from oh_goon.core.models.fleet_plan_trust_receipt import (
    FleetPlanAdmissionTrustReceipt,
    FleetPlanArtifactTrustReceipt,
    FleetPlanBoundaryTrustReceipt,
    FleetPlanCacheAttemptTrustReceipt,
    FleetPlanEgressTrustReceipt,
    FleetPlanFinalTrustReceipt,
    FleetPlanKubernetesReleaseTrustReceipt,
    FleetPlanOciReleaseTrustReceipt,
    FleetPlanOutputTrustReceipt,
    FleetPlanReadinessStepTrustReceipt,
    FleetPlanReadinessTrustReceipt,
    FleetPlanReleaseStepTrustReceipt,
    FleetPlanReleaseTrustReceipt,
    FleetPlanRolloutVerificationTrustReceipt,
    FleetPlanRuntimeToolTrustReceipt,
    FleetPlanRuntimeTrustReceipt,
    FleetPlanTeardownStepTrustReceipt,
    FleetPlanTeardownTrustReceipt,
    FleetPlanTrustReceipt,
    FleetPlanWorkspaceTrustReceipt,
    RepositoryFleetPlanTrustReceipt,
)
from oh_goon.core.models.operation import FleetPlanOperationEvidence, OperationReport


def project_repository_fleet_plan_trust_receipts(
    operations: tuple[OperationReport, ...],
) -> tuple[RepositoryFleetPlanTrustReceipt, ...]:
    return tuple(
        RepositoryFleetPlanTrustReceipt(
            repository_id=report.repository.repository_id,
            receipt=project_fleet_plan_trust_receipt(report.evidence.plan),
        )
        for report in operations
        if isinstance(report.evidence, FleetPlanOperationEvidence)
    )


def project_fleet_plan_trust_receipt(plan: FleetPlanResult) -> FleetPlanTrustReceipt:
    input_evidence = plan.execution_inputs
    teardown = _teardown(plan)
    return FleetPlanTrustReceipt(
        plan_id=plan.plan_id,
        input_id=input_evidence.input_id if input_evidence is not None else None,
        repository_id=input_evidence.repository_id if input_evidence is not None else None,
        admission=_admission(plan),
        runtime=_runtime(plan),
        workspace=_workspace(plan),
        boundaries=_boundaries(plan),
        release=_release(plan),
        egress=_egress(plan),
        teardown=teardown,
        final=_final(plan, teardown),
    )


def _workspace(plan: FleetPlanResult) -> FleetPlanWorkspaceTrustReceipt | None:
    input_evidence = plan.execution_inputs
    if input_evidence is None:
        return None
    return FleetPlanWorkspaceTrustReceipt(
        base_workspace_sha256=input_evidence.base_workspace.sha256,
        runtime_root_sha256=input_evidence.runtime_root.sha256,
        managed_input_count=len(input_evidence.managed_inputs),
        content_input_count=len(input_evidence.content_inputs),
        attempts=tuple(
            FleetPlanCacheAttemptTrustReceipt(
                step_id=step.step_id,
                attempt=attempt.attempt,
                cache_input_sha256=(attempt.cache_input.sha256 if attempt.cache_input else None),
                cache_output_sha256=(attempt.cache_output.sha256 if attempt.cache_output else None),
                retained_reference_id=(
                    attempt.retained_cache.reference_id if attempt.retained_cache else None
                ),
                retained_owner_repository_id=(
                    attempt.retained_cache.owner_repository_id if attempt.retained_cache else None
                ),
                retained_sha256=(attempt.retained_cache.sha256 if attempt.retained_cache else None),
            )
            for step in plan.steps
            for attempt in step.attempts
        ),
    )


def _boundaries(plan: FleetPlanResult) -> tuple[FleetPlanBoundaryTrustReceipt, ...]:
    return tuple(
        FleetPlanBoundaryTrustReceipt(
            step_id=step.step_id,
            attempt=attempt.attempt,
            egress_mode=attempt.egress.mode,
            egress_policy_id=attempt.egress.policy_id,
            resource_policy_id=attempt.resources.policy_id if attempt.resources else None,
            repository_state_before=attempt.repository_state_before,
            repository_state_after=attempt.repository_state_after,
        )
        for step in plan.steps
        for attempt in step.attempts
    )


def _egress(plan: FleetPlanResult) -> FleetPlanEgressTrustReceipt:
    return FleetPlanEgressTrustReceipt(
        artifacts=tuple(
            FleetPlanArtifactTrustReceipt(
                step_id=step.step_id,
                path=artifact.path,
                present=isinstance(artifact, PresentPlanArtifact),
                byte_count=artifact.byte_count
                if isinstance(artifact, PresentPlanArtifact)
                else None,
                sha256=artifact.sha256 if isinstance(artifact, PresentPlanArtifact) else None,
            )
            for step in plan.steps
            for artifact in step.artifacts
        ),
        outputs=tuple(
            FleetPlanOutputTrustReceipt(
                step_id=step.step_id,
                name=output.name,
                byte_count=output.byte_count,
                sha256=output.sha256,
            )
            for step in plan.steps
            for output in step.outputs
        ),
    )


def _final(
    plan: FleetPlanResult, teardown: FleetPlanTeardownTrustReceipt
) -> FleetPlanFinalTrustReceipt:
    failed = sum(isinstance(step, FailedPlanStepExecution) for step in plan.steps)
    completed = sum(isinstance(step, CompletedPlanStepExecution) for step in plan.steps)
    skipped = sum(isinstance(step, SkippedPlanStepExecution) for step in plan.steps)
    state = (
        "nonStrict"
        if plan.execution_inputs is None
        else "failed"
        if failed > 0 or teardown.failed > 0
        else "evidenceGap"
        if teardown.missing > 0
        else "cleanPass"
    )
    return FleetPlanFinalTrustReceipt(
        state=state,
        completed_steps=completed,
        failed_steps=failed,
        skipped_steps=skipped,
        teardown_verified=teardown.completed,
        teardown_failed=teardown.failed,
        teardown_missing=teardown.missing,
    )


def _admission(plan: FleetPlanResult) -> FleetPlanAdmissionTrustReceipt | None:
    input_evidence = plan.execution_inputs
    admission = input_evidence.repository_admission if input_evidence is not None else None
    if admission is None:
        return None
    return FleetPlanAdmissionTrustReceipt(
        validation_id=admission.validation_id,
        requirements_id=admission.requirements_id,
        requirement_count=admission.requirement_count,
    )


def _runtime(plan: FleetPlanResult) -> FleetPlanRuntimeTrustReceipt | None:
    input_evidence = plan.execution_inputs
    if input_evidence is None:
        return None
    backend = input_evidence.isolation_backend
    return FleetPlanRuntimeTrustReceipt(
        backend_id=backend.backend_id,
        host_toolchain_id=input_evidence.host_toolchain_id,
        configuration_id=backend.configuration_id,
        kernel_boundary=backend.kernel_boundary,
        remote_execution=backend.remote_execution,
        experimental=backend.experimental,
        host_environment_names=input_evidence.host_environment_names,
        host_tools=tuple(
            FleetPlanRuntimeToolTrustReceipt(
                name=tool.name,
                origin=tool.origin,
                sha256=tool.sha256,
                byte_count=tool.byte_count,
                authority_id=tool.authority_id,
            )
            for tool in input_evidence.host_tools
        ),
        hostname_resolution_count=len(input_evidence.hostname_resolutions),
    )


def _release(plan: FleetPlanResult) -> FleetPlanReleaseTrustReceipt:
    steps = tuple(
        _release_step(step)
        for step in plan.steps
        if step.kind in {"oci_publish", "kubernetes_set_image"}
    )
    released = sum(step.gate_state == "RELEASED" for step in steps)
    blocked = sum(step.gate_state == "BLOCKED" for step in steps)
    evidence_gaps = sum(step.gate_state == "EVIDENCE_GAP" for step in steps)
    external_effects = sum(
        step.release is not None
        and (
            step.release.kind == "OCI_PUBLISH"
            or (step.release.kind == "KUBERNETES_SET_IMAGE" and step.release.outcome == "COMMITTED")
        )
        for step in steps
    )
    state = (
        "noRelease"
        if not steps
        else "evidenceGap"
        if evidence_gaps > 0
        else "partialRelease"
        if released > 0 and blocked > 0
        else "released"
        if released > 0
        else "blocked"
    )
    return FleetPlanReleaseTrustReceipt(
        state=state,
        released=released,
        blocked=blocked,
        evidence_gaps=evidence_gaps,
        external_effects=external_effects,
        readiness=_readiness(plan),
        steps=steps,
    )


def _release_step(step) -> FleetPlanReleaseStepTrustReceipt:
    effects = tuple(
        attempt.release_effect for attempt in step.attempts if attempt.release_effect is not None
    )
    effect = effects[0] if effects else None
    gate_state = (
        "RELEASED"
        if effect is not None
        else "EVIDENCE_GAP"
        if isinstance(step, CompletedPlanStepExecution)
        else "BLOCKED"
    )
    release = None
    if isinstance(effect, OciPublishReleaseEvidence):
        release = FleetPlanOciReleaseTrustReceipt(
            release_id=effect.release_id,
            target=effect.published_reference,
            manifest_digest=effect.manifest_digest,
        )
    elif isinstance(effect, KubernetesSetImageReleaseEvidence):
        release = FleetPlanKubernetesReleaseTrustReceipt(
            release_id=effect.release_id,
            outcome=effect.outcome,
            target=_kubernetes_target(effect),
            desired_image=effect.desired_image,
            before_resource_version=effect.before_resource_version,
            before_generation=effect.before_generation,
            after_resource_version=effect.after_resource_version,
            after_generation=effect.after_generation,
        )
    return FleetPlanReleaseStepTrustReceipt(
        step_id=step.step_id,
        step_kind=step.kind,
        execution_status=step.status,
        gate_state=gate_state,
        release=release,
    )


def _readiness(plan: FleetPlanResult) -> FleetPlanReadinessTrustReceipt:
    steps = tuple(
        _readiness_step(step) for step in plan.steps if step.kind == "kubernetes_rollout_verify"
    )
    ready = sum(step.state == "ready" for step in steps)
    failed = sum(step.state == "failed" for step in steps)
    blocked = sum(step.state == "blocked" for step in steps)
    evidence_gaps = sum(step.state == "evidenceGap" for step in steps)
    state = (
        "notRequested"
        if not steps
        else "evidenceGap"
        if evidence_gaps > 0
        else "partial"
        if ready > 0 and (failed > 0 or blocked > 0)
        else "failed"
        if failed > 0
        else "blocked"
        if blocked > 0
        else "ready"
    )
    return FleetPlanReadinessTrustReceipt(
        state=state,
        requested=len(steps),
        ready=ready,
        failed=failed,
        blocked=blocked,
        evidence_gaps=evidence_gaps,
        steps=steps,
    )


def _readiness_step(step) -> FleetPlanReadinessStepTrustReceipt:
    verifications = tuple(
        attempt.release_verification
        for attempt in step.attempts
        if attempt.release_verification is not None
    )
    verification = verifications[-1] if verifications else None
    state = (
        "ready"
        if verification is not None
        and verification.outcome is KubernetesRolloutVerificationOutcome.READY
        else "failed"
        if verification is not None
        else "blocked"
        if isinstance(step, SkippedPlanStepExecution)
        else "evidenceGap"
    )
    projected = None
    if verification is not None:
        projected = FleetPlanRolloutVerificationTrustReceipt(
            verification_id=verification.verification_id,
            outcome=verification.outcome,
            target=_kubernetes_target(verification),
            expected_generation=verification.expected_generation,
            expected_image=verification.expected_image,
            observations=verification.observations,
            final_resource_version=verification.final_resource_version,
            final_generation=verification.final_generation,
            final_image=verification.final_image,
            observed_generation=verification.observed_generation,
            desired_replicas=verification.desired_replicas,
            replicas=verification.replicas,
            updated_replicas=verification.updated_replicas,
            available_replicas=verification.available_replicas,
            unavailable_replicas=verification.unavailable_replicas,
            progressing_status=verification.progressing_status,
            progressing_reason=verification.progressing_reason,
            timeout_seconds=verification.timeout_seconds,
            poll_interval_seconds=verification.poll_interval_seconds,
            request_timeout_seconds=verification.request_timeout_seconds,
        )
    return FleetPlanReadinessStepTrustReceipt(
        step_id=step.step_id,
        execution_status=step.status,
        state=state,
        verification=projected,
    )


def _teardown(plan: FleetPlanResult) -> FleetPlanTeardownTrustReceipt:
    steps = tuple(_teardown_step(step) for step in plan.steps)
    return FleetPlanTeardownTrustReceipt(
        steps=steps,
        completed=sum(step.status == "COMPLETED" for step in steps),
        failed=sum(step.status == "FAILED" for step in steps),
        missing=sum(step.status == "NOT_RECORDED" for step in steps),
    )


def _teardown_step(step) -> FleetPlanTeardownStepTrustReceipt:
    teardown = step.teardown
    if teardown is None:
        return FleetPlanTeardownStepTrustReceipt(
            step_id=step.step_id,
            status="NOT_RECORDED",
        )
    return FleetPlanTeardownStepTrustReceipt(
        step_id=step.step_id,
        status=teardown.status,
        attempt_roots_observed=teardown.attempt_roots_observed,
        reason_code=getattr(teardown, "reason_code", None),
    )


def _kubernetes_target(evidence) -> str:
    return (
        f"{evidence.api_authority}/{evidence.namespace}/{evidence.deployment}:{evidence.container}"
    )
