from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT

_SCOUT_PACKAGE = "scout"
_COMPOSITION_LAYERS = frozenset({"api"})
_ROOT_COMPOSITION_MODULES = frozenset({"application.py", "_application_services.py"})
_ALLOWED_EXTERNAL_RUNTIME = frozenset({"oh_goon.runtime.database", "oh_goon.runtime.journal"})


def _imports(path: Path) -> set[str]:
    tree = python_ast(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _is_scout_owner(relative: Path) -> bool:
    return bool(relative.parts) and relative.parts[0] == _SCOUT_PACKAGE


def _is_composition_consumer(relative: Path) -> bool:
    if relative.parts and relative.parts[0] in _COMPOSITION_LAYERS:
        return True
    return len(relative.parts) == 1 and relative.name in _ROOT_COMPOSITION_MODULES


def _scout_dependency_violations(path: Path, imports: set[str]) -> list[Violation]:
    violations: list[Violation] = []
    for module in sorted(imports):
        if module == "oh_goon.scout" or module.startswith("oh_goon.scout."):
            continue
        forbidden = module.startswith(
            (
                "oh_goon.api",
                "oh_goon.integrations",
                "oh_goon.repository_tooling",
                "oh_goon.services",
            )
        )
        if module.startswith("oh_goon.runtime.") and module not in _ALLOWED_EXTERNAL_RUNTIME:
            forbidden = True
        if forbidden:
            violations.append(Violation(path, f"Scout must not depend on control owner {module}"))
    return violations


def check_scout_observer_contract(package_root: Path = BACKEND_PACKAGE_ROOT) -> list[Violation]:
    """Keep Scout package-owned, downstream, and outside execution authority."""
    violations: list[Violation] = []
    scout_root = package_root / _SCOUT_PACKAGE
    if not scout_root.is_dir():
        return [
            Violation(scout_root, "Scout must have one explicit oh_goon.scout package boundary")
        ]
    for path in package_root.rglob("*.py"):
        relative = path.relative_to(package_root)
        if relative.parts and relative.parts[0] == "repository_tooling":
            continue
        imports = _imports(path)
        if _is_scout_owner(relative):
            violations.extend(_scout_dependency_violations(path, imports))
            continue
        if _is_composition_consumer(relative):
            continue
        if any(
            module == "oh_goon.scout" or module.startswith("oh_goon.scout.") for module in imports
        ):
            violations.append(Violation(path, "execution owners must not depend on Scout"))
    return violations
