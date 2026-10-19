from __future__ import annotations

from pathlib import Path
from typing import Any

from oh_goon.integrations.canonical_goon_tool_authority import (
    load_canonical_goon_tool_authority,
)
from oh_goon.integrations.workload_execution_requirements import (
    WorkloadExecutionRequirements,
    make_target_owner,
)
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.canonical_goon_tool_projection import (
    derive_canonical_goon_tool_projection,
)
from oh_goon.services.goon_execution_requirements import REQUIREMENTS_FILE as REQUIREMENTS_FILE
from oh_goon.services.goon_execution_requirements import (
    SCHEMA,
    StaticAdmissionIssue,
    load_goon_execution_requirements,
    validate_goon_execution_requirements,
    workload_tool_permissions,
)
from oh_goon.services.goon_execution_requirements import (
    canonical_step_required_tools as canonical_step_required_tools,
)


def _catalog_keys(store: GoonTemplateStore, issues: list[StaticAdmissionIssue]) -> tuple[str, ...]:
    try:
        return store.keys()
    except Exception as exc:  # template package discovery must become one static failure
        issues.append(
            StaticAdmissionIssue(
                code="GOON_CATALOG_INVALID",
                message=f"canonical Goon catalog discovery failed: {exc}",
            )
        )
        return ()


def _validate_compiled_step(
    *, key: str, actual: object, keys: tuple[str, ...]
) -> list[StaticAdmissionIssue]:
    issues: list[StaticAdmissionIssue] = []
    actual_kind = str(actual.kind)
    if actual_kind == "goon":
        actual_child = getattr(actual, "goon", None)
        if actual_child not in keys:
            issues.append(
                StaticAdmissionIssue(
                    code="GOON_IMPORT_MISSING",
                    goon=key,
                    step_id=actual.step_id,
                    message=f"imported canonical Goon does not exist: {actual_child!r}",
                )
            )
        return issues
    argv = getattr(actual, "argv", None)
    if argv and Path(str(argv[0])).is_absolute():
        issues.append(
            StaticAdmissionIssue(
                code="ABSOLUTE_HOST_EXECUTABLE",
                goon=key,
                step_id=actual.step_id,
                message=(
                    "canonical Goon primary executables must be capability names or "
                    "repository-relative executables, never absolute host paths"
                ),
            )
        )
    return issues


def _compile_catalog(
    *,
    store: GoonTemplateStore,
    keys: tuple[str, ...],
    issues: list[StaticAdmissionIssue],
) -> tuple[dict[str, object], int]:
    compiled_by_key: dict[str, object] = {}
    step_count = 0
    for key in keys:
        try:
            compiled = store.source(key).compiled
        except Exception as exc:  # collect all bad canonical packages in one pass
            issues.append(
                StaticAdmissionIssue(
                    code="GOON_INVALID",
                    goon=key,
                    message=f"canonical Goon does not compile: {exc}",
                )
            )
            continue
        compiled_by_key[key] = compiled
        steps = tuple(compiled.steps)
        step_count += len(steps)
        for actual in steps:
            issues.extend(_validate_compiled_step(key=key, actual=actual, keys=keys))
    return compiled_by_key, step_count


def _validate_import_cycles(compiled_by_key: dict[str, object]) -> list[StaticAdmissionIssue]:
    graph = {
        key: tuple(
            str(step.goon) for step in compiled.steps if getattr(step, "kind", None) == "goon"
        )
        for key, compiled in compiled_by_key.items()
    }
    issues: list[StaticAdmissionIssue] = []
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            cycle = visiting[visiting.index(key) :] + [key]
            issues.append(
                StaticAdmissionIssue(
                    code="GOON_IMPORT_CYCLE",
                    goon=key,
                    message="canonical Goon import cycle: " + " -> ".join(cycle),
                )
            )
            return
        visiting.append(key)
        for child in graph.get(key, ()):
            if child in graph:
                visit(child)
        visiting.pop()
        visited.add(key)

    for key in sorted(graph):
        visit(key)
    return issues


