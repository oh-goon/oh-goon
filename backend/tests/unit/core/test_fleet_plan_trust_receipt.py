from __future__ import annotations

from pathlib import Path

from oh_goon.core.fleet_plan_trust_receipt import (
    project_fleet_plan_trust_receipt,
    project_repository_fleet_plan_trust_receipts,
)
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    CompletedPlanStepTeardownEvidence,
    FailedPlanStepTeardownEvidence,
    FleetPlanResult,
    PlanEgressEvidence,
    PlanExecutionInputEvidence,
    PlanHostnameResolutionEvidence,
    PlanIsolationBackendEvidence,
    PlanOutputEvidence,
    PlanSnapshotEvidence,
    PlanStepAttemptEvidence,
    RequirementsRepositoryAdmissionEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.fleet_plan_release_evidence import (
    KubernetesRolloutVerificationEvidence,
    KubernetesSetImageReleaseEvidence,
)
from oh_goon.core.models.goon_plan_source import PlanFileSource
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import OperationAction
from tests.factories.fleet_plan_targets import structural_execution_identity
from tests.factories.operation_reports import make_operation_report


def _output() -> PlanOutputEvidence:
    return PlanOutputEvidence(sha256="0" * 64, byte_count=0, line_count=0)


def _snapshot(char: str) -> PlanSnapshotEvidence:
    return PlanSnapshotEvidence(sha256=char * 64, byte_count=1, entry_count=1)


def _attempt(*, release_effect=None, release_verification=None) -> PlanStepAttemptEvidence:
    return PlanStepAttemptEvidence(
        attempt=1,
        returncode=0,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="repo-before",
        repository_state_after="repo-after",
        egress=PlanEgressEvidence(mode="none"),
        release_effect=release_effect,
        release_verification=release_verification,
    )


def _inputs() -> PlanExecutionInputEvidence:
    return PlanExecutionInputEvidence(
        source_authority_id="repository-source-authority-" + "0" * 64,
        repository_id="repo-" + "1" * 20,
        base_workspace=_snapshot("a"),
        repository_admission=RequirementsRepositoryAdmissionEvidence(
            requirements_id="requirements-current",
            requirement_count=3,
        ),
        runtime_root=_snapshot("b"),
        isolation_backend=PlanIsolationBackendEvidence(
            backend_id="bubblewrap",
            configuration_id="cfg-current",
            kernel_boundary="shared_host_kernel",
            remote_execution=False,
            experimental=False,
        ),
        hostname_resolutions=(
            PlanHostnameResolutionEvidence(
                hostname="registry.example.com",
                addresses=("192.0.2.10",),
            ),
        ),
    )


def _completed(step_id: str, kind: str = "run", *, attempt=None, teardown=None):
    return CompletedPlanStepExecution(
        step_id=step_id,
        kind=kind,
        returncode=0,
        stdout_evidence=_output(),
        stderr_evidence=_output(),
        repository_state_before="repo-before",
        repository_state_after="repo-after",
        attempts=((attempt or _attempt()),),
        teardown=teardown or CompletedPlanStepTeardownEvidence(attempt_roots_observed=1),
    )


def _target() -> FleetPlanTarget:
    resolved = ResolvedFleetPlan.create(
        name="trust-receipt-test",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(ResolvedCommandStep(step_id="test", argv=("true",), timeout_seconds=30),),
    )
    return FleetPlanTarget(
        kind="fleet_plan",
        plan=resolved,
        source=PlanFileSource(sha256="0" * 64),
        identity=structural_execution_identity(resolved),
    )


def test_projects_current_requirements_admission_without_sensitive_runtime_payloads() -> None:
    plan = FleetPlanResult(
        plan_id="plan-current",
        steps=(_completed("test"),),
        execution_inputs=_inputs(),
    )

    receipt = project_fleet_plan_trust_receipt(plan)

    assert receipt.admission is not None
    assert receipt.admission.requirements_id == "requirements-current"
    assert receipt.admission.requirement_count == 3
    assert receipt.admission.validation_id.startswith("cicd-repository-admission-")
    assert receipt.final.state == "cleanPass"
    rendered = receipt.model_dump_json()
    assert "192.0.2.10" not in rendered
    assert "argv" not in rendered
    assert "stdout_evidence" not in rendered
    assert "stderr_evidence" not in rendered


