from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT


def _imported_modules(path: Path) -> set[str]:
    tree = python_ast(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def check_backend_boundaries() -> list[Violation]:
    violations: list[Violation] = []
    forbidden = {
        "core": (
            "oh_goon.api",
            "oh_goon.services",
            "oh_goon.runtime",
            "oh_goon.integrations",
        ),
        "integrations": ("oh_goon.api", "oh_goon.services", "oh_goon.runtime"),
        "runtime": ("oh_goon.api", "oh_goon.services"),
        "services": ("oh_goon.api",),
    }
    for layer, prefixes in forbidden.items():
        for path in (BACKEND_PACKAGE_ROOT / layer).rglob("*.py"):
            for module in _imported_modules(path):
                if module.startswith(prefixes):
                    violations.append(
                        Violation(path, f"{layer} layer must not import {module}")
                    )
    workflow_path = BACKEND_PACKAGE_ROOT / "workflow.py"
    cli_self_path = BACKEND_PACKAGE_ROOT / "cli_self.py"
    allowed_repository_tooling: dict[Path, set[str]] = {
        workflow_path: {"oh_goon.repository_tooling.source_normalization"},
        cli_self_path: {"oh_goon.repository_tooling.self_certification_plan"},
    }
    repository_tooling_root = BACKEND_PACKAGE_ROOT / "repository_tooling"
    for path in BACKEND_PACKAGE_ROOT.rglob("*.py"):
        if path.is_relative_to(repository_tooling_root):
            continue
        for module in _imported_modules(path):
            if module == "scripts":
                violations.append(Violation(path, "installed product code must not import repository tooling"))
                continue
            if module.startswith("oh_goon.repository_tooling."):
                if module in allowed_repository_tooling.get(path, set()):
                    continue
                violations.append(
                    Violation(
                        path,
                        "installed product code must not import repository tooling",
                    )
                )
    return violations
