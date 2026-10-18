from __future__ import annotations

import ast
import builtins
import dis
import importlib
import inspect
import subprocess
import sys
import tomllib
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from oh_goon.repository_tooling.release.package_slim import canonical_source_files

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_ROOT = _BACKEND_ROOT.parent
_SOURCE_ROOT = _BACKEND_ROOT / "src" / "oh_goon"
_SOURCE_FILES = tuple(sorted(_SOURCE_ROOT.rglob("*.py")))
_OWNED_PYTHON_FILES = tuple(
    path for path in canonical_source_files(_PROJECT_ROOT) if path.suffix == ".py"
)

# These modules deliberately import the platform lock primitive on only the
# platform that can execute that branch. The opposite name is therefore absent
# after a successful import and must not be mistaken for an unresolved runtime
# global on the current platform.
_PLATFORM_CONDITIONAL_GLOBALS = {
    "oh_goon.integrations.exact_file_write": frozenset({"fcntl", "msvcrt"}),
    "oh_goon.integrations.fleet_content_store_lifecycle": frozenset({"fcntl", "msvcrt"}),
}


def _relative_source(source_file: Path) -> str:
    return source_file.relative_to(_SOURCE_ROOT).as_posix()


def _relative_project_python_file(source_file: Path) -> str:
    return source_file.relative_to(_PROJECT_ROOT).as_posix()


def _file_declared_names(tree: ast.AST) -> set[str]:
    """Return names declared anywhere in one file.

    This intentionally uses a file-wide inventory instead of attempting to become a
    second Python linter.  Its narrow purpose is to catch annotation names that are
    never declared/imported anywhere in the file, including postponed annotations
    that bytecode LOAD_GLOBAL scanning cannot see.
    """

    declared = set(vars(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            declared.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                declared.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    declared.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Param)):
            declared.add(node.id)
    return declared


def _annotation_nodes(tree: ast.AST) -> Iterator[tuple[int, ast.expr]]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
            for argument in arguments:
                if argument.annotation is not None:
                    yield node.lineno, argument.annotation
            if node.args.vararg is not None and node.args.vararg.annotation is not None:
                yield node.lineno, node.args.vararg.annotation
            if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
                yield node.lineno, node.args.kwarg.annotation
            if node.returns is not None:
                yield node.lineno, node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.lineno, node.annotation


