from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
from typing import Any

from oh_goon.repository_tooling.architecture.common import (
    PythonSemanticFacts,
    Violation,
    _node_byte_range,
    python_ast,
    python_semantic_facts,
    python_source_snapshot,
    source_binding_name,
    source_binding_packet,
    source_import_spelling,
)
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
_PUBLIC_HASHMARKS_API = "oh_goon.integrations.hashmarks"


def _owned_package_spelling(path: Path, reference: str, package_root: Path) -> str:
    """Compare authored relative spelling inside an explicitly owned package."""
    level = len(reference) - len(reference.lstrip("."))
    if not level:
        return reference
    try:
        package = path.resolve().parent.relative_to(package_root.resolve().parent).parts
    except ValueError:
        return ""
    if level > len(package):
        return ""
    parent = package[: len(package) - level + 1]
    tail = reference[level:]
    return ".".join((*parent, tail)) if tail else ".".join(parent)


def _imports(
    path: Path,
    *,
    tree: ast.Module | None = None,
    package_root: Path | None = None,
    facts: PythonSemanticFacts | None = None,
) -> set[str]:
    observed = facts or python_semantic_facts(path, imported_only=True, tree=tree)
    package_root = package_root or BACKEND_PACKAGE_ROOT
    result: set[str] = set()
    for fact in source_binding_packet(observed.evidence, language="python")["facts"]:
        syntax = fact.get("import_syntax")
        if fact.get("kind") != "import-binding" or not isinstance(syntax, dict):
            continue
        base = "." * syntax["level"] + (syntax["module"] or "")
        if base:
            result.add(_owned_package_spelling(path, base, package_root))
        if syntax["name"] != "*":
            result.add(_owned_package_spelling(path, source_import_spelling(syntax), package_root))
    result.discard("")
    return result


def _import_facts(path: Path, tree: ast.Module, package_root: Path) -> PythonSemanticFacts:
    del package_root
    return python_semantic_facts(path, imported_only=True, tree=tree)


def _package_policy_references(
    path: Path, facts: PythonSemanticFacts, package_root: Path
) -> set[str]:
    """Compare authored relative bindings under the explicitly owned package.

    This is package-boundary policy, not a claim that an imported implementation
    exists or resolves. The producer's target keeps module_resolution unresolved.
    """
    return {
        authored
        for reference in facts.references
        if (authored := _owned_package_spelling(path, reference, package_root))
    }


def _call_argument(call: ast.Call, position: int, keyword: str) -> ast.expr | None:
    if len(call.args) > position:
        return call.args[position]
    return next((item.value for item in call.keywords if item.arg == keyword), None)


def _native_argument(fact: dict[str, Any], position: int, keyword: str) -> dict[str, Any]:
    return next(
        (
            argument
            for argument in fact["arguments"]
            if (argument["kind"] == "positional" and argument["index"] == position)
            or (argument["kind"] == "keyword" and argument["name"] == keyword)
        ),
        {},
    )


def _native_literal_argument(fact: dict[str, Any], position: int, keyword: str) -> str | None:
    argument = _native_argument(fact, position, keyword)
    literal = argument.get("literal")
    if (
        argument.get("qualification") == "qualified"
        and not argument.get("conditions")
        and isinstance(literal, str)
    ):
        return literal
    return None


def _literal_import_name(fact: dict[str, Any], *, relative: bool) -> str | None:
    name = _native_literal_argument(fact, 0, "name")
    if name is None:
        return None
    if not relative or not name.startswith("."):
        return name
    package = _native_literal_argument(fact, 1, "package")
    if package is None:
        return None
    try:
        return resolve_name(name, package)
    except (ImportError, ValueError):
        return None


def _literal_fromlist(
    fact: dict[str, Any], call: ast.Call, facts: PythonSemanticFacts
) -> tuple[str, ...]:
    """Use AST only to select direct container elements in exact retained bytes."""
    selectors = _call_argument(call, 3, "fromlist")
    if not isinstance(selectors, (ast.List, ast.Tuple)):
        return ()
    argument = _native_argument(fact, 3, "fromlist")
    raw = facts.evidence.source_bytes
    if not argument or raw is None:
        return ()
    offsets = [0]
    for line in raw.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    literals = {
        (row["range"]["start_byte"], row["range"]["end_byte"]): row["literal"]
        for row in source_binding_packet(facts.evidence, language="python")["facts"]
        if row["kind"] == "literal-expression"
        and row.get("qualification") == "qualified"
        and not row.get("conditions")
        and isinstance(row.get("literal"), str)
        and row.get("call_argument") is not None
        and row["call_argument"]["call_range"] == fact["range"]
        and row["call_argument"]["range"] == argument["range"]
    }
    return tuple(
        literals[span]
        for item in selectors.elts
        if isinstance(item, ast.Constant) and (span := _node_byte_range(item, offsets)) in literals
    )


def _literal_dynamic_imports(facts: PythonSemanticFacts) -> set[str]:
    # Only literal selectors on scope-qualified native import functions are
    # admitted here. Runtime reflection and supplied callable inputs are not.
    result: set[str] = set()
    for fact, call in facts.call_observations:
        target = source_binding_name(fact, imported_only=True)
        if target not in {"importlib.import_module", "builtins.__import__"}:
            continue
        name = _literal_import_name(fact, relative=target == "importlib.import_module")
        if name is None:
            continue
        result.add(name)
        if target == "builtins.__import__":
            result.update(f"{name}.{selector}" for selector in _literal_fromlist(fact, call, facts))
    return result


