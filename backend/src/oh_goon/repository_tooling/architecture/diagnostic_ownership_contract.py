from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_semantic_facts
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
    return set(python_semantic_facts(path, imported_only=True).imports)


def _has_owner_backed_diagnostic_emit(path: Path) -> bool:
    """Prove emitted typed evidence originates from the diagnostic semantic owner.

    The exact constructor/classmethod/helper spelling is intentionally not architecture
    authority.  The boundary is that the event passed to ``emit`` is constructed through
    a binding imported from ``core.models.diagnostic``.
    """

    facts = python_semantic_facts(path, imported_only=True)
    qualified_calls = {call: name for name, call in facts.calls}
    for _observation, node in facts.call_observations:
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
        # The native producer owns aliases, rebinding and shadowing. The AST
        # only identifies which exact call expression supplies the public event
        # argument; an unrelated imported name cannot qualify that expression.
        owner = qualified_calls.get(event) if isinstance(event, ast.Call) else None
        if owner is not None and owner.startswith(_DIAGNOSTIC_MODEL_MODULE + "."):
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
