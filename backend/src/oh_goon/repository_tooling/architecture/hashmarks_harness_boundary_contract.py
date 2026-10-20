from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT, ROOT

AGENTS = ROOT / "AGENTS.md"
HASHMARKS_INTEGRATION_ROOT = BACKEND_PACKAGE_ROOT / "integrations" / "hashmarks"
HASHMARKS_SHARD_ADAPTER = HASHMARKS_INTEGRATION_ROOT / "verification_selection.py"
HASHMARKS_VERIFICATION_EXECUTION_BUILDER = (
    BACKEND_PACKAGE_ROOT / "repository_tooling/hashmarks_verification_execution.py"
)
EXECUTION_MATERIALIZATION_MODEL = BACKEND_PACKAGE_ROOT / "core/models/execution_materialization.py"
GAME_TAPE_MODEL = BACKEND_PACKAGE_ROOT / "core/models/game_tape.py"
GAME_TAPE_PROJECTION = BACKEND_PACKAGE_ROOT / "core/game_tape_projection.py"
VALIDATION_AUTHORITY_OWNER = BACKEND_PACKAGE_ROOT / "repository_tooling/self_certification_plan.py"
VALIDATION_GATE_POLICY_OWNER = (
    BACKEND_PACKAGE_ROOT / "repository_tooling/_self_certification_plan_units.py"
)

_HASHMARKS_AMBIENT_CONTROL_PLANE_IMPORTS = frozenset(
    {"socket", "subprocess", "requests", "httpx", "aiohttp", "urllib", "urllib3"}
)
_HASHMARKS_EXECUTION_IMPORT_PREFIXES = (
    "hashmarks.benchmark_shards",
    "hashmarks.execution",
    "hashmarks.harness",
)


def _imports(
    path: Path, *, tree: ast.AST | None = None, package_root: Path | None = None
) -> set[str]:
    tree = python_ast(path) if tree is None else tree
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                root = (package_root or BACKEND_PACKAGE_ROOT).resolve()
                try:
                    package = path.resolve().parent.relative_to(root.parent).parts
                except ValueError:
                    continue
                if node.level > len(package):
                    continue
                parent = package[: len(package) - node.level + 1]
                module = ".".join((*parent, module)) if module else ".".join(parent)
            if module:
                result.add(module)
                result.update(f"{module}.{alias.name}" for alias in node.names if alias.name != "*")
    return result


def _is_hashmarks_integration(path: Path, *, package_root: Path | None = None) -> bool:
    root = (package_root or BACKEND_PACKAGE_ROOT).resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return False
    return len(relative.parts) >= 3 and relative.parts[:2] == ("integrations", "hashmarks")


def _package_source_state(package_root: Path) -> tuple[tuple[str, int, int, int], ...]:
    package_root = package_root.resolve()
    rows: list[tuple[str, int, int, int]] = []
    for path in sorted(package_root.rglob("*.py")):
        stat_result = path.stat()
        rows.append(
            (
                path.relative_to(package_root).as_posix(),
                stat_result.st_size,
                stat_result.st_mtime_ns,
                stat_result.st_ctime_ns,
            )
        )
    return tuple(rows)


def _hashmarks_integration_violations(path: Path, path_imports: set[str]) -> list[Violation]:
    violations: list[Violation] = []
    ambient = sorted(
        module
        for module in path_imports
        if any(
            module == owner or module.startswith(owner + ".")
            for owner in _HASHMARKS_AMBIENT_CONTROL_PLANE_IMPORTS
        )
    )
    if ambient:
        violations.append(
            Violation(
                path,
                "Hashmarks integration may translate repository evidence but may not discover ambient daemon/socket/process/network authority: "
                + ", ".join(ambient),
            )
        )
    execution = sorted(
        module for module in path_imports if module.startswith(_HASHMARKS_EXECUTION_IMPORT_PREFIXES)
    )
    if execution:
        violations.append(
            Violation(
                path,
                "Hashmarks integration must not import producer execution/harness machinery; Oh-Goon owns execution: "
                + ", ".join(execution),
            )
        )
    return violations


