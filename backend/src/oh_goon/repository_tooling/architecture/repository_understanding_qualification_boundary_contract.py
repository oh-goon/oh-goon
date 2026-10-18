from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast
from oh_goon.repository_tooling.architecture.repository_layout import BACKEND_PACKAGE_ROOT, ROOT

HASHMARKS_INTEGRATION_ROOT = BACKEND_PACKAGE_ROOT / "integrations" / "hashmarks"
REPOSITORY_TOOLING_ROOT = BACKEND_PACKAGE_ROOT / "repository_tooling"
SECRET_ANSWER_PATH = "docs/certification/answers"

# These modules own Repository Understanding qualification truth.  Repository evidence
# may be consumed by qualification, but Hashmarks-facing adapters must never import
# these owners or gain access to their hidden answer/scoring/certificate semantics.
QUALIFICATION_AUTHORITY_MODULES = frozenset(
    {
        "oh_goon.repository_tooling.repository_understanding_integrity",
        "oh_goon.repository_tooling.repository_understanding_policy",
        "oh_goon.repository_tooling.repository_understanding_exam",
        "oh_goon.repository_tooling.repository_understanding_capability",
        "oh_goon.repository_tooling.repository_understanding_stability",
        "oh_goon.repository_tooling.repository_understanding_calibration",
        "oh_goon.repository_tooling.repository_understanding_lifecycle",
        "oh_goon.repository_tooling.repository_understanding_recertification",
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


def _string_literals(path: Path) -> set[str]:
    tree = python_ast(path)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _hashmarks_adapters() -> list[Path]:
    if not HASHMARKS_INTEGRATION_ROOT.is_dir():
        return []
    return sorted(HASHMARKS_INTEGRATION_ROOT.rglob("*.py"))


def _qualification_modules() -> list[Path]:
    if not REPOSITORY_TOOLING_ROOT.is_dir():
        return []
    return sorted(REPOSITORY_TOOLING_ROOT.glob("repository_understanding*.py"))



def check_repository_understanding_qualification_boundary_contract() -> list[Violation]:
    """Keep repository retrieval/intelligence separate from qualification authority."""

    violations: list[Violation] = []
    for path in _hashmarks_adapters():
        imports = _imports(path)
        forbidden_imports = sorted(
            module
            for module in imports
            if module in QUALIFICATION_AUTHORITY_MODULES
            or any(module.startswith(owner + ".") for owner in QUALIFICATION_AUTHORITY_MODULES)
        )
        if forbidden_imports:
            violations.append(
                Violation(
                    path,
                    "Hashmarks repository-evidence adapters must not import Repository Understanding "
                    "grading/policy/certificate authority: " + ", ".join(forbidden_imports),
                )
            )

        secret_literals = sorted(value for value in _string_literals(path) if SECRET_ANSWER_PATH in value)
        if secret_literals:
            violations.append(
                Violation(
                    path,
                    "Hashmarks repository evidence must never contain or address SECRET answer truth: "
                    + ", ".join(repr(value) for value in secret_literals),
                )
            )

    for path in _qualification_modules():
        imports = _imports(path)
        forbidden = sorted(
            module
            for module in imports
            if module == "hashmarks"
            or module.startswith("hashmarks.")
            or module.startswith("oh_goon.integrations.hashmarks")
        )
        if forbidden:
            violations.append(
                Violation(
                    path,
                    "Repository Understanding qualification owners may consume frozen repository evidence only "
                    "through admitted inputs; they must not import Hashmarks repository-intelligence semantics: "
                    + ", ".join(forbidden),
                )
            )

    return violations
