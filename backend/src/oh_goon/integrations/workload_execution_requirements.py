from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.fleet_plan_tool_requirements import SHELL_RUNTIME_SUPPORT_TOOLS
from oh_goon.integrations.make_execution_authority import derive_make_target_authority
from oh_goon.integrations.package_script_execution_authority import PackageExecutableAuthority

MAKEFILE = "Makefile"
_ANNOTATION = re.compile(r"^# OH_GOON_OPAQUE_TOOLS: (?P<tools>none|[A-Za-z0-9_.+-]+(?: [A-Za-z0-9_.+-]+)*)$")
_TARGET = re.compile(r"^(?P<target>[A-Za-z0-9_.-]+):(?:\s|$)")
_OWNER_PREFIX = "make:"


@dataclass(frozen=True, slots=True)
class WorkloadExecutionRequirements:
    tools_by_owner: dict[str, tuple[str, ...]]
    opaque_tools_by_owner: dict[str, tuple[str, ...]]
    derived_tools_by_owner: dict[str, tuple[str, ...]]
    provenance_by_owner: dict[str, tuple[tuple[str, str], ...]]
    unresolved_by_owner: dict[str, tuple[str, ...]]
    source_identities_by_owner: dict[str, tuple[tuple[str, str], ...]]
    package_executables_by_owner: dict[str, tuple[PackageExecutableAuthority, ...]]
    package_provenance_by_owner: dict[str, tuple[tuple[str, str], ...]]
    identity: str


def _opaque_tools_from_annotation(raw: str, line_number: int) -> tuple[str, ...]:
    tools = () if raw == "none" else tuple(raw.split(" "))
    if tools != tuple(sorted(set(tools))):
        raise ValueError(
            f"{MAKEFILE}:{line_number}: OH_GOON_OPAQUE_TOOLS must be unique and sorted"
        )
    return tools


def _load_opaque_tools(path: Path) -> dict[str, tuple[str, ...]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"missing workload execution-requirements owner: {path}") from exc
    result: dict[str, tuple[str, ...]] = {}
    pending: tuple[str, ...] | None = None
    pending_line = 0
    for line_number, line in enumerate(lines, start=1):
        annotation = _ANNOTATION.fullmatch(line)
        if annotation is not None:
            if pending is not None:
                raise ValueError(
                    f"{MAKEFILE}:{line_number}: workload requirement annotation follows "
                    f"unbound annotation at line {pending_line}"
                )
            pending = _opaque_tools_from_annotation(annotation.group("tools"), line_number)
            pending_line = line_number
            continue
        if pending is None:
            continue
        target = _TARGET.match(line)
        if target is None:
            raise ValueError(
                f"{MAKEFILE}:{pending_line}: OH_GOON_OPAQUE_TOOLS must be immediately "
                "followed by its owning Make target"
            )
        owner = _OWNER_PREFIX + target.group("target")
        if owner in result:
            raise ValueError(f"duplicate workload execution-requirements owner: {owner}")
        result[owner] = pending
        pending = None
        pending_line = 0
    if pending is not None:
        raise ValueError(
            f"{MAKEFILE}:{pending_line}: OH_GOON_OPAQUE_TOOLS has no owning Make target"
        )
    return result




def _registered_make_owners(root: Path) -> tuple[str, ...]:
    """Return Make workload owners carrying independent repository permission.

    The policy registry does not mirror canonical Step identity or execution
    semantics. It names only workload owners for which the repository grants a tool
    permission ceiling. The canonical Step -> Make owner relationship is derived
    later from the compiled Step argv.
    """

    path = root / "backend" / "goon-execution-requirements.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid workload owner registry: {path}") from exc
    permissions = payload.get("workload_tool_permissions") if isinstance(payload, dict) else None
    if not isinstance(permissions, dict):
        raise ValueError(f"invalid workload owner registry: {path}")
    owners = tuple(sorted(permissions))
    if any(not isinstance(owner, str) or not owner.startswith(_OWNER_PREFIX) for owner in owners):
        raise ValueError(f"invalid Make workload permission owner in {path}")
    return owners

def _close_owner(
    root: Path, owner: str, opaque_tools: tuple[str, ...]
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    tuple[PackageExecutableAuthority, ...],
    tuple[tuple[str, str], ...],
]:
    target = owner.removeprefix(_OWNER_PREFIX)
    authority = derive_make_target_authority(root, target)
    opaque_provenance = tuple(
        (tool, f"{target} -> <opaque-owned-boundary> -> {tool}") for tool in opaque_tools
    )
    provenance = tuple(sorted((*authority.provenance, *opaque_provenance)))
    closed = tuple(sorted(set(opaque_tools) | set(authority.tools)))
    return (
        closed,
        authority.tools,
        provenance,
        authority.unresolved,
        authority.source_identities,
        authority.package_executables,
        authority.package_provenance,
    )