@lru_cache(maxsize=16)
def _package_boundary_violations_cached(
    package_root_text: str, state: tuple[tuple[str, int, int, int], ...]
) -> tuple[Violation, ...]:
    package_root = Path(package_root_text)
    violations: list[Violation] = []
    for relative, _size, _mtime_ns, _ctime_ns in state:
        path = package_root / relative
        path_imports = _imports(path, package_root=package_root)
        if _is_hashmarks_integration(path, package_root=package_root):
            violations.extend(_hashmarks_integration_violations(path, path_imports))
            continue
        direct_hashmarks = sorted(
            module
            for module in path_imports
            if module == "hashmarks" or module.startswith("hashmarks.")
        )
        if direct_hashmarks:
            violations.append(
                Violation(
                    path,
                    "Hashmarks Python API imports must pass through the explicit oh_goon.integrations.hashmarks package: "
                    + ", ".join(direct_hashmarks),
                )
            )
    return tuple(violations)


def _package_boundary_violations(package_root: Path) -> tuple[Violation, ...]:
    package_root = package_root.resolve()
    return _package_boundary_violations_cached(
        str(package_root), _package_source_state(package_root)
    )


def _boundary_documentation_violations() -> list[Violation]:
    if AGENTS.is_file():
        return []
    return [Violation(AGENTS, "Hashmarks/Oh-Goon boundary documentation artifact is missing")]


def _hashmarks_integration_owner_violations(package_root: Path) -> list[Violation]:
    integration_root = package_root.resolve() / "integrations" / "hashmarks"
    if not integration_root.is_dir():
        return [
            Violation(
                integration_root,
                "explicit Hashmarks integration package is missing",
            )
        ]

    violations: list[Violation] = []
    package_init = integration_root / "__init__.py"
    if not package_init.is_file():
        violations.append(
            Violation(
                package_init,
                "Hashmarks integration boundary must remain an explicit Python package",
            )
        )

    structural_consumers = [
        path
        for path in sorted(integration_root.rglob("*.py"))
        if any(
            module == "hashmarks" or module.startswith("hashmarks.") for module in _imports(path)
        )
    ]
    if not structural_consumers:
        violations.append(
            Violation(
                integration_root,
                "Hashmarks integration boundary must structurally own external Hashmarks API consumption",
            )
        )
    return violations


def _presentation_boundary_violations() -> list[Violation]:
    violations: list[Violation] = []
    for owner in (GAME_TAPE_MODEL, GAME_TAPE_PROJECTION):
        if not owner.is_file():
            violations.append(Violation(owner, "Game Tape presentation owner is missing"))
        elif any(
            marker in owner.read_text(encoding="utf-8")
            for marker in ("canonical_payload_json", "HashmarksRepositoryEvidence")
        ):
            violations.append(
                Violation(
                    owner,
                    "Game Tape must consume identity-only Hashmarks disclosure metadata, never raw repository context",
                )
            )
    return violations


def _contract_identity_violations() -> list[Violation]:
    violations: list[Violation] = []
    for owner, label in (
        (HASHMARKS_SHARD_ADAPTER, "Hashmarks shard adapter"),
        (EXECUTION_MATERIALIZATION_MODEL, "execution materialization"),
        (GAME_TAPE_MODEL, "Game Tape lineage"),
    ):
        if not owner.is_file():
            violations.append(Violation(owner, f"{label} owner is missing"))
        elif "producer_contract_identity" not in owner.read_text(encoding="utf-8"):
            violations.append(Violation(owner, f"{label} must retain producer_contract_identity"))
    return violations


