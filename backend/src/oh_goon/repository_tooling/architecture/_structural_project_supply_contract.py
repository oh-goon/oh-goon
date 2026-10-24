from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

from oh_goon.repository_tooling.architecture.common import Violation, python_ast


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _path_label_option_dest(tree: ast.Module) -> str | None:
    """Return argparse's semantic destination for the public path-label option."""

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) != "add_argument":
            continue
        if not any(
            isinstance(argument, ast.Constant) and argument.value == "--path-label"
            for argument in node.args
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "dest"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
                and keyword.value.value
            ):
                return keyword.value.value
        return "path_label"
    return None


def _native_execution_observer(tree: ast.Module) -> ast.FunctionDef | None:
    """Find the function that owns the one native ``subprocess.run`` boundary."""

    owners: list[ast.FunctionDef] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if _native_process_attempts(node):
            owners.append(node)
    return owners[0] if len(owners) == 1 else None


def _is_native_process_attempt(node: ast.AST) -> TypeGuard[ast.Call]:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def _execution_nodes_with_repetition(
    function: ast.FunctionDef,
) -> tuple[tuple[ast.AST, bool], ...]:
    """Return executable nodes and whether they are under a repetition construct.

    Nested function/class/lambda bodies are intentionally excluded: dead helper
    definitions are not evidence about one invocation of the enclosing observer.
    """

    result: list[tuple[ast.AST, bool]] = []

    def visit(node: ast.AST, *, repeated: bool) -> None:
        result.append((node, repeated))
        child_repeated = repeated or isinstance(
            node,
            (
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        )
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            visit(child, repeated=child_repeated)

    for statement in function.body:
        visit(statement, repeated=False)
    return tuple(result)


def _native_process_attempts(function: ast.FunctionDef) -> tuple[tuple[ast.Call, bool], ...]:
    return tuple(
        (node, repeated)
        for node, repeated in _execution_nodes_with_repetition(function)
        if _is_native_process_attempt(node)
    )


def _top_level_call_graph(tree: ast.Module) -> dict[str, set[str]]:
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    return {
        name: {
            call.func.id
            for call, _repeated in _execution_nodes_with_repetition(function)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id in functions
        }
        for name, function in functions.items()
    }


def _function_reaches_itself(tree: ast.Module, function_name: str) -> bool:
    graph = _top_level_call_graph(tree)
    pending = list(graph.get(function_name, ()))
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == function_name:
            return True
        if current in seen:
            continue
        seen.add(current)
        pending.extend(graph.get(current, ()))
    return False


def _main_invokes_observer_once(tree: ast.Module, observer_name: str) -> bool:
    main = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"),
        None,
    )
    if main is None:
        return False
    calls = tuple(
        (node, repeated)
        for node, repeated in _execution_nodes_with_repetition(main)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == observer_name
    )
    return len(calls) == 1 and not calls[0][1]


def _native_execution_is_single_attempt(tree: ast.Module) -> bool:
    """Prove one CLI observation owns one non-repeating native execution attempt.

    This proof is intentionally structural rather than vocabulary based.  Retry
    constants, sleep aliases, error strings, and helper names have no authority.
    The native process boundary must be unique, non-repeating, non-recursive, and
    invoked exactly once by the CLI entrypoint.
    """

    observer = _native_execution_observer(tree)
    if observer is None:
        return False
    attempts = _native_process_attempts(observer)
    if len(attempts) != 1 or attempts[0][1]:
        return False
    if _function_reaches_itself(tree, observer.name):
        return False
    return _main_invokes_observer_once(tree, observer.name)


def _namespace_bindings(function: ast.FunctionDef) -> set[str]:
    """Find locals bound from an argparse ``parse_args`` result."""

    result: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr == "parse_args"
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        result.update(target.id for target in targets if isinstance(target, ast.Name))
    return result