def _requirements_identity(
    closed: dict[str, tuple[str, ...]],
    derived: dict[str, tuple[str, ...]],
    provenance: dict[str, tuple[tuple[str, str], ...]],
    unresolved: dict[str, tuple[str, ...]],
    sources: dict[str, tuple[tuple[str, str], ...]],
    package_executables: dict[str, tuple[PackageExecutableAuthority, ...]],
    package_provenance: dict[str, tuple[tuple[str, str], ...]],
) -> str:
    encoded = json.dumps(
        {
            "owners": {
                owner: {
                    "tools": list(closed[owner]),
                    "derived_tools": list(derived[owner]),
                    "provenance": [list(item) for item in provenance[owner]],
                    "unresolved": list(unresolved[owner]),
                    "sources": [list(item) for item in sources[owner]],
                    "package_executables": [
                        list(item.identity_row()) for item in package_executables[owner]
                    ],
                    "package_provenance": [
                        list(item) for item in package_provenance[owner]
                    ],
                }
                for owner in sorted(closed)
            },
            "shell_runtime_support_tools": list(SHELL_RUNTIME_SUPPORT_TOOLS),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def load_workload_execution_requirements(root: Path) -> WorkloadExecutionRequirements:
    """Load opaque supplements and derive project-owned Make execution closure."""

    root = root.resolve()
    opaque = _load_opaque_tools(root / MAKEFILE)
    owners = tuple(sorted(set(_registered_make_owners(root)) | set(opaque)))
    opaque_by_owner = {owner: opaque.get(owner, ()) for owner in owners}
    closed: dict[str, tuple[str, ...]] = {}
    derived: dict[str, tuple[str, ...]] = {}
    provenance: dict[str, tuple[tuple[str, str], ...]] = {}
    unresolved: dict[str, tuple[str, ...]] = {}
    sources: dict[str, tuple[tuple[str, str], ...]] = {}
    package_executables: dict[str, tuple[PackageExecutableAuthority, ...]] = {}
    package_provenance: dict[str, tuple[tuple[str, str], ...]] = {}
    for owner in owners:
        opaque_tools = opaque_by_owner[owner]
        (
            owner_closed,
            owner_derived,
            owner_provenance,
            owner_unresolved,
            owner_sources,
            owner_package_executables,
            owner_package_provenance,
        ) = _close_owner(root, owner, opaque_tools)
        closed[owner] = owner_closed
        derived[owner] = owner_derived
        provenance[owner] = owner_provenance
        unresolved[owner] = owner_unresolved
        sources[owner] = owner_sources
        package_executables[owner] = owner_package_executables
        package_provenance[owner] = owner_package_provenance
    return WorkloadExecutionRequirements(
        tools_by_owner=closed,
        opaque_tools_by_owner=opaque_by_owner,
        derived_tools_by_owner=derived,
        provenance_by_owner=provenance,
        unresolved_by_owner=unresolved,
        source_identities_by_owner=sources,
        package_executables_by_owner=package_executables,
        package_provenance_by_owner=package_provenance,
        identity=_requirements_identity(
            closed,
            derived,
            provenance,
            unresolved,
            sources,
            package_executables,
            package_provenance,
        ),
    )


def required_tools_for_owner(
    requirements: WorkloadExecutionRequirements, owner: str
) -> tuple[str, ...]:
    try:
        return requirements.tools_by_owner[owner]
    except KeyError as exc:
        raise ValueError(f"unknown workload execution-requirements owner: {owner}") from exc


def make_target_owner(argv: tuple[str, ...] | list[str] | None) -> str | None:
    if not argv or len(argv) < 2 or argv[0] != "make":
        return None
    target = argv[1]
    if not isinstance(target, str) or not target or "=" in target or target.startswith("-"):
        return None
    return _OWNER_PREFIX + target


def require_make_target_owner(argv: tuple[str, ...] | list[str] | None, owner: str) -> None:
    actual = make_target_owner(argv)
    if actual != owner:
        raise FleetPlanInvalid(
            "Canonical Step execution-requirements owner does not match its Make workload: "
            f"declared={owner!r} actual={actual!r}"
        )
