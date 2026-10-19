from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedOciBuildStep
from oh_goon.core.models.fleet_plan_evidence import (
    CicdRepositoryAdmissionEvidence,
    PlanImportApplicabilityEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.run_admission import PlanRepositoryAdmissionEvidence
from oh_goon.integrations.fleet_plan_admission import validate_cicd_snapshot_requirements
from oh_goon.integrations.fleet_plan_containerfile_policy import (
    validate_oci_build_repository_policy,
)
from oh_goon.integrations.fleet_plan_import_applicability import (
    import_applicability,
    import_skip_reasons,
)


@dataclass(frozen=True, slots=True)
class RepositoryScopeBinding:
    """One repository-scope decision shared by admission preview and execution preparation."""

    admission: CicdRepositoryAdmissionEvidence | None
    import_applicability: tuple[PlanImportApplicabilityEvidence, ...]
    import_skips: Mapping[str, str]
    active_step_ids: tuple[str, ...]


def bind_repository_scope(
    repository: Path, plan: ResolvedFleetPlan | None
) -> RepositoryScopeBinding:
    """Evaluate repository-owned pre-effect policy from one resolved Plan.

    Import applicability belongs to every Plan purpose so admission preview has one
    complete semantic projection. Strict CI/CD additionally owns typed repository
    admission and OCI repository policy.
    """

    if plan is None:
        return RepositoryScopeBinding(None, (), MappingProxyType({}), ())

    applicability = import_applicability(repository, plan)
    import_skips = MappingProxyType(import_skip_reasons(plan, applicability))
    if not isinstance(plan.execution, CicdExecutionPolicy):
        return RepositoryScopeBinding(
            admission=None,
            import_applicability=applicability,
            import_skips=import_skips,
            active_step_ids=tuple(
                step.step_id for step in plan.steps if step.step_id not in import_skips
            ),
        )

    admission = validate_cicd_snapshot_requirements(repository, plan.requirements)
    _validate_typed_step_repository_policy(repository, plan, import_skips)
    return RepositoryScopeBinding(
        admission=admission,
        import_applicability=applicability,
        import_skips=import_skips,
        active_step_ids=tuple(
            step.step_id for step in plan.steps if step.step_id not in import_skips
        ),
    )


def admitted_active_plan(
    plan: ResolvedFleetPlan, admitted: PlanRepositoryAdmissionEvidence
) -> ResolvedFleetPlan:
    """Project the exact active Steps transported by repository admission evidence."""

    if admitted.plan_id != plan.plan_id:
        raise FleetPlanInvalid("Repository admission Plan identity does not match resolved Plan")
    by_id = {step.step_id: step for step in plan.steps}
    if len(by_id) != len(plan.steps):
        raise FleetPlanInvalid("Resolved Plan contains duplicate Step identities")
    expected_import_ids = tuple(item.import_id for item in plan.imports)
    actual_import_ids = tuple(item.import_id for item in admitted.import_applicability)
    if actual_import_ids != expected_import_ids:
        raise FleetPlanInvalid(
            "Repository admission import applicability does not match canonical Plan imports"
        )
    skips = import_skip_reasons(plan, admitted.import_applicability)
    expected_active = tuple(step.step_id for step in plan.steps if step.step_id not in skips)
    if admitted.active_step_ids != expected_active:
        raise FleetPlanInvalid(
            "Repository admission active Steps do not match admitted import applicability"
        )
    return plan.model_copy(update={"steps": tuple(by_id[step_id] for step_id in expected_active)})


def _validate_typed_step_repository_policy(
    repository: Path, plan: ResolvedFleetPlan, import_skips: Mapping[str, str]
) -> None:
    for step in plan.steps:
        if step.step_id in import_skips:
            continue
        if not isinstance(step, ResolvedOciBuildStep):
            continue
        issues = validate_oci_build_repository_policy(repository, step)
        if issues:
            raise FleetPlanInvalid(
                f"Fleet OCI repository policy failed for step {step.step_id}: " + "; ".join(issues)
            )