def _depends_on_path_label(
    node: ast.AST,
    *,
    namespace_bindings: set[str],
    option_dest: str,
    derived_names: set[str],
) -> bool:
    if isinstance(node, ast.Name) and node.id in derived_names:
        return True
    if (
        isinstance(node, ast.Attribute)
        and node.attr == option_dest
        and isinstance(node.value, ast.Name)
        and node.value.id in namespace_bindings
    ):
        return True
    return any(
        _depends_on_path_label(
            child,
            namespace_bindings=namespace_bindings,
            option_dest=option_dest,
            derived_names=derived_names,
        )
        for child in ast.iter_child_nodes(node)
    )


def _forwards_path_label_identity(tree: ast.Module, option_dest: str) -> bool:
    """Prove parsed path labels reach the native execution observer unchanged in identity.

    Helper names, local variable names, keyword names, and positional-vs-keyword call shape are
    intentionally irrelevant.  The semantic boundary is dataflow from the public argparse option
    into the function that owns native process execution.
    """

    observer = _native_execution_observer(tree)
    main = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main"),
        None,
    )
    if observer is None or main is None:
        return False
    namespaces = _namespace_bindings(main)
    if not namespaces:
        return False
    events = sorted(
        (
            node
            for node in ast.walk(main)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Call))
        ),
        key=lambda node: (
            getattr(node, "lineno", -1),
            getattr(node, "col_offset", -1),
            0 if isinstance(node, (ast.Assign, ast.AnnAssign)) else 1,
        ),
    )
    derived: set[str] = set()
    for node in events:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is None:
                continue
            value_is_derived = _depends_on_path_label(
                node.value,
                namespace_bindings=namespaces,
                option_dest=option_dest,
                derived_names=derived,
            )
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if value_is_derived:
                    derived.add(target.id)
                else:
                    derived.discard(target.id)
            continue
        if _call_name(node.func) != observer.name:
            continue
        forwarded = (*node.args, *(keyword.value for keyword in node.keywords))
        if any(
            _depends_on_path_label(
                value,
                namespace_bindings=namespaces,
                option_dest=option_dest,
                derived_names=derived,
            )
            for value in forwarded
        ):
            return True
    return False


def _annotation_leaf_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _application_run_service(root: Path) -> str | None:
    """Return the service type exposed through ``ApplicationRuntime.runs``.

    ``runs`` is a public application boundary.  The concrete service class name is
    discovered from that boundary rather than duplicated in this architecture
    contract.
    """

    application = root / "backend/src/oh_goon/application.py"
    if not application.is_file():
        return None
    tree = python_ast(application)
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ApplicationRuntime":
            continue
        for member in node.body:
            if (
                isinstance(member, ast.AnnAssign)
                and isinstance(member.target, ast.Name)
                and member.target.id == "runs"
            ):
                return _annotation_leaf_name(member.annotation)
    return None


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root / "backend" / "src").with_suffix("")
    return ".".join(relative.parts)


_FUNCTION = ast.FunctionDef | ast.AsyncFunctionDef


def _top_level_functions(tree: ast.Module) -> dict[str, _FUNCTION]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _executable_nodes(function: _FUNCTION) -> tuple[ast.AST, ...]:
    """Return executable nodes without treating uncalled nested definitions as evidence."""

    nodes: list[ast.AST] = []
    pending: list[ast.AST] = list(reversed(function.body))
    while pending:
        node = pending.pop()
        nodes.append(node)
        children = list(ast.iter_child_nodes(node))
        for child in reversed(children):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            pending.append(child)
    return tuple(nodes)