def _requirements_failure(exc: ValueError) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "goon_count": 0,
        "step_count": 0,
        "requirements_identity": None,
        "execution_authority_identity": None,
        "authority_closure_count": 0,
        "authority_closures_proven": 0,
        "unresolved_execution_edge_count": 0,
        "package_executable_authority_count": 0,
        "issues": [
            StaticAdmissionIssue(
                code="REQUIREMENTS_INVALID",
                message=str(exc),
            ).as_payload()
        ],
    }


def _tool_authority_failure(exc: Exception) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "status": "FAIL",
        "goon_count": 0,
        "step_count": 0,
        "requirements_identity": None,
        "execution_authority_identity": None,
        "authority_closure_count": 0,
        "authority_closures_proven": 0,
        "unresolved_execution_edge_count": 0,
        "package_executable_authority_count": 0,
        "issues": [
            StaticAdmissionIssue(
                code="GOON_TOOL_AUTHORITY_INVALID",
                message=str(exc),
            ).as_payload()
        ],
    }


def _tool_projection_issues(
    *,
    root: Path,
    requirements: dict[str, Any],
    workloads: WorkloadExecutionRequirements,
    generated_authority: dict[str, dict[str, tuple[str, ...]]],
) -> list[StaticAdmissionIssue]:
    permissions = workload_tool_permissions(requirements)
    try:
        projection = derive_canonical_goon_tool_projection(
            root,
            workload_permissions=permissions,
            workloads=workloads,
        )
    except Exception as exc:
        return [
            StaticAdmissionIssue(
                code="GOON_TOOL_PROJECTION_INVALID",
                message=f"cannot derive canonical Goon tool projection: {exc}",
            )
        ]

    issues: list[StaticAdmissionIssue] = [
        StaticAdmissionIssue(
            code="TOOL_PERMISSION_POLICY_MISSING",
            goon=item.goon,
            step_id=item.step_id,
            message=(
                "compiled canonical Make workload has no independent repository "
                f"tool permission ceiling: {item.owner}"
            ),
        )
        for item in projection.missing_permissions
    ]
    for item in projection.permission_violations:
        provenance = workloads.provenance_by_owner.get(item.owner, ())
        issues.append(
            StaticAdmissionIssue(
                code="TOOL_PERMISSION_MISSING",
                goon=item.goon,
                step_id=item.step_id,
                missing_tools=item.missing_tools,
                authority_paths=tuple(
                    path for tool, path in provenance if tool in item.missing_tools
                ),
                message=(
                    "derived executable need exceeds independent repository permission; "
                    "the workload must not start"
                ),
            )
        )
    if projection.stale_permission_owners:
        issues.append(
            StaticAdmissionIssue(
                code="TOOL_PERMISSION_POLICY_STALE",
                message=(
                    "independent workload permission has no canonical Make consumer: "
                    + ", ".join(projection.stale_permission_owners)
                ),
            )
        )
    expected = projection.tools_by_goon
    for key, expected_steps in expected.items():
        generated_steps = generated_authority.get(key)
        if generated_steps is None:
            issues.append(
                StaticAdmissionIssue(
                    code="TOOL_AUTHORITY_STALE",
                    goon=key,
                    message="generated tool authority is missing the canonical Goon",
                )
            )
            continue
        for step_id, expected_tools in expected_steps.items():
            generated_tools = generated_steps.get(step_id)
            if generated_tools is None:
                issues.append(
                    StaticAdmissionIssue(
                        code="TOOL_AUTHORITY_STALE",
                        goon=key,
                        step_id=step_id,
                        message="generated tool authority is missing the compiled canonical Step",
                    )
                )
                continue
            missing = tuple(sorted(set(expected_tools) - set(generated_tools)))
            excess = tuple(sorted(set(generated_tools) - set(expected_tools)))
            owner = projection.workload_owner_by_step.get((key, step_id))
            provenance = workloads.provenance_by_owner.get(owner, ()) if owner else ()
            if missing:
                issues.append(
                    StaticAdmissionIssue(
                        code="TOOL_AUTHORITY_MISSING",
                        goon=key,
                        step_id=step_id,
                        missing_tools=missing,
                        authority_paths=tuple(path for tool, path in provenance if tool in missing),
                        message=(
                            "generated canonical Step tool transport is missing current "
                            "compiler/workload-derived executable need"
                        ),
                    )
                )
            if excess:
                issues.append(
                    StaticAdmissionIssue(
                        code="TOOL_AUTHORITY_EXCESS",
                        goon=key,
                        step_id=step_id,
                        excess_tools=excess,
                        message=(
                            "generated canonical Step tool transport exceeds current "
                            "compiler/workload-derived executable need"
                        ),
                    )
                )
        stale_steps = tuple(sorted(set(generated_steps) - set(expected_steps)))
        if stale_steps:
            issues.append(
                StaticAdmissionIssue(
                    code="TOOL_AUTHORITY_STALE",
                    goon=key,
                    message="generated tool authority references stale Steps: "
                    + ", ".join(stale_steps),
                )
            )
    stale_goons = tuple(sorted(set(generated_authority) - set(expected)))
    for key in stale_goons:
        issues.append(
            StaticAdmissionIssue(
                code="TOOL_AUTHORITY_STALE",
                goon=key,
                message="generated tool authority references a non-canonical Goon",
            )
        )
    return issues


