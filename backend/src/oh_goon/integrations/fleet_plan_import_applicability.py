from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedImport
from oh_goon.core.models.fleet_plan_evidence import PlanImportApplicabilityEvidence
from oh_goon.integrations.fleet_plan_admission import inspect_repository_requirements


def import_applicability(
    repository: Path,
    plan: ResolvedFleetPlan,
) -> tuple[PlanImportApplicabilityEvidence, ...]:
    """Evaluate one parent-aware applicability decision per canonical import."""

    imports = {item.import_id: item for item in plan.imports}
    decisions: dict[str, PlanImportApplicabilityEvidence] = {}

    def decide(imported: ResolvedImport) -> PlanImportApplicabilityEvidence:
        existing = decisions.get(imported.import_id)
        if existing is not None:
            return existing
        if imported.parent_import_id is not None:
            parent = decide(imports[imported.parent_import_id])
            if parent.status == "NOT_APPLICABLE":
                decision = PlanImportApplicabilityEvidence(
                    import_id=imported.import_id,
                    status="NOT_APPLICABLE",
                    reason=(
                        f"Imported template {imported.name} ({imported.import_id}) is not applicable "
                        f"because parent import {imported.parent_import_id} is not applicable"
                    ),
                )
                decisions[imported.import_id] = decision
                return decision
        decision = _direct_applicability(repository, imported)
        decisions[imported.import_id] = decision
        return decision

    return tuple(decide(imported) for imported in plan.imports)


def _direct_applicability(
    repository: Path,
    imported: ResolvedImport,
) -> PlanImportApplicabilityEvidence:
    issues = inspect_repository_requirements(repository, imported.requirements)
    if not issues:
        return PlanImportApplicabilityEvidence(
            import_id=imported.import_id, status="APPLICABLE", reason=""
        )
    return PlanImportApplicabilityEvidence(
        import_id=imported.import_id,
        status="NOT_APPLICABLE",
        reason=(
            f"Imported Goon {imported.name} ({imported.import_id}) is not applicable: "
            + "; ".join(issues)
        ),
    )


def import_skip_reasons(
    plan: ResolvedFleetPlan,
    applicability: tuple[PlanImportApplicabilityEvidence, ...],
) -> dict[str, str]:
    """Project only root-cause non-applicability onto concrete imported steps."""

    imports = {item.import_id: item for item in plan.imports}
    decisions = {item.import_id: item for item in applicability}
    reasons: dict[str, list[str]] = {}
    for decision in applicability:
        if decision.status == "APPLICABLE":
            continue
        imported = imports[decision.import_id]
        if imported.parent_import_id is not None:
            parent = decisions[imported.parent_import_id]
            if parent.status == "NOT_APPLICABLE":
                continue
        for step_id in imported.step_ids:
            reasons.setdefault(step_id, []).append(decision.reason)
    return {step_id: " | ".join(items) for step_id, items in reasons.items()}


def applicable_plan_for_repository(
    repository: Path,
    plan: ResolvedFleetPlan,
) -> tuple[ResolvedFleetPlan, tuple[PlanImportApplicabilityEvidence, ...]]:
    """Project one resolved Plan to only the imported Steps applicable to this repository."""

    applicability = import_applicability(repository, plan)
    skips = import_skip_reasons(plan, applicability)
    active_steps = tuple(step for step in plan.steps if step.step_id not in skips)
    return plan.model_copy(update={"steps": active_steps}), applicability
