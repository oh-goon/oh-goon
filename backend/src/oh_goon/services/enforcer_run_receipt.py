from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import step_execution_tool
from oh_goon.core.models.enforcer_receipt import (
    EnforcerAttemptFact,
    EnforcerEgressFact,
    EnforcerExecutionInputFact,
    EnforcerFleetExecutionFact,
    EnforcerHostnameResolutionFact,
    EnforcerImportFact,
    EnforcerOperationFact,
    EnforcerPlacementIds,
    EnforcerRepositoryAdmissionFact,
    EnforcerRepositoryFact,
    EnforcerRequirementFact,
    EnforcerRequirementsAdmissionFact,
    EnforcerResolvedPlanFact,
    EnforcerResolvedStepFact,
    EnforcerRunLifecycleFact,
    EnforcerRunReceipt,
    EnforcerStepResultFact,
    EnforcerToolFact,
)
from oh_goon.core.models.fleet_plan import ResolvedLocalSearchAgentStep
from oh_goon.core.models.fleet_plan_evidence import (
    MissingPlanArtifact,
    RequirementsRepositoryAdmissionEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_step_primitives import StepRetryPolicy
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import StrictFleetExecutionAdmission
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import stable_id
from oh_goon.integrations.fleet_plan_containerfile_policy import oci_build_repository_policy_id


def enforcer_run_receipt(detail: FleetRunDetail) -> EnforcerRunReceipt:
    """Project one strict Fleet run into a deterministic, non-secret debugging receipt."""

    target = detail.run.target
    if not isinstance(target, FleetPlanTarget) or target.plan.execution.purpose != "cicd":
        raise FleetPlanInvalid(
            "Enforcer run receipts are available only for strict CI/CD Fleet runs"
        )
    execution = detail.execution
    if not isinstance(execution, StrictFleetExecutionAdmission):
        raise FleetPlanInvalid("Strict CI/CD run detail is missing strict executor admission")

    plan = target.plan
    assert isinstance(plan.execution, CicdExecutionPolicy)
    capacity = execution.placement.executor_capacity
    reports = {report.repository.repository_id: report for report in detail.operations}
    return EnforcerRunReceipt(
        fleet_run_id=detail.run.fleet_run_id,
        lifecycle=EnforcerRunLifecycleFact(
            status=detail.run.status,
            accepted_at=detail.run.accepted_at,
            started_at=getattr(detail.run, "started_at", None),
            cancel_requested_at=getattr(detail.run, "cancel_requested_at", None),
            finished_at=getattr(detail.run, "finished_at", None),
        ),
        outcome=detail.outcome,
        plan=EnforcerResolvedPlanFact(
            plan_id=plan.plan_id,
            contract_version=plan.contract_version,
            name=plan.name,
            execution=plan.execution,
            requirements=tuple(_requirement(item) for item in plan.requirements),
            imports=tuple(_import_fact(item) for item in plan.imports),
            steps=tuple(_resolved_step(step) for step in plan.steps),
        ),
        execution_reference=target.execution_reference,
        fleet_execution=EnforcerFleetExecutionFact(
            placement=execution.placement,
            placement_ids=EnforcerPlacementIds(
                inventory_id=capacity.inventory_id,
                health_attestation_id=capacity.health_attestation_id,
                placement_id=capacity.placement_id,
                reservation_id=capacity.reservation_id,
            ),
            readmitted_from_run_id=execution.readmitted_from_run_id,
            rerun_from_run_id=execution.rerun_from_run_id,
        ),
        repositories=tuple(
            _repository(
                target_repository.repository_id,
                target_repository.name,
                reports.get(target_repository.repository_id),
            )
            for target_repository in detail.run.targets
        ),
    )


def render_enforcer_yaml(receipt: EnforcerRunReceipt) -> str:
    payload = receipt.model_dump(mode="json", exclude_none=True, exclude_computed_fields=True)
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False, default_flow_style=False)


def _resolved_step(step: Any) -> EnforcerResolvedStepFact:
    required_tool = step_execution_tool(step)
    if isinstance(step, ResolvedLocalSearchAgentStep) and step.native_agent is not None:
        # Receipts retain a display label, never a native host path or a second
        # lookup authority. Attempt tool evidence independently names its launcher.
        required_tool = Path(required_tool).name if required_tool else step.native_agent.agent
    return EnforcerResolvedStepFact(
        step_id=step.step_id,
        kind=step.kind,
        required_tool=required_tool,
        timeout_seconds=getattr(step, "timeout_seconds", None),
        workspace=step.workspace,
        cache=step.cache,
        retry=getattr(step, "retry", None) or StepRetryPolicy(),
        egress_mode=step.capabilities.egress.mode,
        repository_policy_id=(
            oci_build_repository_policy_id(step) if step.kind == "oci_build" else None
        ),
    )


