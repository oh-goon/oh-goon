from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT

_CANONICAL_RUN_FIELDS = frozenset(
    {
        "run_id",
        "started_at",
        "run_status",
        "repository_id",
        "repository_name",
        "repository_status",
    }
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


def _annotation_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _route_keyword(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _observations_route_function(tree: ast.Module) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "get"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and decorator.args[0].value == "/observations"
            ):
                continue
            response_model = _route_keyword(decorator, "response_model")
            operation_id = _route_keyword(decorator, "operation_id")
            if (
                _annotation_name(response_model) == "FleetExecutionTimeline"
                and isinstance(operation_id, ast.Constant)
                and operation_id.value == "fleet_run_execution_observations"
            ):
                return node
    return None



def _scout_duplicate_run_models() -> list[Path]:
    duplicates: list[Path] = []
    scout_root = BACKEND_PACKAGE_ROOT / "scout"
    for path in sorted(scout_root.rglob("*.py")):
        tree = python_ast(path)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            fields = {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            }
            if _CANONICAL_RUN_FIELDS <= fields:
                duplicates.append(path)
                break
    return duplicates


def _scout_evidence_uses_canonical_run_model(path: Path) -> bool:
    tree = python_ast(path)
    imports_canonical = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "oh_goon.core.execution_observability"
        and any(alias.name == "RepositoryRunObservation" for alias in node.names)
        for node in tree.body
    )
    if not imports_canonical:
        return False
    runs_annotations = [
        statement.annotation
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        for statement in node.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and statement.target.id == "runs"
    ]
    return len(runs_annotations) == 1 and "RepositoryRunObservation" in ast.unparse(runs_annotations[0])


def check_observability_projection_contract() -> list[Violation]:
    """Keep execution observability downstream, read-only, and on canonical status facts."""
    violations: list[Violation] = []
    projection = BACKEND_PACKAGE_ROOT / "core/execution_observability.py"
    for module in _imports(projection):
        if module.startswith(("oh_goon.runtime.", "oh_goon.services.", "oh_goon.integrations.")):
            violations.append(
                Violation(
                    projection,
                    f"execution observability must not depend on control/effect owner {module}",
                )
            )
        if ".scout" in module:
            violations.append(
                Violation(projection, "canonical execution observability must not depend on Scout")
            )

    model = BACKEND_PACKAGE_ROOT / "core/models/execution_observability.py"
    tree = python_ast(model)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
            isinstance(base, ast.Name) and base.id in {"Enum", "StrEnum"} for base in node.bases
        ):
            violations.append(
                Violation(
                    model,
                    "execution observability must reuse canonical status vocabulary instead of defining an enum",
                )
            )

    scout = BACKEND_PACKAGE_ROOT / "scout/evidence.py"
    if not _scout_evidence_uses_canonical_run_model(scout):
        violations.append(
            Violation(
                scout,
                "Scout evidence must consume canonical RepositoryRunObservation from core execution observability",
            )
        )
    for duplicate in _scout_duplicate_run_models():
        violations.append(
            Violation(
                duplicate,
                "Scout must not define a second Run identity/status representation",
            )
        )

    route = BACKEND_PACKAGE_ROOT / "api/routes/fleet_runs.py"
    route_tree = python_ast(route)
    route_function = _observations_route_function(route_tree)
    if route_function is None:
        violations.append(
            Violation(
                route,
                "canonical GET /observations FleetExecutionTimeline route with public operation identity is missing",
            )
        )
    return violations
