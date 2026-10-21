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


def _scope_nodes(
    statements: list[ast.stmt],
    *,
    definition_expressions: bool = False,
    comprehension_scopes: bool = False,
) -> tuple[ast.AST, ...]:
    """Return nodes owned by one lexical scope without entering nested scopes."""

    nodes: list[ast.AST] = []

    def visit(node: ast.AST) -> None:
        nodes.append(node)
        if comprehension_scopes and isinstance(
            node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
        ):
            # Python evaluates only the first iterable in the containing
            # scope. Targets and the remaining expressions belong to the
            # comprehension's own lexical scope.
            visit(node.generators[0].iter)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            if definition_expressions:
                expressions: list[ast.expr | None] = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    expressions.extend((*node.args.defaults, *node.args.kw_defaults))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    expressions.extend(node.decorator_list)
                if isinstance(node, ast.ClassDef):
                    expressions.extend(node.bases)
                    expressions.extend(keyword.value for keyword in node.keywords)
                for expression in expressions:
                    if expression is not None:
                        visit(expression)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    for statement in statements:
        visit(statement)
    return tuple(nodes)


def _qualified_symbol(
    node: ast.AST,
    aliases: dict[str, str | None],
    *,
    imported_only: bool = False,
) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, None if imported_only else node.id)
    if isinstance(node, ast.Attribute):
        prefix = _qualified_symbol(node.value, aliases, imported_only=imported_only)
        return f"{prefix}.{node.attr}" if prefix else None
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and (not imported_only or "getattr" not in aliases)
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and isinstance(node.args[1].value, str)
    ):
        prefix = _qualified_symbol(node.args[0], aliases, imported_only=imported_only)
        return f"{prefix}.{node.args[1].value}" if prefix else None
    return None


