from __future__ import annotations

import ast
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    python_ast,
    python_semantic_facts,
)


def _literal_string_argv(
    node: ast.AST, bindings: dict[str, tuple[str, ...]]
) -> tuple[str, ...] | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for item in node.elts:
        if isinstance(item, ast.Starred):
            expanded = _literal_string_argv(item.value, bindings)
            if expanded is None:
                return None
            values.extend(expanded)
            continue
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            return None
        values.append(item.value)
    return tuple(values)


def _literal_string_prefix(node: ast.AST, bindings: dict[str, ast.AST]) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        bound = bindings.get(node.id)
        return _literal_string_prefix(bound, bindings) if bound is not None else ()
    if not isinstance(node, (ast.List, ast.Tuple)):
        return ()
    values: list[str] = []
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            break
        values.append(item.value)
    return tuple(values)


def _native_command_wrapper_names(tree: ast.AST) -> set[str]:
    wrappers: set[str] = set()
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        if not function.args.args:
            continue
        argv_name = function.args.args[0].arg
        if any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and bool(node.args)
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == argv_name
            for node in ast.walk(function)
        ):
            wrappers.add(function.name)
    return wrappers


def _native_command_prefixes(path: Path) -> tuple[tuple[str, ...], ...]:
    tree = python_ast(path)
    bindings: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = node.value

    wrappers = _native_command_wrapper_names(tree)
    prefixes: list[tuple[str, ...]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        is_wrapper_call = isinstance(node.func, ast.Name) and node.func.id in wrappers
        is_direct_subprocess = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        )
        if not (is_wrapper_call or is_direct_subprocess):
            continue
        prefix = _literal_string_prefix(node.args[0], bindings)
        if prefix:
            prefixes.append(prefix)
    return tuple(prefixes)


def _native_compose_version_probe_exists(path: Path) -> bool:
    tree = python_ast(path)
    bindings: dict[str, tuple[str, ...]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is None:
                continue
            resolved = _literal_string_argv(value, bindings)
            if resolved is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = resolved

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "run"
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        argv = _literal_string_argv(node.args[0], bindings)
        if argv is not None and argv[:3] == ("docker", "compose", "version"):
            return True
    return False


def _function_node(tree: ast.AST, name: str) -> ast.FunctionDef | None:
    return next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ),
        None,
    )


