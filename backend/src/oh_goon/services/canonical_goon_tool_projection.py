from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.models.fleet_plan import CompiledGoonImportStep
from oh_goon.integrations.fleet_plan_source_snapshot import read_fleet_plan_source
from oh_goon.integrations.workload_execution_requirements import (
    WorkloadExecutionRequirements,
    make_target_owner,
    required_tools_for_owner,
)
from oh_goon.runtime.template_discovery import (
    discover_template_packages,
    validate_template_package,
)


@dataclass(frozen=True, slots=True)
class MissingWorkloadToolPermission:
    goon: str
    step_id: str
    owner: str


@dataclass(frozen=True, slots=True)
class WorkloadToolPermissionViolation:
    goon: str
    step_id: str
    owner: str
    missing_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalGoonToolProjection:
    tools_by_goon: dict[str, dict[str, tuple[str, ...]]]
    workload_owner_by_step: dict[tuple[str, str], str]
    used_permission_owners: frozenset[str]
    missing_permissions: tuple[MissingWorkloadToolPermission, ...]
    permission_violations: tuple[WorkloadToolPermissionViolation, ...]
    stale_permission_owners: tuple[str, ...]


def derive_canonical_goon_tool_projection(
    root: Path,
    *,
    workload_permissions: dict[str, tuple[str, ...]],
    workloads: WorkloadExecutionRequirements,
) -> CanonicalGoonToolProjection:
    """Derive generated tool transport from compiler truth plus independent policy.

    Canonical Goon YAML is compiled through the production compiler without applying
    the generated authority file. Direct authored tool capability is compiler-owned.
    For a ``make <target>`` Step, the workload owner is derived from the compiled argv,
    the Make/native closure owns exact executable need, and repository policy supplies
    only the independent maximum permission ceiling.
    """

    template_root = root.resolve() / "templates/goons"
    packages = discover_template_packages(template_root, source_name="goon.yml", kind="Goon")
    tools_by_goon: dict[str, dict[str, tuple[str, ...]]] = {}
    owner_by_step: dict[tuple[str, str], str] = {}
    used_permission_owners: set[str] = set()
    missing_permissions: list[MissingWorkloadToolPermission] = []
    permission_violations: list[WorkloadToolPermissionViolation] = []

    for key, directory in packages.items():
        source_path = validate_template_package(directory, source_name="goon.yml", kind="Goon")
        compiled = read_fleet_plan_source(
            source_path,
            kind="goon",
            source=f"template:{key}/goon.yml",
            expected_name=directory.name,
        ).compiled
        projected_steps: dict[str, tuple[str, ...]] = {}
        for step in compiled.steps:
            if isinstance(step, CompiledGoonImportStep):
                continue
            owner = make_target_owner(getattr(step, "argv", None))
            if owner is not None:
                owner_by_step[(key, step.step_id)] = owner
                permitted = workload_permissions.get(owner)
                if permitted is None:
                    missing_permissions.append(
                        MissingWorkloadToolPermission(
                            goon=key,
                            step_id=step.step_id,
                            owner=owner,
                        )
                    )
                    # Missing independent permission is itself admission-fatal. Keep
                    # the raw compiled tools only so the complete catalog can still be
                    # inspected and all missing-policy consumers can be reported.
                    capabilities = getattr(step, "capabilities", None)
                    tools = tuple(capabilities.tools) if capabilities is not None else ()
                else:
                    required = required_tools_for_owner(workloads, owner)
                    denied = tuple(sorted(set(required) - set(permitted)))
                    if denied:
                        permission_violations.append(
                            WorkloadToolPermissionViolation(
                                goon=key,
                                step_id=step.step_id,
                                owner=owner,
                                missing_tools=denied,
                            )
                        )
                    tools = required
                    used_permission_owners.add(owner)
            else:
                capabilities = getattr(step, "capabilities", None)
                tools = tuple(capabilities.tools) if capabilities is not None else ()
            projected_steps[step.step_id] = tuple(tools)
        tools_by_goon[key] = projected_steps

    stale_permissions = tuple(sorted(set(workload_permissions) - used_permission_owners))
    return CanonicalGoonToolProjection(
        tools_by_goon=tools_by_goon,
        workload_owner_by_step=owner_by_step,
        used_permission_owners=frozenset(used_permission_owners),
        missing_permissions=tuple(missing_permissions),
        permission_violations=tuple(permission_violations),
        stale_permission_owners=stale_permissions,
    )
