from __future__ import annotations

import pytest

from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedImport,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy, CicdResourcePolicy


def _step(step_id: str) -> ResolvedCommandStep:
    return ResolvedCommandStep(step_id=step_id, argv=("true",), timeout_seconds=10)


def _import(
    import_id: str,
    *,
    step_ids: tuple[str, ...],
    parent_import_id: str | None = None,
) -> ResolvedImport:
    return ResolvedImport(
        import_id=import_id,
        parent_import_id=parent_import_id,
        kind="goon",
        name=f"general/{import_id}",
        source_sha256="a" * 64,
        step_ids=step_ids,
    )


def _plan(*imports: ResolvedImport) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="graph",
        description="Import graph invariant test.",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=512, max_processes=64),
        ),
        requirements=(),
        imports=imports,
        steps=(_step("parent.child"), _step("other.child")),
    )


def test_resolved_import_graph_is_parent_first() -> None:
    parent = _import("parent", step_ids=("parent.child",))
    child = _import("child", parent_import_id="parent", step_ids=("parent.child",))

    with pytest.raises(ValueError, match="must appear after its parent"):
        _plan(child, parent)


def test_child_import_scope_must_be_contained_by_parent() -> None:
    parent = _import("parent", step_ids=("parent.child",))
    child = _import("child", parent_import_id="parent", step_ids=("other.child",))

    with pytest.raises(ValueError, match="step scope must be contained"):
        _plan(parent, child)


def test_sibling_import_scopes_cannot_overlap() -> None:
    first = _import("first", step_ids=("parent.child",))
    second = _import("second", step_ids=("parent.child",))

    with pytest.raises(ValueError, match="sibling imports.*overlap"):
        _plan(first, second)
