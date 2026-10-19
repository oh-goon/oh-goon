from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.integrations.workload_execution_requirements import (
    load_workload_execution_requirements,
    required_tools_for_owner,
)

ROOT = Path(__file__).resolve().parents[2]
_TOOLING_OWNER = "make:test-tooling-complete"
_SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}


def _literal_executable(call: ast.Call) -> str | None:
    if not call.args:
        return None
    argument = call.args[0]
    if isinstance(argument, (ast.Tuple, ast.List)) and argument.elts:
        first = argument.elts[0]
    else:
        first = argument
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _is_subprocess_call(call: ast.Call) -> bool:
    function = call.func
    return (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "subprocess"
        and function.attr in _SUBPROCESS_CALLS
    )


def _is_pytest_skip(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    target = node.func
    return (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "pytest"
        and target.attr == "skip"
    )


def _is_optional_node_guard(node: ast.AST, *, before_line: int) -> bool:
    if not isinstance(node, ast.If) or node.lineno >= before_line:
        return False
    test = ast.unparse(node.test)
    return (
        "shutil.which('node') is None" in test or 'shutil.which("node") is None' in test
    )


def _contains_optional_node_skip(
    function: ast.FunctionDef | ast.AsyncFunctionDef, *, before_line: int
) -> bool:
    guards = (
        node
        for node in ast.walk(function)
        if _is_optional_node_guard(node, before_line=before_line)
    )
    return any(_is_pytest_skip(child) for guard in guards for child in ast.walk(guard))


def _unguarded_literal_children(path: Path) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    children: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        executable = _literal_executable(node)
        if executable is None:
            continue
        if executable == "node":
            owner: ast.AST | None = node
            while owner is not None and not isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                owner = parent.get(owner)
            if isinstance(
                owner, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and _contains_optional_node_skip(owner, before_line=node.lineno):
                continue
        children.append((node.lineno, executable))
    return tuple(children)


def test_tooling_literal_subprocess_children_fit_strict_workload_authority() -> None:
    requirements = load_workload_execution_requirements(ROOT)
    allowed = {
        *required_tools_for_owner(requirements, _TOOLING_OWNER),
        "make",
        "/bin/sh",
    }
    offenders: list[str] = []
    for path in sorted((ROOT / "scripts/tests").glob("test_*.py")):
        for line, executable in _unguarded_literal_children(path):
            if executable not in allowed:
                offenders.append(f"{path.relative_to(ROOT)}:{line}:{executable}")
    assert offenders == []


def test_tooling_literal_child_scanner_rejects_new_unowned_child(
    tmp_path: Path,
) -> None:
    source = tmp_path / "test_unowned_child.py"
    source.write_text(
        "import subprocess\n"
        "def test_unowned():\n"
        "    subprocess.run(('curl', 'https://example.invalid'), check=False)\n",
        encoding="utf-8",
    )
    assert _unguarded_literal_children(source) == ((3, "curl"),)