def _unqualified_import_selectors(facts: PythonSemanticFacts) -> set[str]:
    """Retain unresolved import-shaped syntax without inventing builtin meaning."""
    selectors: set[str] = set()
    for fact, call in facts.call_observations:
        symbolic = fact.get("symbolic_reference") or {}
        origin = symbolic.get("root") or {}
        if (
            fact.get("qualification") != "unknown"
            or "unresolved-lexical-name" not in fact.get("reasons", [])
            or symbolic.get("qualification") != "qualified"
            or symbolic.get("conditions")
            or symbolic.get("properties")
            or origin.get("kind") != "unresolved-name"
            or origin.get("name") != "__import__"
        ):
            continue
        name = _literal_import_name(fact, relative=False)
        if name is not None:
            selectors.add(name)
            selectors.update(
                f"{name}.{selector}" for selector in _literal_fromlist(fact, call, facts)
            )
    return selectors


def _assigned_expression(tree: ast.Module, name: str) -> ast.expr | None:
    values = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    return values[0] if len(values) == 1 else None


def _public_exports(package_root: Path) -> frozenset[str]:
    path = package_root / "integrations/hashmarks/__init__.py"
    if not path.is_file():
        return frozenset()
    tree = python_ast(path)
    expression = _assigned_expression(tree, "__all__")
    if (
        isinstance(expression, ast.Call)
        and isinstance(expression.func, ast.Name)
        and expression.func.id == "tuple"
        and len(expression.args) == 1
        and isinstance(expression.args[0], ast.Name)
    ):
        expression = _assigned_expression(tree, expression.args[0].id)
    values: tuple[ast.expr | None, ...]
    if isinstance(expression, ast.Dict):
        values = tuple(expression.keys)
    elif isinstance(expression, (ast.Tuple, ast.List)):
        values = tuple(expression.elts)
    else:
        return frozenset()
    names = [
        value.value
        for value in values
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    ]
    if len(names) != len(values) or not all(
        name.isidentifier() and not name.startswith("_") for name in names
    ):
        return frozenset()
    return frozenset(names) if len(set(names)) == len(names) else frozenset()


def _private_api_violations(
    path: Path, names: set[str], exports: frozenset[str], *, wildcard: bool
) -> list[Violation]:
    private = sorted(
        name
        for name in names
        if name.startswith(_PUBLIC_HASHMARKS_API + ".")
        and name[len(_PUBLIC_HASHMARKS_API) + 1 :].split(".", 1)[0] not in exports
    )
    if not private and not wildcard:
        return []
    return [
        Violation(
            path,
            "Hashmarks consumers must use declared public API capabilities; private submodules, "
            "unknown exports and wildcard imports are not supported: "
            + ", ".join((*private, *(("*",) if wildcard else ()))),
        )
    ]


def _is_hashmarks_integration(path: Path, *, package_root: Path | None = None) -> bool:
    root = (package_root or BACKEND_PACKAGE_ROOT).resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return False
    return len(relative.parts) >= 3 and relative.parts[:2] == ("integrations", "hashmarks")


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


def _package_boundary_violations_in_snapshot(package_root: Path) -> tuple[Violation, ...]:
    violations: list[Violation] = []
    exports = _public_exports(package_root)
    for path in sorted(package_root.rglob("*.py")):
        tree = python_ast(path)
        inside = _is_hashmarks_integration(path, package_root=package_root)
        facts = _import_facts(path, tree, package_root)
        path_imports = _imports(path, package_root=package_root, facts=facts)
        path_imports.update(_literal_dynamic_imports(facts))
        if inside:
            violations.extend(_hashmarks_integration_violations(path, path_imports))
            continue
        violations.extend(
            _private_api_violations(
                path,
                path_imports | _package_policy_references(path, facts, package_root),
                exports,
                wildcard=any(
                    fact.get("kind") == "import-binding"
                    and fact.get("import_syntax", {}).get("name") == "*"
                    and _owned_package_spelling(
                        path,
                        "." * fact["import_syntax"]["level"]
                        + (fact["import_syntax"]["module"] or ""),
                        package_root,
                    )
                    == _PUBLIC_HASHMARKS_API
                    for fact in source_binding_packet(facts.evidence, language="python")["facts"]
                ),
            )
        )
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
        unqualified = _unqualified_import_selectors(facts)
        for row in _private_api_violations(path, unqualified, exports, wildcard=False):
            violations.append(
                Violation(
                    path,
                    "Unqualified import-shaped source (native target unresolved): " + row.message,
                )
            )
        direct_unqualified = sorted(
            name for name in unqualified if name == "hashmarks" or name.startswith("hashmarks.")
        )
        if direct_unqualified:
            violations.append(
                Violation(
                    path,
                    "Unqualified Hashmarks import-shaped source must pass through the explicit "
                    "oh_goon.integrations.hashmarks package; native target remains unresolved: "
                    + ", ".join(direct_unqualified),
                )
            )
    return tuple(violations)


def _package_boundary_violations(package_root: Path) -> tuple[Violation, ...]:
    package_root = package_root.resolve()
    with python_source_snapshot(package_root.parent):
        return _package_boundary_violations_in_snapshot(package_root)


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
    elif not _public_exports(package_root):
        violations.append(
            Violation(
                package_init,
                "Hashmarks public API must declare a nonempty, explicit __all__ capability census",
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

    source_units: list[dict[str, object]] = [
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
    with python_source_snapshot():
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