def _repository(repository_id: str, name: str, report: Any | None) -> EnforcerRepositoryFact:
    if report is None or not isinstance(report.evidence, FleetPlanOperationEvidence):
        return EnforcerRepositoryFact(repository_id=repository_id, name=name)
    result = report.evidence.plan
    inputs = result.execution_inputs
    if inputs is None:
        return EnforcerRepositoryFact(
            repository_id=repository_id,
            name=name,
            operation=EnforcerOperationFact(
                operation_id=report.provenance.operation_id, outcome=report.outcome
            ),
            steps=tuple(_step_result(step) for step in result.steps),
        )
    admission = inputs.repository_admission
    return EnforcerRepositoryFact(
        repository_id=repository_id,
        name=name,
        operation=EnforcerOperationFact(
            operation_id=report.provenance.operation_id, outcome=report.outcome
        ),
        execution_input=EnforcerExecutionInputFact(
            input_id=inputs.input_id,
            admission=_admission_fact(admission),
            base_workspace=inputs.base_workspace,
            runtime_root=inputs.runtime_root,
            host_environment_names=inputs.host_environment_names,
            hostname_resolutions=tuple(
                _hostname_resolution(item.hostname, item.addresses)
                for item in inputs.hostname_resolutions
            ),
            host_toolchain_id=inputs.host_toolchain_id,
            host_tools=tuple(_tool(item) for item in inputs.host_tools),
            managed_inputs=inputs.managed_inputs,
            content_inputs=inputs.content_inputs,
            import_applicability=inputs.import_applicability,
        ),
        steps=tuple(_step_result(step) for step in result.steps),
    )


def _step_result(step: Any) -> EnforcerStepResultFact:
    artifacts = tuple(
        artifact.sha256
        for artifact in step.artifacts
        if not isinstance(artifact, MissingPlanArtifact)
    )
    return EnforcerStepResultFact(
        step_id=step.step_id,
        kind=step.kind,
        status=step.status,
        reason=getattr(step, "reason", None),
        returncode=getattr(step, "returncode", None),
        duration_ms=step.duration_ms,
        repository_state_before=step.repository_state_before,
        repository_state_after=step.repository_state_after,
        attempts=tuple(_attempt(item) for item in step.attempts),
        teardown=step.teardown,
        artifact_digests=artifacts,
        outputs=step.outputs,
    )


def _attempt(item: Any) -> EnforcerAttemptFact:
    return EnforcerAttemptFact(
        attempt=item.attempt,
        returncode=item.returncode,
        duration_ms=item.duration_ms,
        tool=_tool(item.tool) if item.tool is not None else None,
        cache_input=item.cache_input,
        cache_output=item.cache_output,
        retained_cache_reference_id=(
            item.retained_cache.reference_id if item.retained_cache is not None else None
        ),
        egress=EnforcerEgressFact(mode=item.egress.mode, policy_id=item.egress.policy_id),
        resource_policy_id=(item.resources.policy_id if item.resources is not None else None),
        retry=item.retry,
    )


def _tool(item: Any) -> EnforcerToolFact:
    return EnforcerToolFact(
        name=item.name,
        sha256=item.sha256,
        byte_count=item.byte_count,
        authority_id=getattr(item, "authority_id", "")
        or _tool_identity(item.name, item.sha256, item.byte_count),
    )


def _tool_identity(name: str, sha256: str, byte_count: int) -> str:
    return stable_id(
        "plan-host-tool-authority",
        {"name": name, "sha256": sha256, "byte_count": byte_count},
    )


def _admission_fact(admission: Any | None) -> EnforcerRepositoryAdmissionFact | None:
    if admission is None:
        return None
    if isinstance(admission, RequirementsRepositoryAdmissionEvidence):
        return EnforcerRequirementsAdmissionFact(
            validation_id=admission.validation_id,
            requirements_id=admission.requirements_id,
            requirement_count=admission.requirement_count,
        )
    raise AssertionError(f"Unhandled CI/CD admission evidence: {type(admission).__name__}")


def _import_fact(item: Any) -> EnforcerImportFact:
    return EnforcerImportFact(
        import_id=item.import_id,
        parent_import_id=item.parent_import_id,
        kind=item.kind,
        name=item.name,
        source_sha256=item.source_sha256,
        step_ids=item.step_ids,
        requirements=tuple(_requirement(requirement) for requirement in item.requirements),
    )


def _requirement(item: Any) -> EnforcerRequirementFact:
    kind = item.kind
    if kind in {"file_exists", "folder_exists", "path_exists"}:
        detail = item.path
    elif kind == "uv_project":
        detail = item.group
    elif kind == "pyproject_value":
        detail = item.field
    else:  # pragma: no cover - closed requirement union
        detail = kind
    return EnforcerRequirementFact(kind=kind, detail=detail)


def _hostname_resolution(
    hostname: str, addresses: tuple[str, ...]
) -> EnforcerHostnameResolutionFact:
    # Debuggable drift signal without serializing internal resolved addresses.
    raw = (hostname + "\0" + "\0".join(addresses)).encode("utf-8")
    return EnforcerHostnameResolutionFact(
        hostname=hostname,
        sha256=hashlib.sha256(raw).hexdigest(),
    )