def _function_bindings(function: ast.FunctionDef) -> dict[str, ast.AST]:
    bindings: dict[str, ast.AST] = {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = node.value
    return bindings


def _static_string_tokens(
    node: ast.AST,
    bindings: dict[str, ast.AST],
    *,
    resolving: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        if node.id in resolving:
            return ()
        bound = bindings.get(node.id)
        if bound is None:
            return ()
        return _static_string_tokens(
            bound,
            bindings,
            resolving=resolving | {node.id},
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.Starred):
        return _static_string_tokens(node.value, bindings, resolving=resolving)
    if isinstance(node, (ast.List, ast.Tuple)):
        return tuple(
            token
            for item in node.elts
            for token in _static_string_tokens(item, bindings, resolving=resolving)
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return (
            *_static_string_tokens(node.left, bindings, resolving=resolving),
            *_static_string_tokens(node.right, bindings, resolving=resolving),
        )
    return ()


def _native_validation_commands(
    tree: ast.AST,
    function_name: str,
) -> tuple[tuple[str, ...], ...]:
    function = _function_node(tree, function_name)
    if function is None:
        return ()
    bindings = _function_bindings(function)
    commands: list[tuple[str, ...]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "_native_check"):
            continue
        tokens = _static_string_tokens(node.args[0], bindings)
        if tokens:
            commands.append(tokens)
    return tuple(commands)


def _direct_dependency_lock_content_parsing(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            if any(module == "tomllib" for module in modules):
                return True
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "open",
            "read_bytes",
            "read_text",
        }:
            return True
    return False


def _check_lock_authority(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    authoring = root / "backend/src/oh_goon/repository_tooling/dependency_lock_authoring.py"
    if authoring.exists():
        violations.append(
            Violation(authoring, "dependency lock authoring must be owned directly by uv/npm")
        )
    checks = root / "backend/src/oh_goon/repository_tooling/dependency_locks.py"
    if not checks.is_file():
        return violations
    tree = python_ast(checks)
    backend_commands = _native_validation_commands(tree, "backend_dependency_lock_issues")
    backend_native = any(
        command
        and command[0] == "uv"
        and "lock" in command
        and "--project" in command
        and "--check" in command
        and "--check-exists" not in command
        for command in backend_commands
    )
    if not backend_native:
        violations.append(
            Violation(
                checks,
                "backend dependency validation must delegate stale-lock authority to native uv lock --check for the backend project",
            )
        )
    frontend_commands = _native_validation_commands(tree, "frontend_dependency_lock_issues")
    frontend_native = any(
        command
        and command[0] == "npm"
        and "ci" in command
        and "--package-lock-only" in command
        and "--dry-run" in command
        for command in frontend_commands
    )
    if not frontend_native:
        violations.append(
            Violation(
                checks,
                "frontend dependency validation must delegate lock authority to native npm ci --package-lock-only --dry-run",
            )
        )
    if _direct_dependency_lock_content_parsing(tree):
        violations.append(
            Violation(
                checks,
                "dependency validation must not read or parse lock contents in parallel with native uv/npm authority",
            )
        )
    ignore = root / ".gitignore"
    if ignore.is_file() and "backend/uv.lock" in ignore.read_text(encoding="utf-8").splitlines():
        violations.append(Violation(ignore, "committed backend/uv.lock must not be ignored"))
    return violations


def _check_dependency_privacy(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    privacy_module = "oh_goon.repository_tooling.public_dependency_privacy"
    boundary = root / "backend/src/oh_goon/repository_tooling/public_dependency_privacy.py"
    if not boundary.is_file():
        violations.append(
            Violation(
                boundary, "public/private dependency privacy boundary must be machine-enforced"
            )
        )
        return violations

    consumers = (
        root / "backend/src/oh_goon/repository_tooling/dependency_locks.py",
        root / "backend/src/oh_goon/repository_tooling/release/package_slim.py",
        root / "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py",
        root / "backend/src/oh_goon/repository_tooling/release/accept_container.py",
    )
    for consumer in consumers:
        if not consumer.is_file():
            violations.append(
                Violation(
                    consumer,
                    "dependency privacy semantic consumer is missing",
                )
            )
            continue
        tree = python_ast(consumer)
        consumes_privacy_owner = any(
            (isinstance(node, ast.ImportFrom) and node.module == privacy_module)
            or (
                isinstance(node, ast.Import)
                and any(alias.name == privacy_module for alias in node.names)
            )
            for node in ast.walk(tree)
        )
        if not consumes_privacy_owner:
            violations.append(
                Violation(
                    consumer,
                    "dependency privacy surface must consume the public dependency privacy semantic owner",
                )
            )
    return violations


_LOCK_SEMANTIC_PARSERS = {"json", "toml", "tomllib"}
_LOCK_SEMANTIC_PARSER_CALLS = {"load", "loads"}


def _contains_lock_identity(node: ast.AST, *, lock_names: frozenset[str]) -> bool:
    return any(
        isinstance(child, ast.Constant)
        and isinstance(child.value, str)
        and any(
            child.value == lock_name or child.value.endswith("/" + lock_name)
            for lock_name in lock_names
        )
        for child in ast.walk(node)
    )


class _LockSemanticFlowAnalyzer:
    """Prove lock bytes/streams never flow into a parallel JSON/TOML parser.

    Exact lock paths are protocol identities. Their bytes may be transported,
    compared, restored, or hashed as evidence. Dependency semantics remain owned
    by native uv/npm, so only a data flow from those lock identities into a
    semantic document parser is forbidden. Private variable/helper/import names
    are deliberately irrelevant.
    """

    def __init__(self, tree: ast.Module, *, lock_names: frozenset[str]) -> None:
        self.tree = tree
        self.lock_names = lock_names
        self.functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.module_aliases: dict[str, str] = {}
        self.parser_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _LOCK_SEMANTIC_PARSERS:
                        self.module_aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module in _LOCK_SEMANTIC_PARSERS:
                for alias in node.names:
                    if alias.name in _LOCK_SEMANTIC_PARSER_CALLS:
                        self.parser_aliases.add(alias.asname or alias.name)
        self.globals = self._global_taint()
        self.violation = False
        self._active: set[tuple[str, tuple[bool, ...]]] = set()
        self._memo: dict[tuple[str, tuple[bool, ...]], bool] = {}

    def _global_taint(self) -> dict[str, bool]:
        taint: dict[str, bool] = {}
        assignments: list[tuple[ast.expr, ast.AST]] = []
        for node in self.tree.body:
            if isinstance(node, ast.Assign):
                assignments.extend((target, node.value) for target in node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                assignments.append((node.target, node.value))
        changed = True
        while changed:
            changed = False
            for target, value in assignments:
                value_tainted = _contains_lock_identity(value, lock_names=self.lock_names) or any(
                    isinstance(child, ast.Name) and taint.get(child.id, False)
                    for child in ast.walk(value)
                )
                if not value_tainted:
                    continue
                for name in self._target_names(target):
                    if not taint.get(name, False):
                        taint[name] = True
                        changed = True
        return taint

    @staticmethod
    def _target_names(target: ast.AST) -> tuple[str, ...]:
        if isinstance(target, ast.Name):
            return (target.id,)
        if isinstance(target, (ast.Tuple, ast.List)):
            return tuple(
                name
                for item in target.elts
                for name in _LockSemanticFlowAnalyzer._target_names(item)
            )
        return ()

    def _bind(self, target: ast.AST, tainted: bool, env: dict[str, bool]) -> None:
        for name in self._target_names(target):
            env[name] = tainted

    def _is_parser_call(self, node: ast.Call) -> bool:
        if isinstance(node.func, ast.Name):
            return node.func.id in self.parser_aliases
        if not isinstance(node.func, ast.Attribute):
            return False
        if node.func.attr not in _LOCK_SEMANTIC_PARSER_CALLS:
            return False
        return isinstance(node.func.value, ast.Name) and node.func.value.id in self.module_aliases

    def _expr(self, node: ast.AST | None, env: dict[str, bool]) -> bool:
        if node is None:
            return False
        if _contains_lock_identity(node, lock_names=self.lock_names):
            return True
        if isinstance(node, ast.Name):
            return env.get(node.id, self.globals.get(node.id, False))
        if isinstance(node, ast.Constant):
            return False
        if isinstance(node, ast.Call):
            argument_taint = [self._expr(argument, env) for argument in node.args]
            argument_taint.extend(self._expr(keyword.value, env) for keyword in node.keywords)
            receiver_taint = (
                self._expr(node.func.value, env) if isinstance(node.func, ast.Attribute) else False
            )
            if self._is_parser_call(node):
                if any(argument_taint):
                    self.violation = True
                return any(argument_taint)
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                return self._function(node.func.id, tuple(argument_taint))
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                return bool(argument_taint and argument_taint[0])
            if isinstance(node.func, ast.Attribute) and receiver_taint:
                if node.func.attr in {
                    "open",
                    "read",
                    "read_bytes",
                    "read_text",
                    "decode",
                    "strip",
                    "lstrip",
                    "rstrip",
                }:
                    return True
            return False
        if isinstance(node, ast.Attribute):
            return self._expr(node.value, env)
        if isinstance(node, ast.Subscript):
            return self._expr(node.value, env) or self._expr(node.slice, env)
        if isinstance(node, ast.NamedExpr):
            tainted = self._expr(node.value, env)
            self._bind(node.target, tainted, env)
            return tainted
        return any(self._expr(child, env) for child in ast.iter_child_nodes(node))

    def _statements(self, statements: list[ast.stmt], env: dict[str, bool]) -> bool:
        returned = False
        for statement in statements:
            if isinstance(statement, ast.Assign):
                tainted = self._expr(statement.value, env)
                for target in statement.targets:
                    self._bind(target, tainted, env)
            elif isinstance(statement, ast.AnnAssign):
                self._bind(statement.target, self._expr(statement.value, env), env)
            elif isinstance(statement, ast.AugAssign):
                tainted = self._expr(statement.target, env) or self._expr(statement.value, env)
                self._bind(statement.target, tainted, env)
            elif isinstance(statement, ast.Expr):
                self._expr(statement.value, env)
            elif isinstance(statement, ast.Return):
                returned = self._expr(statement.value, env) or returned
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                nested = dict(env)
                for item in statement.items:
                    tainted = self._expr(item.context_expr, nested)
                    if item.optional_vars is not None:
                        self._bind(item.optional_vars, tainted, nested)
                returned = self._statements(statement.body, nested) or returned
                for name, value in nested.items():
                    env[name] = env.get(name, False) or value
            elif isinstance(statement, ast.If):
                self._expr(statement.test, env)
                branches = []
                for body in (statement.body, statement.orelse):
                    nested = dict(env)
                    returned = self._statements(body, nested) or returned
                    branches.append(nested)
                for name in set().union(*(branch.keys() for branch in branches)):
                    env[name] = env.get(name, False) or any(
                        branch.get(name, False) for branch in branches
                    )
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                nested = dict(env)
                self._bind(statement.target, self._expr(statement.iter, env), nested)
                returned = self._statements(statement.body, nested) or returned
                returned = self._statements(statement.orelse, nested) or returned
                for name, value in nested.items():
                    env[name] = env.get(name, False) or value
            elif isinstance(statement, ast.While):
                self._expr(statement.test, env)
                nested = dict(env)
                returned = self._statements(statement.body, nested) or returned
                returned = self._statements(statement.orelse, nested) or returned
                for name, value in nested.items():
                    env[name] = env.get(name, False) or value
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                bodies = [statement.body, statement.orelse, statement.finalbody]
                bodies.extend(handler.body for handler in statement.handlers)
                for body in bodies:
                    nested = dict(env)
                    returned = self._statements(body, nested) or returned
                    for name, value in nested.items():
                        env[name] = env.get(name, False) or value
            else:
                for child in ast.iter_child_nodes(statement):
                    if isinstance(child, ast.expr):
                        self._expr(child, env)
        return returned

    def _function(self, name: str, argument_taint: tuple[bool, ...]) -> bool:
        function = self.functions[name]
        parameters = [
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        ]
        key = (name, argument_taint)
        if key in self._memo:
            return self._memo[key]
        if key in self._active:
            return False
        self._active.add(key)
        try:
            env = dict(self.globals)
            for index, parameter in enumerate(parameters):
                env[parameter.arg] = argument_taint[index] if index < len(argument_taint) else False
            result = self._statements(function.body, env)
            self._memo[key] = result
            return result
        finally:
            self._active.remove(key)

    def has_semantic_parser_flow(self) -> bool:
        module_env = dict(self.globals)
        self._statements(
            [
                node
                for node in self.tree.body
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ],
            module_env,
        )
        for name, function in self.functions.items():
            parameter_count = (
                len(function.args.posonlyargs)
                + len(function.args.args)
                + len(function.args.kwonlyargs)
            )
            self._function(name, (False,) * parameter_count)
        return self.violation


def _lock_semantic_parser_flow(path: Path, *, lock_names: frozenset[str]) -> bool:
    return _LockSemanticFlowAnalyzer(
        python_ast(path),
        lock_names=lock_names,
    ).has_semantic_parser_flow()


_PROJECT_DECLARATION = 1
_PROJECT_OBSERVED_VERSION = 2
_PROJECT_DECLARATION_KEYS = frozenset({"requires-python", "dependencies"})
_PROJECT_VERSION_SOURCES = frozenset(
    {
        "sys.version",
        "sys.version_info",
        "sys.hexversion",
        "sys.implementation.version",
        "platform.python_version",
        "platform.python_version_tuple",
        "importlib.metadata.version",
    }
)


class _ProjectCompatibilityDecisionAnalyzer:
    """Reject local version-compatibility verdicts in the project observer.

    The project capability may transport declaration text and observed runtime
    versions, but native uv is the only compatibility decision authority.  This
    analysis therefore follows those values through renamed locals and local
    helpers and rejects version comparisons or truth decisions derived from an
    observed version.  Private helper/import spellings are deliberately not
    evidence.
    """

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    self.aliases[bound] = alias.name if alias.asname else bound
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    qualified = f"{module}.{alias.name}" if module else alias.name
                    self.aliases[alias.asname or alias.name] = qualified
        self.globals: dict[str, int] = {}
        self.violation = False
        self._active: set[tuple[str, tuple[int, ...]]] = set()
        self._memo: dict[tuple[str, tuple[int, ...]], int] = {}

    @staticmethod
    def _target_names(target: ast.AST) -> tuple[str, ...]:
        if isinstance(target, ast.Name):
            return (target.id,)
        if isinstance(target, (ast.Tuple, ast.List)):
            return tuple(
                name
                for item in target.elts
                for name in _ProjectCompatibilityDecisionAnalyzer._target_names(item)
            )
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
            return (target.value.id,)
        return ()

    def _bind(self, target: ast.AST, mask: int, env: dict[str, int]) -> None:
        for name in self._target_names(target):
            env[name] = env.get(name, 0) | mask

    def _qualified_name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            prefix = self._qualified_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else None
        return None

    @staticmethod
    def _declaration_key(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in _PROJECT_DECLARATION_KEYS
        )

    def _observed_source(self, node: ast.AST) -> bool:
        qualified = self._qualified_name(node)
        return qualified in _PROJECT_VERSION_SOURCES if qualified is not None else False

    def _decision_mask(self, mask: int) -> None:
        if mask & _PROJECT_OBSERVED_VERSION:
            self.violation = True

    def _expr(self, node: ast.AST | None, env: dict[str, int]) -> int:
        if node is None:
            return 0
        if isinstance(node, ast.Name):
            if self._observed_source(node):
                return _PROJECT_OBSERVED_VERSION
            return env.get(node.id, self.globals.get(node.id, 0))
        if isinstance(node, ast.Constant):
            return 0
        if isinstance(node, ast.Attribute):
            if self._observed_source(node):
                return _PROJECT_OBSERVED_VERSION
            return self._expr(node.value, env)
        if isinstance(node, ast.Subscript):
            mask = self._expr(node.value, env) | self._expr(node.slice, env)
            if self._declaration_key(node.slice):
                mask |= _PROJECT_DECLARATION
            return mask
        if isinstance(node, ast.Call):
            if self._observed_source(node.func):
                return _PROJECT_OBSERVED_VERSION
            argument_masks = [self._expr(argument, env) for argument in node.args]
            argument_masks.extend(self._expr(keyword.value, env) for keyword in node.keywords)
            receiver_mask = (
                self._expr(node.func.value, env) if isinstance(node.func, ast.Attribute) else 0
            )
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and self._declaration_key(node.args[0])
            ):
                receiver_mask |= _PROJECT_DECLARATION
            if isinstance(node.func, ast.Name) and node.func.id in self.functions:
                return self._function(node.func.id, tuple(argument_masks))
            argument_mask = 0
            for mask in argument_masks:
                argument_mask |= mask
            if (
                receiver_mask & _PROJECT_DECLARATION and argument_mask & _PROJECT_OBSERVED_VERSION
            ) or (
                receiver_mask & _PROJECT_OBSERVED_VERSION and argument_mask & _PROJECT_DECLARATION
            ):
                self.violation = True
            return receiver_mask | argument_mask
        if isinstance(node, ast.Compare):
            mask = self._expr(node.left, env)
            for comparator in node.comparators:
                mask |= self._expr(comparator, env)
            self._decision_mask(mask)
            return mask
        if isinstance(node, ast.IfExp):
            test_mask = self._expr(node.test, env)
            self._decision_mask(test_mask)
            return test_mask | self._expr(node.body, env) | self._expr(node.orelse, env)
        if isinstance(node, ast.BoolOp):
            mask = 0
            for value in node.values:
                mask |= self._expr(value, env)
            if mask & _PROJECT_DECLARATION and mask & _PROJECT_OBSERVED_VERSION:
                self.violation = True
            return mask
        if isinstance(node, ast.NamedExpr):
            mask = self._expr(node.value, env)
            self._bind(node.target, mask, env)
            return mask
        if isinstance(node, ast.Dict):
            # A report may legitimately place declaration and observation fields
            # beside one another.  Inspect each expression for nested decisions,
            # but do not turn the aggregate report into a compatibility value.
            for key, value in zip(node.keys, node.values, strict=True):
                self._expr(key, env)
                self._expr(value, env)
            return 0
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            mask = 0
            for item in node.elts:
                mask |= self._expr(item, env)
            return mask
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            nested = dict(env)
            for generator in node.generators:
                iterator_mask = self._expr(generator.iter, nested)
                self._bind(generator.target, iterator_mask, nested)
                for condition in generator.ifs:
                    condition_mask = self._expr(condition, nested)
                    self._decision_mask(condition_mask)
            return self._expr(node.elt, nested)
        if isinstance(node, ast.DictComp):
            nested = dict(env)
            for generator in node.generators:
                iterator_mask = self._expr(generator.iter, nested)
                self._bind(generator.target, iterator_mask, nested)
                for condition in generator.ifs:
                    condition_mask = self._expr(condition, nested)
                    self._decision_mask(condition_mask)
            self._expr(node.key, nested)
            return self._expr(node.value, nested)
        mask = 0
        for child in ast.iter_child_nodes(node):
            mask |= self._expr(child, env)
        return mask

    def _statements(self, statements: list[ast.stmt], env: dict[str, int]) -> int:
        returned = 0
        for statement in statements:
            if isinstance(statement, ast.Assign):
                mask = self._expr(statement.value, env)
                for target in statement.targets:
                    self._bind(target, mask, env)
            elif isinstance(statement, ast.AnnAssign):
                self._bind(statement.target, self._expr(statement.value, env), env)
            elif isinstance(statement, ast.AugAssign):
                mask = self._expr(statement.target, env) | self._expr(statement.value, env)
                self._bind(statement.target, mask, env)
            elif isinstance(statement, ast.Expr):
                self._expr(statement.value, env)
            elif isinstance(statement, ast.Return):
                returned |= self._expr(statement.value, env)
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            elif isinstance(statement, ast.Assert):
                mask = self._expr(statement.test, env)
                self._decision_mask(mask)
                self._expr(statement.msg, env)
            elif isinstance(statement, ast.If):
                test_mask = self._expr(statement.test, env)
                self._decision_mask(test_mask)
                branches: list[dict[str, int]] = []
                for body in (statement.body, statement.orelse):
                    nested = dict(env)
                    returned |= self._statements(body, nested)
                    branches.append(nested)
                for name in set().union(*(branch.keys() for branch in branches)):
                    env[name] = env.get(name, 0)
                    for branch in branches:
                        env[name] |= branch.get(name, 0)
            elif isinstance(statement, ast.While):
                test_mask = self._expr(statement.test, env)
                self._decision_mask(test_mask)
                nested = dict(env)
                returned |= self._statements(statement.body, nested)
                returned |= self._statements(statement.orelse, nested)
                for name, mask in nested.items():
                    env[name] = env.get(name, 0) | mask
            elif isinstance(statement, (ast.For, ast.AsyncFor)):
                nested = dict(env)
                self._bind(statement.target, self._expr(statement.iter, env), nested)
                returned |= self._statements(statement.body, nested)
                returned |= self._statements(statement.orelse, nested)
                for name, mask in nested.items():
                    env[name] = env.get(name, 0) | mask
            elif isinstance(statement, (ast.With, ast.AsyncWith)):
                nested = dict(env)
                for item in statement.items:
                    mask = self._expr(item.context_expr, nested)
                    if item.optional_vars is not None:
                        self._bind(item.optional_vars, mask, nested)
                returned |= self._statements(statement.body, nested)
                for name, mask in nested.items():
                    env[name] = env.get(name, 0) | mask
            elif isinstance(statement, (ast.Try, ast.TryStar)):
                bodies = [statement.body, statement.orelse, statement.finalbody]
                bodies.extend(handler.body for handler in statement.handlers)
                for body in bodies:
                    nested = dict(env)
                    returned |= self._statements(body, nested)
                    for name, mask in nested.items():
                        env[name] = env.get(name, 0) | mask
            else:
                for child in ast.iter_child_nodes(statement):
                    if isinstance(child, ast.expr):
                        self._expr(child, env)
        return returned

    def _function(self, name: str, argument_masks: tuple[int, ...]) -> int:
        function = self.functions[name]
        parameters = [
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        ]
        key = (name, argument_masks)
        if key in self._memo:
            return self._memo[key]
        if key in self._active:
            return 0
        self._active.add(key)
        try:
            env = dict(self.globals)
            for index, parameter in enumerate(parameters):
                env[parameter.arg] = argument_masks[index] if index < len(argument_masks) else 0
            result = self._statements(function.body, env)
            self._memo[key] = result
            return result
        finally:
            self._active.remove(key)

    def has_local_compatibility_decision(self) -> bool:
        self._statements(
            [
                node
                for node in self.tree.body
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ],
            self.globals,
        )
        for name, function in self.functions.items():
            parameter_count = (
                len(function.args.posonlyargs)
                + len(function.args.args)
                + len(function.args.kwonlyargs)
            )
            self._function(name, (0,) * parameter_count)
        return self.violation


def _project_capability_redecides_compatibility(path: Path) -> bool:
    return _ProjectCompatibilityDecisionAnalyzer(
        python_ast(path)
    ).has_local_compatibility_decision()


def _check_browser_dependency_identity(root: Path) -> list[Violation]:
    browser = (
        root / "backend/src/oh_goon/repository_tooling/release/browser_acceptance_container.py"
    )
    if not browser.is_file():
        return []
    if not _lock_semantic_parser_flow(
        browser,
        lock_names=frozenset({"package-lock.json"}),
    ):
        return []
    return [
        Violation(
            browser,
            "browser acceptance must not parse package-lock semantics in parallel with npm; native npm owns lock selection",
        )
    ]


def _check_native_command_policy(root: Path) -> list[Violation]:
    native = root / "backend/src/oh_goon/repository_tooling/native_command.py"
    if not native.is_file():
        return [Violation(native, "native command observer is missing")]
    tree = python_ast(native)
    text = native.read_text(encoding="utf-8")
    violations: list[Violation] = []

    inherits_environment = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "copy"
        and isinstance(node.func.value, ast.Attribute)
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "os"
        and node.func.value.attr == "environ"
        for node in ast.walk(tree)
    )
    if not inherits_environment:
        violations.append(
            Violation(
                native,
                "native command observer must derive child environment from os.environ without depending on a private local name",
            )
        )

    for key in ("UV_DEFAULT_INDEX", "UV_INDEX", "UV_CONFIG_FILE"):
        if key not in text:
            violations.append(
                Violation(
                    native,
                    f"native command observer must preserve operator/native uv environment evidence for {key}",
                )
            )

    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "time"
        and node.func.attr == "sleep"
        for node in ast.walk(tree)
    ):
        violations.append(Violation(native, "Oh-Goon must not implement native-tool retry sleeps"))

    forbidden_popped = {
        "UV_SYSTEM_CERTS",
        "UV_DEFAULT_INDEX",
        "UV_INDEX",
        "UV_CONFIG_FILE",
        "HOME",
        "XDG_CONFIG_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "pop"
            and node.args
        ):
            continue
        key = node.args[0]
        if isinstance(key, ast.Constant) and key.value in forbidden_popped:
            violations.append(
                Violation(
                    native,
                    f"certification must inherit operator/native environment instead of removing {key.value}",
                )
            )
    for token in ("source ~/.bashrc", ". ~/.bashrc"):
        if token in text:
            violations.append(
                Violation(
                    native,
                    "certification must not source shell startup files to manufacture native uv configuration",
                )
            )
    return violations


def _certification_goon_network_violation(goon: Path) -> Violation | None:
    if "host_unrestricted" not in goon.read_text(encoding="utf-8"):
        return None
    return Violation(
        goon,
        "canonical Oh-Goon verification/release Goons must not acquire host-unrestricted egress implicitly; network authority belongs to an explicitly justified semantic owner",
    )


def _check_release_stamp_and_goon_network(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    stamp = root / "backend/src/oh_goon/repository_tooling/release/stamp_release_version.py"
    if stamp.is_file():
        native_prefixes = _native_command_prefixes(stamp)
        for command_prefix in (("uv", "version", "--frozen"), ("npm", "version")):
            if not any(
                prefix[: len(command_prefix)] == command_prefix for prefix in native_prefixes
            ):
                violations.append(
                    Violation(
                        stamp,
                        "release version stamping must delegate to native package-manager command: "
                        + " ".join(command_prefix),
                    )
                )
        if _lock_semantic_parser_flow(
            stamp,
            lock_names=frozenset({"uv.lock", "package-lock.json"}),
        ):
            violations.append(
                Violation(
                    stamp,
                    "release stamping must not manually reinterpret package-manager lock metadata; exact lock bytes may be transported or hashed only",
                )
            )
    goon_root = root / "templates/goons/oh-goon"
    if goon_root.is_dir():
        for goon in goon_root.glob("*/goon.yml"):
            violation = _certification_goon_network_violation(goon)
            if violation is not None:
                violations.append(violation)
    return violations


def _check_native_authority_deduplication(root: Path) -> list[Violation]:
    """Keep native-tool decisions single-owned across release/diagnostic paths."""

    violations: list[Violation] = []
    accept_release = root / "backend/src/oh_goon/repository_tooling/release/accept_release.py"
    if accept_release.is_file():
        facts = python_semantic_facts(accept_release)
        imports_lock_owner = any(
            imported == "oh_goon.repository_tooling.dependency_locks"
            or imported.startswith("oh_goon.repository_tooling.dependency_locks.")
            for imported in facts.imports
        )
        reinterprets_lock = _lock_semantic_parser_flow(
            accept_release,
            lock_names=frozenset({"uv.lock", "package-lock.json"}),
        )
        if imports_lock_owner or reinterprets_lock:
            violations.append(
                Violation(
                    accept_release,
                    "release acceptance must delegate dependency admissibility to build-release instead of re-probing or reinterpreting uv/npm locks",
                )
            )

    release = root / "scripts/release/build_release.sh"
    if release.is_file():
        text = release.read_text(encoding="utf-8")
        if text.count("make release-locks-check") != 1:
            violations.append(
                Violation(
                    release,
                    "release assembly must invoke exactly one pre-materialization release-locks-check owner",
                )
            )
        for token in ("uv lock --project backend --check", "npm ci --package-lock-only"):
            if token in text:
                violations.append(
                    Violation(
                        release,
                        f"release assembly must not duplicate release-locks-check native semantics: {token}",
                    )
                )

    project_capability = (
        root / "backend/src/oh_goon/repository_tooling/project_dependency_capability.py"
    )
    if project_capability.is_file():
        tree = python_ast(project_capability)
        if _project_capability_redecides_compatibility(project_capability):
            violations.append(
                Violation(
                    project_capability,
                    "materialized project diagnostics must not independently decide project compatibility; native uv owns version compatibility",
                )
            )
        compatibility_is_native_uv = any(
            isinstance(node, ast.Dict)
            and any(
                isinstance(key, ast.Constant)
                and key.value == "compatibility_decision"
                and isinstance(value, ast.Constant)
                and value.value == "native-uv-only"
                for key, value in zip(node.keys, node.values, strict=True)
            )
            for node in ast.walk(tree)
        )
        if not compatibility_is_native_uv:
            violations.append(
                Violation(
                    project_capability,
                    "project runtime diagnostics must state that compatibility authority remains native uv",
                )
            )

    accept_container = root / "backend/src/oh_goon/repository_tooling/release/accept_container.py"
    if accept_container.is_file() and _native_compose_version_probe_exists(accept_container):
        violations.append(
            Violation(
                accept_container,
                "container acceptance must let docker compose build be the first native Compose operation",
            )
        )
    return violations


def check_project_dependency_contract(root: Path) -> list[Violation]:
    return [
        *_check_lock_authority(root),
        *_check_dependency_privacy(root),
        *_check_browser_dependency_identity(root),
        *_check_native_command_policy(root),
        *_check_release_stamp_and_goon_network(root),
        *_check_native_authority_deduplication(root),
    ]