def _undeclared_annotation_names(tree: ast.AST) -> list[str]:
    declared = _file_declared_names(tree)
    failures: list[str] = []
    for line, annotation in _annotation_nodes(tree):
        referenced = {
            node.id
            for node in ast.walk(annotation)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        missing = sorted(referenced - declared)
        if missing:
            failures.append(
                f"line {line}: annotation {ast.unparse(annotation)!r} uses undeclared {missing}"
            )
    return failures


def test_annotation_binding_smoke_rejects_a_never_declared_annotation_name() -> None:
    tree = ast.parse(
        "from __future__ import annotations\n"
        "def example(value: MissingType) -> None:\n"
        "    return None\n"
    )

    assert _undeclared_annotation_names(tree) == [
        "line 2: annotation 'MissingType' uses undeclared ['MissingType']"
    ]


@pytest.mark.parametrize("python_file", _OWNED_PYTHON_FILES, ids=_relative_project_python_file)
def test_each_owned_python_file_compiles_and_annotation_names_are_declared(
    python_file: Path,
) -> None:
    """Every owned Python file gets one pytest node, including tests and tooling.

    This is deliberately smaller than Ruff: compile the exact file and prove every
    annotation name is declared/imported somewhere in that file.  It catches obvious
    postponed-annotation NameErrors without making unrelated lint debt authoritative.
    """

    source = python_file.read_text(encoding="utf-8")
    compile(source, str(python_file), "exec")
    tree = ast.parse(source, filename=str(python_file))
    assert _undeclared_annotation_names(tree) == []


def _module_name(source_file: Path) -> str:
    relative = source_file.relative_to(_SOURCE_ROOT)
    parts = relative.with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(("oh_goon", *parts))


def _code_objects(code: types.CodeType) -> Iterator[types.CodeType]:
    yield code
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            yield from _code_objects(value)


def _class_functions(cls: type[object]) -> Iterator[tuple[str, object]]:
    for name, value in vars(cls).items():
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        if isinstance(value, property):
            for accessor_name, accessor in (
                ("fget", value.fget),
                ("fset", value.fset),
                ("fdel", value.fdel),
            ):
                if accessor is not None:
                    yield f"{name}.{accessor_name}", accessor
            continue
        if inspect.isfunction(value):
            yield name, value
        elif inspect.isclass(value):
            for nested_name, function in _class_functions(value):
                yield f"{name}.{nested_name}", function


def _module_functions(module: object) -> Iterator[tuple[str, object]]:
    module_name = module.__name__
    for name, value in vars(module).items():
        if inspect.isfunction(value) and value.__module__ == module_name:
            yield name, value
        elif inspect.isclass(value) and value.__module__ == module_name:
            for nested_name, function in _class_functions(value):
                if getattr(function, "__module__", None) == module_name:
                    yield f"{name}.{nested_name}", function


def _unresolved_global_references(module_name: str) -> list[str]:
    module = importlib.import_module(module_name)
    builtin_names = vars(builtins)
    allowed = _PLATFORM_CONDITIONAL_GLOBALS.get(module_name, frozenset())
    unresolved: set[str] = set()

    for qualified_name, function in _module_functions(module):
        function_globals = function.__globals__
        for code in _code_objects(function.__code__):
            for instruction in dis.get_instructions(code):
                if instruction.opname != "LOAD_GLOBAL":
                    continue
                name = instruction.argval
                if not isinstance(name, str):
                    continue
                if name in function_globals or name in builtin_names or name in allowed:
                    continue
                unresolved.add(
                    f"{module_name}:{qualified_name}:{code.co_name}: unresolved global {name!r}"
                )
    return sorted(unresolved)


@pytest.mark.parametrize("source_file", _SOURCE_FILES, ids=_relative_source)
def test_each_backend_source_file_compiles_imports_and_resolves_globals(source_file: Path) -> None:
    """Every authored backend Python file gets one locally runnable pytest node.

    Every package module must import and every runtime ``LOAD_GLOBAL`` used by
    module-owned functions/classes must resolve from the imported module, builtins,
    or one explicitly platform-conditional primitive. ``oh_goon.__main__`` is
    import-skipped because importing it intentionally launches the CLI process; the
    separate owned-file test still compiles it and the subprocess smoke executes it.
    """

    module_name = _module_name(source_file)
    if module_name == "oh_goon.__main__":
        return

    importlib.import_module(module_name)
    assert _unresolved_global_references(module_name) == []


def _project_scripts() -> dict[str, str]:
    data = tomllib.loads((_BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(data["project"]["scripts"])


def _resolve_script_target(target: str) -> object:
    module_name, attribute_path = target.split(":", 1)
    value: object = importlib.import_module(module_name)
    for attribute in attribute_path.split("."):
        value = getattr(value, attribute)
    return value


def test_declared_console_scripts_resolve_to_callables() -> None:
    failures: list[str] = []
    for script_name, target in sorted(_project_scripts().items()):
        try:
            resolved = _resolve_script_target(target)
        except Exception as exc:
            failures.append(f"{script_name} -> {target}: {type(exc).__name__}: {exc}")
            continue
        if not callable(resolved):
            failures.append(f"{script_name} -> {target}: resolved object is not callable")

    assert failures == []


def test_package_main_entrypoint_executes_help_without_optional_native_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "oh_goon", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "oh-goon local fleet maintenance and strict CI/CD" in completed.stdout