_VALIDATION_GATE_PROBE_SCRIPT_UNITS = (
    "scripts/m117-semantic-probe-a",
    "scripts/m117-semantic-probe-b",
)
_REQUIRED_VALIDATION_GATE_DEPENDENCIES = {
    "admission": frozenset({"goon-static-admission"}),
    "integration": frozenset({"integration"}),
    "architecture": frozenset({"architecture"}),
    "compile": frozenset({"compile"}),
    "shell": frozenset({"shell"}),
    "scripts": frozenset(_VALIDATION_GATE_PROBE_SCRIPT_UNITS),
}


def _resolved_validation_gate_dependencies() -> dict[str, object]:
    """Resolve validation gates through the canonical owner's behavior, not its source text."""

    from oh_goon.repository_tooling import _self_certification_plan_units as plan_units

    source_units = [
        {"unit": unit, "surface": plan_units.SCRIPTS_SURFACE}
        for unit in _VALIDATION_GATE_PROBE_SCRIPT_UNITS
    ]
    return dict(plan_units._source_gate_dependencies(source_units))


def _dependency_set(value: object) -> frozenset[str] | None:
    if not isinstance(value, (tuple, list, set, frozenset)):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return frozenset(value)


def _validation_authority_violations() -> list[Violation]:
    if not VALIDATION_AUTHORITY_OWNER.is_file():
        return [
            Violation(
                VALIDATION_AUTHORITY_OWNER,
                "Phase 21 canonical validation-authority owner is missing",
            )
        ]

    violations: list[Violation] = []
    if not VALIDATION_GATE_POLICY_OWNER.is_file():
        violations.append(
            Violation(
                VALIDATION_GATE_POLICY_OWNER,
                "canonical validation-gate policy owner is missing",
            )
        )

    delegated = sorted(
        module
        for module in _imports(VALIDATION_AUTHORITY_OWNER)
        if module == "hashmarks"
        or module.startswith("hashmarks.")
        or module.startswith("oh_goon.integrations.hashmarks")
    )
    if delegated:
        violations.append(
            Violation(
                VALIDATION_AUTHORITY_OWNER,
                "Hashmarks selection evidence must not own or suppress canonical validation gates: "
                + ", ".join(delegated),
            )
        )

    try:
        resolved = _resolved_validation_gate_dependencies()
    except (ImportError, TypeError, ValueError) as exc:
        violations.append(
            Violation(
                VALIDATION_GATE_POLICY_OWNER,
                f"canonical validation-gate semantics could not be resolved: {exc}",
            )
        )
        return violations

    for gate, required_dependencies in _REQUIRED_VALIDATION_GATE_DEPENDENCIES.items():
        if gate not in resolved:
            violations.append(
                Violation(
                    VALIDATION_GATE_POLICY_OWNER,
                    f"required validation gate {gate!r} is missing from canonical semantic owner",
                )
            )
            continue
        dependencies = _dependency_set(resolved[gate])
        if dependencies is None:
            violations.append(
                Violation(
                    VALIDATION_GATE_POLICY_OWNER,
                    f"required validation gate {gate!r} must resolve to a dependency sequence",
                )
            )
            continue
        missing = required_dependencies - dependencies
        if missing:
            violations.append(
                Violation(
                    VALIDATION_GATE_POLICY_OWNER,
                    f"required validation gate {gate!r} is missing dependencies: "
                    + ", ".join(sorted(missing)),
                )
            )
    return violations


def check_hashmarks_harness_boundary_contract() -> list[Violation]:
    """Keep Hashmarks API consumption behind one structural integration boundary."""

    violations: list[Violation] = []
    for check in (
        _boundary_documentation_violations,
        _presentation_boundary_violations,
        _contract_identity_violations,
        _validation_authority_violations,
    ):
        violations.extend(check())
    violations.extend(_hashmarks_integration_owner_violations(BACKEND_PACKAGE_ROOT))
    violations.extend(_package_boundary_violations(BACKEND_PACKAGE_ROOT))
    return violations