def test_zero_requirement_admission_remains_serializable() -> None:
    inputs = _inputs().model_copy(
        update={
            "repository_admission": RequirementsRepositoryAdmissionEvidence(
                requirements_id="requirements-empty",
                requirement_count=0,
            )
        }
    )
    plan = FleetPlanResult(
        plan_id="plan-empty-admission",
        steps=(_completed("test"),),
        execution_inputs=inputs,
    )

    receipt = project_fleet_plan_trust_receipt(plan)

    assert receipt.admission is not None
    assert receipt.admission.requirement_count == 0
    rendered = receipt.model_dump_json()
    assert '"requirement_count":0' in rendered


def test_release_and_readiness_are_interpreted_once_from_canonical_evidence() -> None:
    release = KubernetesSetImageReleaseEvidence(
        outcome="COMMITTED",
        api_authority="kubernetes.example.com",
        namespace="prod",
        deployment="api",
        container="api",
        desired_image="registry.example.com/team/app@sha256:" + "a" * 64,
        before_resource_version="10",
        before_generation=4,
        after_resource_version="11",
        after_generation=5,
        after_image="registry.example.com/team/app@sha256:" + "a" * 64,
        tls_verified=True,
        resource_version_tested=True,
        current_image_tested=True,
        rollout_waited=False,
    )
    verification = KubernetesRolloutVerificationEvidence(
        outcome="READY",
        api_authority="kubernetes.example.com",
        namespace="prod",
        deployment="api",
        container="api",
        expected_generation=5,
        expected_image="registry.example.com/team/app@sha256:" + "a" * 64,
        observations=2,
        final_resource_version="12",
        final_generation=5,
        final_image="registry.example.com/team/app@sha256:" + "a" * 64,
        observed_generation=5,
        desired_replicas=2,
        replicas=2,
        updated_replicas=2,
        available_replicas=2,
        unavailable_replicas=0,
        timeout_seconds=300,
        poll_interval_seconds=5,
        request_timeout_seconds=10,
        tls_verified=True,
    )
    plan = FleetPlanResult(
        plan_id="plan-release",
        steps=(
            _completed("release", "kubernetes_set_image", attempt=_attempt(release_effect=release)),
            _completed(
                "verify",
                "kubernetes_rollout_verify",
                attempt=_attempt(release_verification=verification),
            ),
        ),
        execution_inputs=_inputs(),
    )

    receipt = project_fleet_plan_trust_receipt(plan)

    assert receipt.release.state == "released"
    assert receipt.release.external_effects == 1
    assert receipt.release.readiness.state == "ready"
    assert receipt.release.readiness.ready == 1
    assert receipt.release.steps[0].release is not None
    assert receipt.release.steps[0].release.kind == "KUBERNETES_SET_IMAGE"


def test_teardown_failure_prevents_clean_pass() -> None:
    plan = FleetPlanResult(
        plan_id="plan-teardown",
        steps=(
            _completed(
                "test",
                teardown=FailedPlanStepTeardownEvidence(
                    attempt_roots_observed=1,
                    step_attempts_root_absent=False,
                    reason_code="ROOT_REMAINS",
                ),
            ),
        ),
        execution_inputs=_inputs(),
    )

    receipt = project_fleet_plan_trust_receipt(plan)

    assert receipt.teardown.failed == 1
    assert receipt.final.state == "failed"


def test_repository_receipts_are_derived_from_operation_reports_not_persisted_into_them() -> None:
    plan = FleetPlanResult(
        plan_id="plan-report",
        steps=(_completed("test"),),
        execution_inputs=_inputs(),
    )
    report = make_operation_report(
        Path("/repo/project"),
        OperationAction.EXECUTE_PLAN,
        target=_target(),
        evidence=FleetPlanOperationEvidence(kind="FLEET_PLAN", plan=plan),
    )

    receipts = project_repository_fleet_plan_trust_receipts((report,))

    assert len(receipts) == 1
    assert receipts[0].repository_id == report.repository.repository_id
    assert receipts[0].receipt.plan_id == "plan-report"
    assert "trust" not in report.model_dump(mode="json")