def _scope_aliases(
    statements: list[ast.stmt],
    inherited: dict[str, str | None],
    *,
    imported_only: bool = False,
) -> tuple[dict[str, str | None], set[str]]:
    aliases = dict(inherited)
    imported: set[str] = set()
    nodes = _scope_nodes(statements, comprehension_scopes=imported_only)
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                qualified = alias.name if alias.asname else bound
                aliases[bound] = qualified
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if not node.module and not imported_only:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if imported_only and (node.level or not node.module):
                    aliases[alias.asname or alias.name] = None
                    imported.add(
                        "." * node.level + f"{node.module + '.' if node.module else ''}{alias.name}"
                    )
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

    if imported_only:
        # Rebinding an import is a shadow, not a second import of the same
        # native module. An alias must be its name's sole lexical binding;
        # other declarations cannot be overwritten by alias resolution.
        counts: dict[str, int] = {}
        for node in nodes:
            if isinstance(node, ast.Import):
                names = tuple(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names = tuple(
                    alias.asname or alias.name for alias in node.names if alias.name != "*"
                )
            else:
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    bound = node.id
                elif isinstance(node, ast.ExceptHandler) and node.name:
                    bound = node.name
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bound = node.name
                else:
                    continue
                aliases[bound] = None
                names = (bound,)
            for name in names:
                counts[name] = counts.get(name, 0) + 1
        assignments = [
            (target, value)
            for target, value in assignments
            if isinstance(target, ast.Name) and counts[target.id] == 1
        ]

    changed = True
    while changed:
        changed = False
        for target, value in assignments:
            if not isinstance(target, ast.Name):
                continue
            if target.id in aliases and (not imported_only or aliases[target.id] is not None):
                continue
            resolved = _qualified_symbol(value, aliases, imported_only=imported_only)
            if resolved is None:
                continue
            aliases[target.id] = resolved
            changed = True
    return aliases, imported


def python_semantic_facts(path: Path, *, imported_only: bool = False) -> PythonSemanticFacts:
    """Project Python semantics without granting comments/private spellings authority.

    ``imported_only`` requires a static import binding, including unambiguous
    callable aliases and callable defaults. Parameters and local declarations
    shadow inherited imports. Dynamic imports and arbitrary data flow are not
    inferred. A direct module-local helper call may transport an imported
    argument across one edge; recursive/multihop argument flow is not inferred.
    Callers selecting this mode must define their supported APIs.
    """

    tree = python_ast(path)
    calls: list[tuple[str, ast.Call]] = []
    references: set[str] = set()
    imports: set[str] = set()
    strings: list[str] = []
    classes: list[ast.ClassDef] = []

    def function_bindings(
        function: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
        inherited: dict[str, str | None],
        *,
        default_bindings: dict[str, str | None] | None = None,
    ) -> dict[str, str | None]:
        bindings = dict(inherited)
        definition_bindings = inherited if default_bindings is None else default_bindings
        arguments = function.args
        positional = [*arguments.posonlyargs, *arguments.args]
        parameters = [*positional, *arguments.kwonlyargs]
        parameters.extend(arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None)
        bindings.update((arg.arg, None) for arg in parameters)
        defaults = [
            *zip(
                positional[len(positional) - len(arguments.defaults) :],
                arguments.defaults,
                strict=True,
            ),
            *zip(arguments.kwonlyargs, arguments.kw_defaults, strict=True),
        ]
        for arg, default in defaults:
            if default is not None:
                bindings[arg.arg] = _qualified_symbol(
                    default, definition_bindings, imported_only=True
                )
        if not isinstance(function, ast.Lambda):
            bindings.update(function_inputs.get(function, {}))
        return bindings

    function_inputs: dict[ast.FunctionDef | ast.AsyncFunctionDef, dict[str, str]] = {}
    if imported_only:
        # Native modules may be explicitly passed to a module-local helper
        # (for example cli_start's imported uvicorn delegate). Follow only one
        # direct call edge from imports/defaults, never speculative caller
        # state or recursive argument data flow.
        module_aliases, _imports = _scope_aliases(tree.body, {}, imported_only=True)
        definitions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        candidates: dict[str, dict[str, set[str]]] = {}
        scopes: list[tuple[list[ast.stmt], dict[str, str | None], set[str]]] = [
            (tree.body, module_aliases, set())
        ]
        scopes.extend(
            (
                node.body,
                function_bindings(node, module_aliases),
                {
                    arg.arg
                    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
                }
                | {arg.arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None},
            )
            for node in definitions.values()
        )
        for statements, inherited, parameters in scopes:
            aliases, _imports = _scope_aliases(statements, inherited, imported_only=True)
            nodes = _scope_nodes(statements, comprehension_scopes=True)
            shadowed = {
                node.id
                for node in nodes
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
            }
            shadowed.update(
                node.name
                for node in nodes
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and statements is not tree.body
            )
            shadowed.update(name for name, binding in inherited.items() if binding is not None)
            shadowed.update(parameters)
            for node in nodes:
                if isinstance(node, ast.Import):
                    shadowed.update(
                        alias.asname or alias.name.split(".", 1)[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom):
                    shadowed.update(alias.asname or alias.name for alias in node.names)
            for node in nodes:
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                target = definitions.get(node.func.id)
                if target is None or node.func.id in shadowed:
                    continue
                positional = [*target.args.posonlyargs, *target.args.args]
                supplied = [
                    (arg.arg, value) for arg, value in zip(positional, node.args, strict=False)
                ]
                supplied.extend(
                    (keyword.arg, keyword.value) for keyword in node.keywords if keyword.arg
                )
                for parameter, value in supplied:
                    imported = _qualified_symbol(value, aliases, imported_only=True)
                    if imported is not None:
                        candidates.setdefault(target.name, {}).setdefault(parameter, set()).add(
                            imported
                        )
        function_inputs = {
            definitions[name]: {
                parameter: next(iter(bindings))
                for parameter, bindings in parameters.items()
                if len(bindings) == 1
            }
            for name, parameters in candidates.items()
        }

    def inspect_scope(
        statements: list[ast.stmt],
        inherited: dict[str, str | None],
        *,
        class_enclosing: dict[str, str | None] | None = None,
    ) -> None:
        aliases, scope_imports = _scope_aliases(statements, inherited, imported_only=imported_only)
        # A class suite evaluates against its own aliases, but its nested
        # lexical scopes close over the enclosing function/module instead.
        nested_bindings = aliases if class_enclosing is None else class_enclosing
        imports.update(scope_imports)
        nodes = _scope_nodes(
            statements,
            definition_expressions=imported_only,
            comprehension_scopes=imported_only,
        )
        for node in nodes:
            if isinstance(node, ast.Call):
                qualified = _qualified_symbol(node.func, aliases, imported_only=imported_only)
                if qualified is not None:
                    calls.append((qualified, node))
            elif isinstance(node, (ast.Name, ast.Attribute)):
                qualified = _qualified_symbol(node, aliases, imported_only=imported_only)
                if qualified is not None:
                    references.add(qualified)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)
            elif imported_only and isinstance(node, ast.Lambda):
                inspect_scope(
                    [ast.Expr(value=node.body)],
                    function_bindings(node, nested_bindings, default_bindings=aliases),
                )
            elif imported_only and isinstance(
                node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
            ):
                local = dict(nested_bindings)
                for generator in node.generators:
                    for target in ast.walk(generator.target):
                        if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                            local[target.id] = None
                expressions = [generator.iter for generator in node.generators[1:]]
                expressions.extend(
                    condition for generator in node.generators for condition in generator.ifs
                )
                expressions.extend(
                    (node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)
                )
                inspect_scope([ast.Expr(value=value) for value in expressions], local)

        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                inspect_scope(
                    statement.body,
                    function_bindings(statement, nested_bindings, default_bindings=aliases)
                    if imported_only
                    else aliases,
                )
            elif isinstance(statement, ast.ClassDef):
                classes.append(statement)
                inspect_scope(
                    statement.body,
                    nested_bindings,
                    class_enclosing=nested_bindings if imported_only else None,
                )
            else:
                for node in _scope_nodes([statement], comprehension_scopes=imported_only):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        inspect_scope(
                            node.body,
                            function_bindings(node, nested_bindings, default_bindings=aliases)
                            if imported_only
                            else aliases,
                        )
                    elif isinstance(node, ast.ClassDef):
                        classes.append(node)
                        inspect_scope(
                            node.body,
                            nested_bindings,
                            class_enclosing=nested_bindings if imported_only else None,
                        )

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
