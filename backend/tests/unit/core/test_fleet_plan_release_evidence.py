from __future__ import annotations

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
    PlanStepSettlementEvidence,
)
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesSetImageReleaseEvidence,
    OciPublishReleaseEvidence,
)


def _output() -> PlanOutputEvidence:
    return PlanOutputEvidence(sha256="a" * 64, byte_count=0, line_count=0)


def _oci_release() -> OciPublishReleaseEvidence:
    digest = "sha256:" + "b" * 64
    return OciPublishReleaseEvidence(
        registry_authority="registry.example.com",
        repository="team/app",
        manifest_digest=digest,
        published_reference=f"registry.example.com/team/app@{digest}",
    )


def _attempt(*, release_effect=None, returncode: int = 0) -> PlanStepAttemptEvidence:
    return PlanStepAttemptEvidence(
        attempt=1,
        returncode=returncode,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        egress=PlanEgressEvidence(mode="none"),
        release_effect=release_effect,
    )


def _completed(kind: str, *, release_effect=None) -> CompletedPlanStepExecution:
    return CompletedPlanStepExecution(
        step_id="release",
        kind=kind,
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        attempts=(_attempt(release_effect=release_effect),),
    )


def test_completed_release_step_requires_canonical_release_fact() -> None:
    with pytest.raises(ValidationError, match="requires canonical release evidence"):
        FleetPlanResult(plan_id="plan-1", steps=(_completed("oci_publish"),))


def test_non_release_step_cannot_claim_external_release() -> None:
    with pytest.raises(ValidationError, match="cannot carry release-effect evidence"):
        FleetPlanResult(
            plan_id="plan-1",
            steps=(_completed("command", release_effect=_oci_release()),),
        )


def test_failed_release_step_preserves_known_external_effect() -> None:
    step = FailedPlanStepExecution(
        step_id="publish",
        kind="oci_publish",
        returncode=73,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="before",
        repository_state_after="after",
        attempts=(_attempt(release_effect=_oci_release(), returncode=73),),
    )

    result = FleetPlanResult(plan_id="plan-1", steps=(step,))

    assert result.successful is False
    assert result.steps[0].attempts[0].release_effect is not None
    assert result.steps[0].attempts[0].release_effect.kind == "OCI_PUBLISH"


def test_kubernetes_release_transition_is_structurally_validated() -> None:
    common = dict(
        api_authority="kubernetes.example.com",
        namespace="production",
        deployment="api",
        container="api",
        desired_image="registry.example.com/team/app@sha256:" + "c" * 64,
        before_resource_version="10",
        before_generation=4,
        after_image="registry.example.com/team/app@sha256:" + "c" * 64,
    )
    with pytest.raises(ValidationError, match="cannot claim a Deployment mutation"):
        KubernetesSetImageReleaseEvidence(
            outcome="ALREADY_DESIRED",
            after_resource_version="11",
            after_generation=5,
            **common,
        )
    with pytest.raises(ValidationError, match="advanced Deployment generation"):
        KubernetesSetImageReleaseEvidence(
            outcome="COMMITTED",
            after_resource_version="11",
            after_generation=4,
            **common,
        )


def test_release_fact_survives_canonical_result_json_round_trip() -> None:
    result = FleetPlanResult(
        plan_id="plan-1",
        steps=(_completed("oci_publish", release_effect=_oci_release()),),
    )

    restored = FleetPlanResult.model_validate_json(
        result.model_dump_json(exclude_computed_fields=True)
    )

    effect = restored.steps[0].attempts[0].release_effect
    assert effect is not None
    assert effect.kind == "OCI_PUBLISH"
    assert effect.release_id == _oci_release().release_id
