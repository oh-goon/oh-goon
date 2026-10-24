from __future__ import annotations

from pathlib import Path
from typing import TypedDict

from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.fleet_plan_output_refs import step_output_reference
from oh_goon.core.models.fleet_plan import (
    MissingConfigPolicy,
    ResolvedFleetPlan,
    ResolvedLocalSearchAgentStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
    StepStatusCondition,
    step_runtime_strings,
)
from oh_goon.core.models.fleet_plan_evidence import (
    FleetPlanPreview,
    PlanImportApplicabilityEvidence,
    PlanImportPreview,
    PlanPreviewDisposition,
    PlanSnapshotEvidence,
    PlanStepPreview,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.path_authority import navigation_path
from oh_goon.core.product_identity import repository_subject_instance_id, stable_id
from oh_goon.integrations._fleet_plan_step_dataflow import (
    condition_skip_reason,
    validate_step_declared_paths,
)
from oh_goon.integrations._fleet_plan_step_local_execution import step_working_directory
from oh_goon.integrations.executable_materialization_authority import (
    logical_step_execution_authority,
)
from oh_goon.integrations.fleet_plan_environment import require_environment
from oh_goon.integrations.fleet_plan_repository_scope import (
    RepositoryScopeBinding,
    bind_repository_scope,
)
from oh_goon.integrations.source_snapshot import source_snapshot_identity


def preview_plan(repository: Path, plan: ResolvedFleetPlan) -> FleetPlanPreview:
    """Inspect current repository-resolvable plan decisions without running effects."""
    repository = navigation_path(repository)
    return _preview_plan_from_scope(repository, plan, bind_repository_scope(repository, plan))


def _preview_plan_from_scope(
    repository: Path, plan: ResolvedFleetPlan, repository_scope: RepositoryScopeBinding
) -> FleetPlanPreview:
    applicability = repository_scope.import_applicability
    imported_skips = repository_scope.import_skips
    return FleetPlanPreview(
        plan_id=plan.plan_id,
        repository=repository,
        plan_name=plan.name,
        execution_purpose=str(plan.execution.purpose),
        repository_execution_mode=str(plan.execution.repositories.mode),
        repository_max_parallel=getattr(plan.execution.repositories, "max_parallel", None),
        imports=_import_previews(plan, applicability),
        steps=tuple(
            _preview_step(repository, step, imported_skips.get(step.step_id)) for step in plan.steps
        ),
    )


def repository_plan_admission_evidence(
    repository: RepositoryIdentity,
    plan: ResolvedFleetPlan,
) -> PlanRepositoryAdmissionEvidence:
    """Observe one repository/Plan semantic admission fact without effects."""

    if isinstance(plan.execution, CicdExecutionPolicy) and any(
        isinstance(step, ResolvedLocalSearchAgentStep) and step.native_agent is not None
        for step in plan.steps
    ):
        raise FleetPlanInvalid(
            "Native Agent requires ordinary execution; strict CI/CD is unsupported"
        )
    scope = bind_repository_scope(repository.path, plan)
    preview = _preview_plan_from_scope(repository.path, plan, scope)
    if isinstance(plan.execution, CicdExecutionPolicy):
        for step in plan.steps:
            if step.step_id in scope.import_skips:
                continue
            authority = logical_step_execution_authority(repository.path, step)
            if authority.materialized_executables:
                raise FleetPlanInvalid(
                    "Strict CI/CD source-only execution cannot depend on live package-manager "
                    "materialization paths; use the native uv/npm execution model instead: "
                    + ", ".join(authority.materialized_executables)
                )
    payload = preview.model_dump(
        mode="json",
        exclude={"repository"},
        exclude_computed_fields=True,
    )
    source = source_snapshot_identity(repository.path)
    source_authority = RepositorySourceAuthority.create(
        subject_instance_id=repository_subject_instance_id(repository.path),
        source_snapshot=PlanSnapshotEvidence(
            sha256=source.sha256,
            byte_count=source.byte_count,
            entry_count=source.entry_count,
        ),
    )
    return PlanRepositoryAdmissionEvidence.create(
        repository_id=repository.repository_id,
        plan_id=plan.plan_id,
        semantic_preview_id=stable_id("fleet-plan-repository-preview", payload),
        import_applicability=scope.import_applicability,
        active_step_ids=scope.active_step_ids,
        source_authority=source_authority,
    )


def require_repository_plan_admission_current(
    repository: RepositoryIdentity,
    plan: ResolvedFleetPlan,
    admitted: PlanRepositoryAdmissionEvidence,
) -> None:
    """Revalidate admitted semantic facts without becoming a second resolver."""

    try:
        current = repository_plan_admission_evidence(repository, plan)
    except (FleetPlanInvalid, ValueError) as exc:
        raise StateConflict(
            "Fleet repository/Plan admission became stale after RUN_ADMITTED: " + str(exc)
        ) from exc
    if current != admitted:
        raise StateConflict("Fleet repository/Plan admission became stale after RUN_ADMITTED")


def _preview_step(
    repository: Path, step: ResolvedPlanStep, imported_reason: str | None
) -> PlanStepPreview:
    policy = _step_policy_preview(step)
    if imported_reason is not None:
        return PlanStepPreview(
            step_id=step.step_id,
            kind=step.kind,
            disposition=PlanPreviewDisposition.SKIPPED,
            reason=imported_reason,
            **policy,
        )
    require_environment(step)
    step_working_directory(repository, step)
    validate_step_declared_paths(repository, step)
    if (
        isinstance(step, ResolvedPreCommitStep)
        and step.if_missing is MissingConfigPolicy.FAIL
        and not (repository / ".pre-commit-config.yaml").is_file()
    ):
        raise FleetPlanInvalid(
            f"Repository has no .pre-commit-config.yaml required by step {step.step_id}"
        )
    condition = getattr(step, "when", None)
    if isinstance(condition, StepStatusCondition):
        return PlanStepPreview(
            step_id=step.step_id,
            kind=step.kind,
            disposition=PlanPreviewDisposition.RUNTIME_DEPENDENT,
            reason=f"Depends on runtime status of earlier step {condition.step_id}",
            **policy,
        )
    reason = condition_skip_reason(repository, step, ())
    if reason:
        return PlanStepPreview(
            step_id=step.step_id,
            kind=step.kind,
            disposition=PlanPreviewDisposition.SKIPPED,
            reason=reason,
            **policy,
        )
    if any(step_output_reference(value) for value in step_runtime_strings(step)):
        return PlanStepPreview(
            step_id=step.step_id,
            kind=step.kind,
            disposition=PlanPreviewDisposition.RUNTIME_DEPENDENT,
            reason="Depends on runtime output from an earlier step",
            **policy,
        )
    return PlanStepPreview(
        step_id=step.step_id,
        kind=step.kind,
        disposition=PlanPreviewDisposition.READY,
        reason="",
        **policy,
    )


class _PolicyPreview(TypedDict):
    timeout_seconds: float | None
    workspace_source: str
    workspace_publish: bool
    cache_source: str
    cache_publish: bool
    cache_retain: bool
    egress_mode: str


def _step_policy_preview(step: ResolvedPlanStep) -> _PolicyPreview:
    workspace = getattr(step, "workspace", None)
    cache = getattr(step, "cache", None)
    capabilities = getattr(step, "capabilities", None)
    workspace_source = getattr(workspace, "source", None)
    cache_source = getattr(cache, "source", None)
    return {
        "timeout_seconds": getattr(step, "timeout_seconds", None),
        "workspace_source": _source_label(workspace_source),
        "workspace_publish": bool(getattr(workspace, "publish", False)),
        "cache_source": _source_label(cache_source),
        "cache_publish": bool(getattr(cache, "publish", False)),
        "cache_retain": bool(getattr(cache, "retain", False)),
        "egress_mode": str(getattr(getattr(capabilities, "egress", None), "mode", "none")),
    }


def _source_label(source: object) -> str:
    kind = str(getattr(source, "kind", "repository"))
    step_id = getattr(source, "step_id", None)
    if step_id:
        return f"{kind}:{step_id}"
    authority = getattr(source, "authority", None)
    if authority:
        return f"{kind}:{authority}"
    access = getattr(source, "access", None)
    reference_id = getattr(source, "reference_id", None)
    if reference_id:
        return f"{kind}:{access}:{reference_id}" if access else f"{kind}:{reference_id}"
    return kind


def _import_previews(
    plan: ResolvedFleetPlan,
    applicability: tuple[PlanImportApplicabilityEvidence, ...],
) -> tuple[PlanImportPreview, ...]:
    decisions = {item.import_id: item for item in applicability}
    return tuple(
        PlanImportPreview(
            import_id=item.import_id,
            parent_import_id=item.parent_import_id,
            kind=item.kind,
            name=item.name,
            source_sha256=item.source_sha256,
            step_ids=item.step_ids,
            requirement_count=len(item.requirements),
            applicability=decisions[item.import_id].status,
            reason=decisions[item.import_id].reason,
        )
        for item in plan.imports
    )