def _authority_closure_metrics(
    compiled_by_key: dict[str, object],
    workloads: WorkloadExecutionRequirements,
) -> tuple[int, int, int]:
    total = 0
    proven = 0
    unresolved_count = 0
    for compiled in compiled_by_key.values():
        for step in compiled.steps:
            if getattr(step, "kind", None) == "goon":
                continue
            total += 1
            owner = make_target_owner(getattr(step, "argv", None))
            unresolved = workloads.unresolved_by_owner.get(owner, ()) if owner is not None else ()
            unresolved_count += len(unresolved)
            proven += int(not unresolved)
    return total, proven, unresolved_count


def _package_executable_authority_count(
    workloads: WorkloadExecutionRequirements,
) -> int:
    return len(
        {
            item.identity_row()
            for rows in workloads.package_executables_by_owner.values()
            for item in rows
        }
    )


def compile_canonical_goons(root: Path) -> dict[str, object]:
    """Statically validate every shipped canonical Goon without executing a workload."""
    root = root.resolve()
    try:
        requirements, requirements_identity, workloads = load_goon_execution_requirements(root)
    except ValueError as exc:
        return _requirements_failure(exc)

    issues = validate_goon_execution_requirements(requirements, workloads)
    authority_path = root / "backend/src/oh_goon/canonical_goon_tool_authority.json"
    try:
        generated_authority = load_canonical_goon_tool_authority(authority_path)
    except Exception as exc:
        return _tool_authority_failure(exc)
    issues.extend(
        _tool_projection_issues(
            root=root,
            requirements=requirements,
            workloads=workloads,
            generated_authority=generated_authority,
        )
    )
    try:
        store = GoonTemplateStore(
            root / "templates" / "goons",
            tool_authority_path=authority_path,
        )
    except Exception as exc:  # generated authority is a source-admission input
        return _tool_authority_failure(exc)
    keys = _catalog_keys(store, issues)
    compiled_by_key, step_count = _compile_catalog(
        store=store,
        keys=keys,
        issues=issues,
    )
    issues.extend(_validate_import_cycles(compiled_by_key))
    (
        authority_closure_count,
        authority_closures_proven,
        unresolved_execution_edge_count,
    ) = _authority_closure_metrics(compiled_by_key, workloads)

    ordered_issues = sorted(
        issues,
        key=lambda issue: (issue.goon or "", issue.step_id or "", issue.code, issue.message),
    )
    return {
        "schema": SCHEMA,
        "status": "PASS" if not ordered_issues else "FAIL",
        "goon_count": len(keys),
        "step_count": step_count,
        "requirements_identity": requirements_identity,
        "execution_authority_identity": workloads.identity,
        "authority_closure_count": authority_closure_count,
        "authority_closures_proven": authority_closures_proven,
        "unresolved_execution_edge_count": unresolved_execution_edge_count,
        "package_executable_authority_count": _package_executable_authority_count(workloads),
        "issues": [issue.as_payload() for issue in ordered_issues],
    }
