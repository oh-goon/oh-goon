from __future__ import annotations

import ast
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


def project_scripts_from_text(text: str) -> dict[str, str]:
    """Return semantic ``[project.scripts]`` ownership from TOML text.

    Quote style, whitespace, and ordering are representation details. Architecture
    contracts should consume this mapping instead of searching raw pyproject text.
    """

    payload = tomllib.loads(text)
    project = payload.get("project")
    if not isinstance(project, dict):
        return {}
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {
        str(name): target
        for name, target in scripts.items()
        if isinstance(name, str) and isinstance(target, str)
    }


DEPENDENCY_POLICY_BOOTSTRAP_CAPABILITY = "oh-goon-dependency-policy"


def project_script_source_path(
    root: Path,
    pyproject_text: str,
    capability: str,
) -> Path | None:
    """Project one named script capability to its owned source module.

    The script mapping is semantic authority.  A backing filename is a private
    representation detail and may change together with the mapping.
    """

    target = project_scripts_from_text(pyproject_text).get(capability)
    if target is None:
        return None
    module, separator, callable_name = target.partition(":")
    if not separator or not module or callable_name != "main":
        return None
    parts = module.split(".")
    if any(not part.isidentifier() for part in parts):
        return None
    return root / "backend" / "src" / Path(*parts).with_suffix(".py")


def dependency_policy_bootstrap_source_path(root: Path, pyproject_text: str) -> Path | None:
    """Resolve the one source module authorized for pre-environment dependency policy."""

    source = project_script_source_path(
        root, pyproject_text, DEPENDENCY_POLICY_BOOTSTRAP_CAPABILITY
    )
    if source is None:
        return None
    repository_tooling = root / "backend" / "src" / "oh_goon" / "repository_tooling"
    return source if source.is_relative_to(repository_tooling) else None


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class PythonSemanticFacts:
    """Representation-invariant Python source facts used by architecture checks.

    Imported module/function aliases and simple callable aliases are resolved to their
    qualified semantic names. Comments and quote style never enter this projection.
    The projection is intentionally bounded to static Python structure; architecture
    checks should consume these facts instead of searching raw source fragments.
    """

    calls: tuple[tuple[str, ast.Call], ...]
    references: frozenset[str]
    imports: frozenset[str]
    string_literals: tuple[str, ...]
    classes: tuple[ast.ClassDef, ...]


def _scope_nodes(statements: list[ast.stmt]) -> tuple[ast.AST, ...]:
    """Return nodes owned by one lexical scope without entering nested scopes."""

    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in statements:
        visit(statement)
    return tuple(nodes)


def _qualified_symbol(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_symbol(node.value, aliases)
        return f"{prefix}.{node.attr}" if prefix else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        prefix = _qualified_symbol(node.args[0], aliases)
        return f"{prefix}.{node.args[1].value}" if prefix else None
    return None


def _scope_aliases(
    statements: list[ast.stmt], inherited: dict[str, str]
) -> tuple[dict[str, str], set[str]]:
    aliases = dict(inherited)
    imported: set[str] = set()
    nodes = _scope_nodes(statements)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                qualified = alias.name if alias.asname else bound
                aliases[bound] = qualified
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                qualified = f"{node.module}.{alias.name}"
                aliases[alias.asname or alias.name] = qualified
                imported.add(qualified)

    assignments: list[tuple[ast.AST, ast.AST]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            assignments.extend((target, node.value) for target in node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((node.target, node.value))
        elif isinstance(node, ast.NamedExpr):
            assignments.append((node.target, node.value))

    changed = True
    while changed:
        changed = False
        for target, value in assignments:
            if not isinstance(target, ast.Name):
                continue
            if target.id in aliases:
                continue
            qualified = _qualified_symbol(value, aliases)
            if qualified is None:
                continue
            aliases[target.id] = qualified
            changed = True
    return aliases, imported


def python_semantic_facts(path: Path) -> PythonSemanticFacts:
    """Project Python semantics without granting comments/private spellings authority."""

    tree = python_ast(path)
    calls: list[tuple[str, ast.Call]] = []
    references: set[str] = set()
    imports: set[str] = set()
    strings: list[str] = []
    classes: list[ast.ClassDef] = []

    def inspect_scope(statements: list[ast.stmt], inherited: dict[str, str]) -> None:
        aliases, scope_imports = _scope_aliases(statements, inherited)
        imports.update(scope_imports)
        nodes = _scope_nodes(statements)
        for node in nodes:
            if isinstance(node, ast.Call):
                qualified = _qualified_symbol(node.func, aliases)
                if qualified is not None:
                    calls.append((qualified, node))
            elif isinstance(node, (ast.Name, ast.Attribute)):
                qualified = _qualified_symbol(node, aliases)
                if qualified is not None:
                    references.add(qualified)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)

        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inspect_scope(statement.body, aliases)
            elif isinstance(statement, ast.ClassDef):
                classes.append(statement)
                inspect_scope(statement.body, aliases)
            else:
                for node in _scope_nodes([statement]):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        inspect_scope(node.body, aliases)
                    elif isinstance(node, ast.ClassDef):
                        classes.append(node)
                        inspect_scope(node.body, aliases)

    inspect_scope(tree.body, {})
    return PythonSemanticFacts(
        calls=tuple(calls),
        references=frozenset(references),
        imports=frozenset(imports),
        string_literals=tuple(strings),
        classes=tuple(classes),
    )


_PYTHON_SOURCE_SNAPSHOT: ContextVar[dict[Path, ast.Module] | None] = ContextVar(
    "architecture_python_source_snapshot", default=None
)


@contextmanager
def python_source_snapshot() -> Iterator[None]:
    """Read and parse each source once for one architecture invocation."""
    if _PYTHON_SOURCE_SNAPSHOT.get() is not None:
        yield
        return
    token = _PYTHON_SOURCE_SNAPSHOT.set({})
    try:
        yield
    finally:
        _PYTHON_SOURCE_SNAPSHOT.reset(token)


def python_ast(path: Path) -> ast.Module:
    """Return exact invocation-local syntax; no timestamp-based reusable proof."""
    resolved = path.resolve()
    snapshot = _PYTHON_SOURCE_SNAPSHOT.get()
    if snapshot is None:
        return ast.parse(resolved.read_text(encoding="utf-8"), filename=str(resolved))
    if resolved not in snapshot:
        snapshot[resolved] = ast.parse(resolved.read_text(encoding="utf-8"), filename=str(resolved))
    return snapshot[resolved]
