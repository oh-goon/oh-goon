from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_DYNAMIC_FILE_LOADER_CALLS = {"spec_from_file_location", "module_from_spec", "exec_module"}
_TEST_MODULE_PREFIX = "_oh_goon_test_"


def _dynamic_loader_calls(path: Path) -> set[tuple[str | None, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls: set[tuple[str | None, str]] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.functions: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_Call(self, node: ast.Call) -> None:
            target = node.func
            name = (
                target.id
                if isinstance(target, ast.Name)
                else target.attr
                if isinstance(target, ast.Attribute)
                else None
            )
            if name in _DYNAMIC_FILE_LOADER_CALLS:
                calls.add((self.functions[-1] if self.functions else None, name))
            self.generic_visit(node)

    Visitor().visit(tree)
    return calls


def _synthetic_module_names(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        call_name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else func.id
            if isinstance(func, ast.Name)
            else None
        )
        if call_name != "spec_from_file_location" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            names.append(first.value)
    return tuple(names)


def test_repository_tests_use_explicit_test_only_identity_for_dynamic_module_loading() -> None:
    observed: dict[str, set[tuple[str | None, str]]] = {}
    for base in (ROOT / "backend/tests", ROOT / "scripts/tests"):
        for path in base.rglob("*.py"):
            calls = _dynamic_loader_calls(path)
            if not calls:
                continue
            relative = path.relative_to(ROOT).as_posix()
            observed[relative] = calls
            names = _synthetic_module_names(path)
            assert names, relative
            assert all(name.startswith(_TEST_MODULE_PREFIX) for name in names), relative
            assert all(function is not None for function, _call in calls), relative

    # Dynamic loading is exceptional test isolation, but its legitimacy is semantic:
    # a clearly synthetic test-only module identity. No filename/path allowlist exists.
    assert observed


def test_dynamic_loader_policy_has_no_named_file_allowlist() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"), filename=__file__)
    module_assignments = {
        target.id
        for node in tree.body
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Name)
    }
    assert "_ALLOWED_DYNAMIC_LOADERS" not in module_assignments