def _function_reaches_attribute(
    function: _FUNCTION,
    *,
    functions: dict[str, _FUNCTION],
    attribute: str,
    seen: frozenset[str] = frozenset(),
) -> bool:
    """Follow called local helpers while ignoring dead definitions and identifier spelling."""

    if function.name in seen:
        return False
    next_seen = seen | {function.name}
    nodes = _executable_nodes(function)
    if any(isinstance(node, ast.Attribute) and node.attr == attribute for node in nodes):
        return True
    called = {
        node.func.id
        for node in nodes
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return any(
        _function_reaches_attribute(
            functions[name],
            functions=functions,
            attribute=attribute,
            seen=next_seen,
        )
        for name in called & functions.keys()
    )


def _game_tape_live_projection_owner(root: Path) -> tuple[str, str] | None:
    """Discover the service projection that derives live output from Run/Game Tape.

    The owner is identified by its domain relationship, not by its filename or
    private/public function spelling: it returns the live-output domain batch and
    consumes the canonical ``FleetRunDetail.game_tape`` projection.
    """

    services = root / "backend/src/oh_goon/services"
    if not services.is_dir():
        return None
    owners: list[tuple[str, str]] = []
    for path in sorted(services.glob("*.py")):
        tree = python_ast(path)
        functions = _top_level_functions(tree)
        for node in functions.values():
            if _annotation_leaf_name(node.returns) != "GameTapeLiveOutputBatch":
                continue
            if not _function_reaches_attribute(
                node,
                functions=functions,
                attribute="game_tape",
            ):
                continue
            owners.append((_module_name(root, path), node.name))
    return owners[0] if len(owners) == 1 else None


def _imported_projection_names(
    tree: ast.Module,
    *,
    module: str,
    symbol: str,
) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.module != module:
            continue
        for imported in node.names:
            if imported.name == symbol:
                names.add(imported.asname or imported.name)
    return names


def _run_service_live_output_method(
    root: Path,
    *,
    service_class: str,
    projection_owner: tuple[str, str],
) -> str | None:
    """Discover the Run service method that delegates to the Game Tape projection."""

    services = root / "backend/src/oh_goon/services"
    if not services.is_dir():
        return None
    projection_module, projection_symbol = projection_owner
    candidates: list[str] = []
    for path in sorted(services.glob("*.py")):
        tree = python_ast(path)
        functions = _top_level_functions(tree)
        imported_names = _imported_projection_names(
            tree,
            module=projection_module,
            symbol=projection_symbol,
        )
        if not imported_names:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name != service_class:
                continue
            methods = {
                method.name: method
                for method in node.body
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            }

            def reaches_projection(
                function: _FUNCTION,
                *,
                seen: frozenset[tuple[str, str]] = frozenset(),
                owner: str,
                imported_names: set[str] = imported_names,
                functions: dict[str, _FUNCTION] = functions,
                methods: dict[str, _FUNCTION] = methods,
            ) -> bool:
                key = (owner, function.name)
                if key in seen:
                    return False
                next_seen = seen | {key}
                nodes = _executable_nodes(function)
                for call in (item for item in nodes if isinstance(item, ast.Call)):
                    if isinstance(call.func, ast.Name):
                        if call.func.id in imported_names:
                            return True
                        helper = functions.get(call.func.id)
                        if helper is not None and reaches_projection(
                            helper,
                            seen=next_seen,
                            owner="module",
                        ):
                            return True
                    if isinstance(call.func, ast.Attribute):
                        helper = methods.get(call.func.attr)
                        if helper is None or not isinstance(call.func.value, ast.Name):
                            continue
                        positional = (*function.args.posonlyargs, *function.args.args)
                        if not positional or call.func.value.id != positional[0].arg:
                            continue
                        if reaches_projection(
                            helper,
                            seen=next_seen,
                            owner="class",
                        ):
                            return True
                return False

            for method in methods.values():
                if _annotation_leaf_name(method.returns) != "GameTapeLiveOutputBatch":
                    continue
                if reaches_projection(method, owner="class"):
                    candidates.append(method.name)
    return candidates[0] if len(candidates) == 1 else None


def _cli_consumes_run_live_output(root: Path, *, method_name: str) -> bool:
    """Prove the CLI consumes live output through its ``ApplicationRuntime.runs`` port."""

    plan_cli = root / "backend/src/oh_goon/cli_runtime_plans.py"
    if not plan_cli.is_file():
        return False
    tree = python_ast(plan_cli)
    for function in (
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        runtime_names = {
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
            )
            if _annotation_leaf_name(argument.annotation) == "ApplicationRuntime"
        }
        if not runtime_names:
            continue
        for call in ast.walk(function):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == method_name
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "runs"
                and isinstance(call.func.value.value, ast.Name)
                and call.func.value.value.id in runtime_names
            ):
                continue
            return True
    return False


