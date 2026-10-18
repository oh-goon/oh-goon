from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import Violation, python_ast


def _top_level_string_assignments(path: Path, names: set[str]) -> dict[str, list[str | object]]:
    if not path.is_file():
        return {}
    observed: dict[str, list[str | object]] = {}
    for node in python_ast(path).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        value: str | object = (
            node.value.value
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            else object()
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                observed.setdefault(target.id, []).append(value)
    return observed


def _loaded_names(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    return {
        node.id
        for node in ast.walk(python_ast(path))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }


def owned_string_constant_violations(
    root: Path,
    required: Mapping[Path, Iterable[str]],
    *,
    missing_message: str,
    duplicate_message: str,
) -> list[Violation]:
    """Prove each public identity has one natural string owner without knowing its value."""

    violations: list[Violation] = []
    names = {name for constants in required.values() for name in constants}
    owners: dict[str, Path] = {}
    for path, constants in required.items():
        constants = tuple(constants)
        if not path.is_file():
            violations.append(Violation(path, missing_message))
            continue
        observed = _top_level_string_assignments(path, set(constants))
        for name in constants:
            values = observed.get(name, [])
            if len(values) != 1 or not isinstance(values[0], str):
                violations.append(
                    Violation(
                        path,
                        f"{missing_message}: {name} must be assigned exactly once as a string",
                    )
                )
                continue
            owners[name] = path.resolve()
            if name not in _loaded_names(path):
                violations.append(
                    Violation(
                        path,
                        f"{missing_message}: {name} is declared but not structurally consumed by its owner",
                    )
                )

    scan_root = root / "backend/src/oh_goon"
    duplicate_locations: dict[str, list[str]] = {}
    if scan_root.is_dir():
        for path in scan_root.rglob("*.py"):
            assignments = _top_level_string_assignments(path, names)
            for name, values in assignments.items():
                if not values:
                    continue
                if owners.get(name) == path.resolve() and len(values) == 1:
                    continue
                duplicate_locations.setdefault(name, []).append(
                    path.relative_to(scan_root).as_posix()
                )
    for name, locations in sorted(duplicate_locations.items()):
        violations.append(
            Violation(
                required_path_for_name(required, name),
                f"{duplicate_message}: {name}: " + ", ".join(sorted(locations)),
            )
        )
    return violations


def required_path_for_name(required: Mapping[Path, Iterable[str]], name: str) -> Path:
    for path, names in required.items():
        if name in names:
            return path
    raise KeyError(name)


def imported_modules(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    result: set[str] = set()
    for node in ast.walk(python_ast(path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def forbidden_import_violations(
    path: Path,
    modules: Iterable[str],
    *,
    message_prefix: str,
) -> list[Violation]:
    forbidden = set(modules)
    observed = imported_modules(path)
    hits = sorted(
        module
        for module in observed
        if module in forbidden or any(module.startswith(item + ".") for item in forbidden)
    )
    return [Violation(path, f"{message_prefix}: {module}") for module in hits]


def python_call_paths(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    result: set[str] = set()
    for node in ast.walk(python_ast(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            result.add(func.id)
        elif isinstance(func, ast.Attribute):
            parts = [func.attr]
            value = func.value
            while isinstance(value, ast.Attribute):
                parts.append(value.attr)
                value = value.value
            if isinstance(value, ast.Name):
                parts.append(value.id)
                result.add(".".join(reversed(parts)))
    return result
