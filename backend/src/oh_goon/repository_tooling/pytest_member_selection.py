"""Discover pytest members and safely prune statically excluded marker members.

This module owns pre-execution pytest member selection only. It does not own batch
partitioning, retries, timeouts, execution, receipts, or resume authority.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


def discover_test_nodes(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return tuple(
        f"{path.as_posix()}::{node.name}"
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    )


def _pytest_marker_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    mark = node.value
    if not isinstance(mark, ast.Attribute) or mark.attr != "mark":
        return None
    if not isinstance(mark.value, ast.Name) or mark.value.id != "pytest":
        return None
    return node.attr


def _module_marker_names(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in targets):
            continue
        value = node.value
        values = value.elts if isinstance(value, (ast.List, ast.Tuple, ast.Set)) else (value,)
        for candidate in values:
            name = _pytest_marker_name(candidate)
            if name is not None:
                names.add(name)
    return frozenset(names)


def _excluded_markers(marker: str | None) -> frozenset[str]:
    if not marker or not marker.strip():
        return frozenset()
    # Optimize only a conjunction of negative names, exactly the shape used by
    # Oh-Goon's fast gates. Complex expressions remain pytest's authority.
    terms = [term.strip() for term in marker.split("and")]
    names: set[str] = set()
    for term in terms:
        match = re.fullmatch(r"not\s+([A-Za-z_][A-Za-z0-9_]*)", term)
        if match is None:
            return frozenset()
        names.add(match.group(1))
    return frozenset(names)


def _cached_tree(trees: dict[Path, ast.Module], path: Path) -> ast.Module:
    tree = trees.get(path)
    if tree is None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        trees[path] = tree
    return tree


def _function_marker_names(tree: ast.Module, test_name: str) -> frozenset[str]:
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == test_name
        ),
        None,
    )
    if function is None:
        return frozenset()
    names: set[str] = set()
    for decorator in function.decorator_list:
        candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = _pytest_marker_name(candidate)
        if name is not None:
            names.add(name)
    return frozenset(names)




def _top_level_test_functions(tree: ast.Module) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...] | None:
    tests: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("pytest_"):
                return None
            if node.name.startswith("test_"):
                tests.append(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            return None
    return tuple(tests)


def _decorator_marker_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> frozenset[str]:
    names: set[str] = set()
    for decorator in function.decorator_list:
        candidate = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = _pytest_marker_name(candidate)
        if name is not None:
            names.add(name)
    return frozenset(names)


def _file_is_provably_excluded(tree: ast.Module, excluded: frozenset[str]) -> bool:
    """Prove a simple file has no runnable tests under the negative marker gate."""
    if not excluded:
        return False
    module_markers = _module_marker_names(tree)
    if module_markers & excluded:
        return True
    tests = _top_level_test_functions(tree)
    if not tests:
        return False
    return all((_decorator_marker_names(test) | module_markers) & excluded for test in tests)

def _member_marker_names(
    tree: ast.Module, *, separator: str, test_name: str
) -> frozenset[str]:
    names = set(_module_marker_names(tree))
    if separator:
        names.update(_function_marker_names(tree, test_name))
    return frozenset(names)


def statically_pruned_members(
    members: tuple[str, ...], *, marker: str | None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return runnable and provably excluded members without executing pytest."""
    excluded = _excluded_markers(marker)
    if not excluded:
        return members, ()
    runnable: list[str] = []
    pruned: list[str] = []
    trees: dict[Path, ast.Module] = {}
    for member in members:
        path_text, separator, test_name = member.partition("::")
        try:
            tree = _cached_tree(trees, Path(path_text))
        except (OSError, SyntaxError):
            runnable.append(member)
            continue
        excluded_member = (
            bool(separator)
            and bool(_member_marker_names(tree, separator=separator, test_name=test_name) & excluded)
        ) or (not separator and _file_is_provably_excluded(tree, excluded))
        (pruned if excluded_member else runnable).append(member)
    return tuple(runnable), tuple(pruned)

def discover_test_files(paths: tuple[Path, ...], excludes: frozenset[str]) -> tuple[str, ...]:
    found: set[str] = set()
    for path in paths:
        if path.is_file():
            candidates = (path,)
        elif path.is_dir():
            candidates = tuple(path.rglob("test_*.py"))
        else:
            raise ValueError(f"pytest batch path does not exist: {path}")
        for candidate in candidates:
            relative = candidate.as_posix()
            if relative not in excludes:
                found.add(relative)
    return tuple(sorted(found))
