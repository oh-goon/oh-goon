from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    PathExistsCondition,
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    ResolvedCommandStep,
    ResolvedFleetPlan,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.path_authority import AttemptWorkspaceAuthority


def _plan(step: ResolvedCommandStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="path-authority",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(step,),
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("working_directory", "../repo-b", "must not contain '..' traversal"),
        ("working_directory", "/srv/repo-b", "must be repository-relative"),
        ("artifact", "../repo-b/secret.txt", "must not contain '..' traversal"),
        ("output", r"reports\\result.txt", "must use '/' separators"),
        ("condition", "../repo-b/secret.txt", "must not contain '..' traversal"),
    ),
)
def test_resolved_execution_paths_use_one_repository_relative_contract(
    field: str, value: str, match: str
) -> None:
    kwargs: dict[str, object] = {
        "step_id": "check",
        "argv": ("true",),
        "timeout_seconds": 60,
    }
    if field == "working_directory":
        kwargs["working_directory"] = value
    elif field == "artifact":
        kwargs["artifacts"] = (PlanArtifactDeclaration(path=value),)
    elif field == "output":
        kwargs["outputs"] = (PlanPublishedOutput(name="RESULT", path=value),)
    elif field == "condition":
        kwargs["when"] = PathExistsCondition(path=value)
    else:  # pragma: no cover - parametrization is closed above
        raise AssertionError(field)

    with pytest.raises(ValidationError, match=match):
        _plan(ResolvedCommandStep(**kwargs))


def test_resolved_execution_paths_accept_canonical_repository_relative_syntax() -> None:
    plan = _plan(
        ResolvedCommandStep(
            step_id="check",
            argv=("true",),
            timeout_seconds=60,
            working_directory="packages/service",
            artifacts=(PlanArtifactDeclaration(path="reports/result.json"),),
            outputs=(PlanPublishedOutput(name="RESULT", path="outputs/result.txt"),),
            when=PathExistsCondition(path="pyproject.toml"),
        )
    )

    assert plan.steps[0].working_directory == "packages/service"


def test_attempt_workspace_authority_requires_exact_private_children(tmp_path: Path) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    children = {
        name: root / name for name in ("workspace", "home", "tmp", "cache", "config", "etc")
    }
    for path in children.values():
        path.mkdir()
    authority = AttemptWorkspaceAuthority.create(root=root, **children)
    authority.prove()


def test_attempt_workspace_authority_rejects_alias_or_escape(tmp_path: Path) -> None:
    root = tmp_path / "attempt"
    root.mkdir()
    children = {
        name: root / name for name in ("workspace", "home", "tmp", "cache", "config", "etc")
    }
    for path in children.values():
        path.mkdir()
    children["tmp"] = children["home"]
    with pytest.raises(FleetPlanInvalid, match="private roots must be distinct"):
        AttemptWorkspaceAuthority.create(root=root, **children)
