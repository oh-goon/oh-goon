from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import CompiledExecutionTemplate, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_diagnostics import (
    FleetPlanCheckReport,
    FleetPlanSemanticDiff,
    PlanCheckIssue,
    PlanFieldChange,
    PlanStepChange,
    PlanStepChangeKind,
    relative_diagnostic_path,
)
from oh_goon.core.path_authority import ContainedPathSpace, canonical_path, navigation_path
from oh_goon.integrations.fleet_plan_compilation import load_plan_template


def check_plan_sources(
    root: Path,
    *,
    containment_root: Path | None = None,
    compiled_overrides: Mapping[Path, CompiledExecutionTemplate] | None = None,
) -> FleetPlanCheckReport:
    """Validate Plan authoring syntax only; Goon graph validation belongs to the resolver."""

    root = navigation_path(root)
    overrides = {canonical_path(path): value for path, value in (compiled_overrides or {}).items()}
    files = (root,) if root in overrides and not root.exists() else _plan_files(root)
    base = _diagnostic_root(root, containment_root)
    issues: list[PlanCheckIssue] = []
    for path in files:
        try:
            overrides.get(canonical_path(path)) or load_plan_template(path)
        except (FleetPlanInvalid, ValueError) as exc:
            issues.append(
                PlanCheckIssue(path=relative_diagnostic_path(path, base), message=str(exc))
            )
    return FleetPlanCheckReport(root=str(root), checked_files=len(files), issues=tuple(issues))


def semantic_plan_diff(
    before: ResolvedFleetPlan, after: ResolvedFleetPlan
) -> FleetPlanSemanticDiff:
    plan_fields = _field_changes(
        {
            "contract_version": before.contract_version,
            "name": before.name,
            "description": before.description,
            "execution": before.execution.model_dump(mode="json"),
            "requirements": [item.model_dump(mode="json") for item in before.requirements],
            "imports": [item.model_dump(mode="json") for item in before.imports],
        },
        {
            "contract_version": after.contract_version,
            "name": after.name,
            "description": after.description,
            "execution": after.execution.model_dump(mode="json"),
            "requirements": [item.model_dump(mode="json") for item in after.requirements],
            "imports": [item.model_dump(mode="json") for item in after.imports],
        },
    )
    before_steps = {step.step_id: step for step in before.steps}
    after_steps = {step.step_id: step for step in after.steps}
    changes: list[PlanStepChange] = []
    for step_id in sorted(set(before_steps) | set(after_steps)):
        old = before_steps.get(step_id)
        new = after_steps.get(step_id)
        if old is None:
            changes.append(PlanStepChange(step_id=step_id, kind=PlanStepChangeKind.ADDED))
            continue
        if new is None:
            changes.append(PlanStepChange(step_id=step_id, kind=PlanStepChangeKind.REMOVED))
            continue
        fields = _field_changes(
            old.model_dump(mode="json", exclude={"step_id"}),
            new.model_dump(mode="json", exclude={"step_id"}),
        )
        if fields:
            changes.append(
                PlanStepChange(step_id=step_id, kind=PlanStepChangeKind.CHANGED, fields=fields)
            )
    return FleetPlanSemanticDiff(
        before_plan_id=before.plan_id,
        after_plan_id=after.plan_id,
        plan_fields=plan_fields,
        steps=tuple(changes),
    )


def _diagnostic_root(root: Path, containment_root: Path | None) -> Path:
    if containment_root is None:
        return root if root.is_dir() else root.parent
    base = navigation_path(containment_root)
    if not base.is_dir():
        raise FleetPlanInvalid(f"Plan containment root is not a directory: {base}")
    ContainedPathSpace.from_root(base).relative(root, label="Plan diagnostic path")
    return base


def _plan_files(root: Path) -> tuple[Path, ...]:
    if root.is_file():
        return (root,)
    if not root.is_dir():
        raise FleetPlanInvalid(f"Plan path does not exist: {root}")
    files = tuple(sorted({*root.rglob("*.yml"), *root.rglob("*.yaml")}))
    if not files:
        raise FleetPlanInvalid(f"Plan directory contains no YAML files: {root}")
    return files


def _field_changes(before: dict[str, Any], after: dict[str, Any]) -> tuple[PlanFieldChange, ...]:
    old = _flatten(before)
    new = _flatten(after)
    return tuple(
        PlanFieldChange(field=field, before=old.get(field), after=new.get(field))
        for field in sorted(set(old) | set(new))
        if old.get(field) != new.get(field)
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    result: dict[str, Any] = {}
    for key, item in value.items():
        field = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict):
            result.update(_flatten(item, field))
        else:
            result[field] = item
    return result