def _has_run_game_tape_progress_relationship(root: Path) -> bool:
    service_class = _application_run_service(root)
    projection_owner = _game_tape_live_projection_owner(root)
    if service_class is None or projection_owner is None:
        return False
    method_name = _run_service_live_output_method(
        root,
        service_class=service_class,
        projection_owner=projection_owner,
    )
    return method_name is not None and _cli_consumes_run_live_output(
        root,
        method_name=method_name,
    )


def _check_native_command_and_run_log_presentation(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    native = root / "backend/src/oh_goon/repository_tooling/native_command.py"
    if native.is_file():
        tree = python_ast(native)
        option_dest = _path_label_option_dest(tree)
        if option_dest is None:
            violations.append(
                Violation(
                    native,
                    "native-tool display must expose the --path-label privacy protocol",
                )
            )
        elif not _forwards_path_label_identity(tree, option_dest):
            violations.append(
                Violation(
                    native,
                    "native-tool display must forward parsed path-label identity to the native execution observer",
                )
            )
        if not _native_execution_is_single_attempt(tree):
            violations.append(
                Violation(
                    native,
                    "native-tool observer must own exactly one non-repeating native execution attempt",
                )
            )
    run_logged = root / "scripts/run_logged.sh"
    if run_logged.is_file():
        text = run_logged.read_text(encoding="utf-8")
        # Architecture owns only observable presentation/protocol vocabulary here.
        # Streaming, argv/status preservation, redaction, durable-log separation,
        # cancellation/cleanup, retention, and persistence-failure isolation are
        # executable behavior contracts in scripts/tests/test_run_logging.py.
        for token in (
            "Working directory: <repo>",
            "<state>/run-logs",
            "<home>",
            "<tmp>",
            "OH_GOON_PROGRESS_FD",
        ):
            if token not in text:
                violations.append(
                    Violation(
                        run_logged,
                        f"normal run-log presentation must preserve observable privacy/progress protocol: {token}",
                    )
                )
    return violations


def _check_certification_progress(root: Path) -> list[Violation]:
    plan_cli = root / "backend/src/oh_goon/cli_runtime_plans.py"
    if not plan_cli.is_file():
        return []
    text = plan_cli.read_text(encoding="utf-8")
    # Architecture owns observable progress vocabulary and the existing Run/Game
    # Tape presentation boundary.  The Run/Game Tape relationship is proven from
    # the application/service/domain graph below; private method/helper spelling is
    # never architecture authority. TTY detection, wait-loop call paths, local
    # variables, and branch choreography remain executable behavior contracts.
    required = (
        "Observed activity: Goon",
        "Certification working",
        "OH_GOON_PROGRESS_FD",
    )
    violations = [
        Violation(
            plan_cli,
            f"certification progress must remain live presentation over existing Run/Game Tape owners: {token}",
        )
        for token in required
        if token not in text
    ]
    if not _has_run_game_tape_progress_relationship(root):
        violations.append(
            Violation(
                plan_cli,
                "certification progress must consume live output through the "
                "ApplicationRuntime.runs Run/Game Tape service owner",
            )
        )
    return violations


def check_project_supply_contract(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    violations.extend(_check_native_command_and_run_log_presentation(root))
    violations.extend(_check_certification_progress(root))
    return violations
