from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT

_CONTROL_PREFIXES = (
    "oh_goon.services.",
    "oh_goon.integrations.",
)
_DIAGNOSTIC_MODEL_MODULE = "oh_goon.core.models.diagnostic"
_REQUIRED_DIAGNOSTIC_SOURCES = (
    "services/fleet_run_execution.py",
    "services/game_tape_live_output.py",
    "integrations/fleet_plan_step_output_observation.py",
)


def _imports(path: Path) -> set[str]:
    tree = python_ast(path)
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _diagnostic_owner_bindings(path: Path) -> set[str]:
    """Return local names that bind the diagnostic semantic owner module or its exports."""

    tree = python_ast(path)
    bindings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _DIAGNOSTIC_MODEL_MODULE:
            bindings.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _DIAGNOSTIC_MODEL_MODULE and alias.asname:
                    bindings.add(alias.asname)
    return bindings


def _expression_root_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _expression_root_name(node.value)
    if isinstance(node, ast.Call):
        return _expression_root_name(node.func)
    return None


def _has_owner_backed_diagnostic_emit(path: Path) -> bool:
    """Prove emitted typed evidence originates from the diagnostic semantic owner.

    The exact constructor/classmethod/helper spelling is intentionally not architecture
    authority.  The boundary is that the event passed to ``emit`` is constructed through
    a binding imported from ``core.models.diagnostic``.
    """

    owner_bindings = _diagnostic_owner_bindings(path)
    if not owner_bindings:
        return False

    tree = python_ast(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "emit":
            continue
        event: ast.AST | None = node.args[0] if node.args else None
        if event is None:
            event = next(
                (keyword.value for keyword in node.keywords if keyword.arg == "event"),
                None,
            )
        if event is None:
            continue
        if _expression_root_name(event) in owner_bindings:
            return True
    return False


def check_diagnostic_ownership_contract() -> list[Violation]:
    """Keep diagnostic transport passive and source-owned rather than lifecycle-owning."""

    violations: list[Violation] = []
    model = BACKEND_PACKAGE_ROOT / "core/models/diagnostic.py"
    port = BACKEND_PACKAGE_ROOT / "core/ports/diagnostics.py"
    router = BACKEND_PACKAGE_ROOT / "runtime/diagnostics.py"

    for path in (model, port):
        for module in _imports(path):
            if module.startswith(("oh_goon.runtime.", *_CONTROL_PREFIXES)):
                violations.append(
                    Violation(
                        path,
                        f"diagnostic contract must not depend on control owner {module}",
                    )
                )

    for module in _imports(router):
        if module.startswith(_CONTROL_PREFIXES):
            violations.append(
                Violation(
                    router,
                    f"diagnostic sinks must not depend on product/effect owner {module}",
                )
            )

    router_text = router.read_text(encoding="utf-8")
    forbidden_control_terms = (
        "FleetRunStatus",
        "FleetRunTerminalIntent",
        "Journal",
        "ExecutionCoordinator",
        "cancel(",
        "retry(",
        "settle(",
    )
    for term in forbidden_control_terms:
        if term in router_text:
            violations.append(
                Violation(router, f"diagnostic sink/router must remain passive; found {term!r}")
            )

    for relative in _REQUIRED_DIAGNOSTIC_SOURCES:
        path = BACKEND_PACKAGE_ROOT / relative
        if not _has_owner_backed_diagnostic_emit(path):
            violations.append(
                Violation(
                    path,
                    "diagnostic degradation must emit typed evidence from the diagnostic semantic owner",
                )
            )
    return violations
