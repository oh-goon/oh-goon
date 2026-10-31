from __future__ import annotations

import ast
import re
import shlex
from collections.abc import Iterator
from pathlib import Path

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    project_scripts_from_text,
    python_ast,
    python_semantic_facts,
)
from oh_goon.repository_tooling.architecture.shell_semantics import (
    MakeSemanticFacts,
    ShellCommand,
    make_semantic_facts,
)

_WORKFLOW_MODULE = Path("backend/src/oh_goon/workflow.py")
_WORKFLOW_ENTRYPOINT_TARGET = "oh_goon.services.certification_frontdoor:main"
_WORKFLOW_ALLOWED_PRODUCT_IMPORTS = frozenset(
    {
        # Dependency-neutral process-environment vocabulary. The workflow may
        # transport the owner-defined key but may not import runtime/domain logic.
        "oh_goon.core.strict_executor_authority",
    }
)

_WORKFLOW_FORBIDDEN_LOCAL_AUTHORITY_MODULES = frozenset({"sqlite3", "resource", "psutil"})
_WORKFLOW_FORBIDDEN_LOCAL_AUTHORITY_CALLS = frozenset(
    {
        "hashlib.sha256",
        "hashlib.new",
        "os.cpu_count",
        "os.sched_getaffinity",
        "resource.getrlimit",
    }
)
_WORKFLOW_FORBIDDEN_NATIVE_AUTHORITY_MARKERS = frozenset(
    {
        "bwrap",
        "systemd-run",
        "cgroup.controllers",
        "memory.max",
        "pids.max",
        "Delegate=yes",
        "DelegateSubgroup=",
    }
)

_MIRROR_MARKER_TOKENS = (
    ".app-passed",
    ".app_passed",
    "app_pass_marker",
    "last_success_marker",
    "certification_pass_marker",
)


def _make_targets(text: str) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?:\s.*)?$", line)
        if match and not match.group(1).startswith("."):
            current = match.group(1)
            targets.setdefault(current, [])
            continue
        if current is not None and line.startswith("\t"):
            targets[current].append(line.lstrip("\t"))
        elif line and not line[0].isspace() and not line.startswith("#"):
            current = None
    return targets


def _make_recipe_commands(lines: list[str]) -> tuple[tuple[str, ...], ...]:
    """Return bounded shell argv for literal Make recipe command lines.

    Shell whitespace and quote spelling are representation-only here. Make recipe
    prefix characters are stripped before tokenization. Compound shell statements
    are still returned as their literal tokens; callers decide the exact owned argv.
    """

    commands: list[tuple[str, ...]] = []
    for line in lines:
        command = line.lstrip().lstrip("@-+").lstrip()
        if not command:
            continue
        try:
            words = tuple(shlex.split(command, posix=True))
        except ValueError:
            continue
        if words:
            commands.append(words)
    return tuple(commands)


def _make_variable_assignments(text: str) -> dict[str, list[tuple[str, str]]]:
    """Return bounded top-level Make variable assignments.

    Whitespace around the assignment operator is representation-only. The operator
    itself remains semantic because recursive/immediate/conditional/append expansion
    can change behavior. Recipe-local assignments are intentionally excluded.
    """

    assignments: dict[str, list[tuple[str, str]]] = {}
    for line in text.splitlines():
        if not line or line[0].isspace() or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*(\?=|:=|\+=|=)\s*(.*)$", line)
        if match is None:
            continue
        name, operator, value = match.groups()
        assignments.setdefault(name, []).append((operator, value.rstrip()))
    return assignments


def _python_imports(path: Path) -> set[str]:
    tree = python_ast(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _literal_string_prefix(node: ast.AST) -> tuple[str, ...] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    values: list[str] = []
    for element in node.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            break
        values.append(element.value)
    return tuple(values) if values else None


def _backend_cli_commands(path: Path) -> tuple[tuple[str, ...], ...]:
    """Return literal backend CLI argv prefixes from workflow functions.

    Quote style, whitespace, list-vs-tuple spelling, and private local variable names
    are representation details. The architecture contract cares only that a command
    passed to ``_backend_cli`` starts with the owned public capability argv.
    """

    commands: list[tuple[str, ...]] = []
    for function in (
        node
        for node in ast.walk(python_ast(path))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        assignments: dict[str, ast.AST] = {}
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                assignments[node.targets[0].id] = node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                assignments[node.target.id] = node.value

        for node in ast.walk(function):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_backend_cli"
                and node.args
            ):
                continue
            argument: ast.AST = node.args[0]
            if isinstance(argument, ast.Name):
                argument = assignments.get(argument.id, argument)
            command = _literal_string_prefix(argument)
            if command is not None:
                commands.append(command)
    return tuple(commands)


def _runtime_python_files(root: Path) -> Iterator[Path]:
    for base in (root / "scripts", root / "backend" / "src"):
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if "tests" in path.parts or "architecture" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _command_argv(command: ShellCommand) -> tuple[str, ...]:
    argv = command.argv
    if argv and argv[0] in {"command", "exec"}:
        argv = argv[1:]
    return argv


def _target_commands(facts: MakeSemanticFacts, target: str) -> tuple[ShellCommand, ...]:
    return facts.target_commands.get(target, ())


def _make_references(value: str) -> set[str]:
    return {
        left or right
        for left, right in re.findall(
            r"\$\(([A-Za-z_][A-Za-z0-9_.-]*)\)|\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}",
            value,
        )
    }


def _target_variable_chain(facts: MakeSemanticFacts, target: str) -> set[str]:
    pending: list[str] = []
    for line in facts.raw_targets.get(target, ()):
        pending.extend(sorted(_make_references(line)))
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(sorted(_make_references(facts.raw_variables.get(name, ""))))
    return seen


def _is_logged_frontdoor(command: ShellCommand, mode: str, owned_command: tuple[str, ...]) -> bool:
    argv = _command_argv(command)
    return (
        argv[:3] == ("bash", "scripts/run_logged.sh", mode)
        and argv[3:] == owned_command
        and "OH_GOON_RUN_LOG_DIR" in command.env()
    )


def _check_make_frontdoors(root: Path, path: Path, facts: MakeSemanticFacts) -> list[Violation]:
    violations: list[Violation] = []
    pyproject = root / "backend" / "pyproject.toml"
    scripts = (
        project_scripts_from_text(pyproject.read_text(encoding="utf-8"))
        if pyproject.is_file()
        else {}
    )
    if scripts.get("oh-goon-workflow") != _WORKFLOW_ENTRYPOINT_TARGET:
        violations.append(
            Violation(
                pyproject,
                "backend pyproject.toml must own the oh-goon-workflow entrypoint",
            )
        )

    # Recursive (=) Make binding remains semantic for the command selected by the
    # test frontdoor because later operator/environment overrides must be observed.
    # The private variable name is not authority: derive the binding through the
    # actual target's variable reference chain and semantic resolved value.
    test_chain = _target_variable_chain(facts, "test")
    runner_bindings = [
        name
        for name in test_chain
        if facts.variables.get(name) == "bash scripts/development_check.sh"
    ]
    if runner_bindings and any(
        facts.variable_operators.get(name) != "=" for name in runner_bindings
    ):
        violations.append(
            Violation(
                path,
                "DEVELOPMENT_TEST_RUNNER must have exactly one canonical dependency-light shell entrypoint definition",
            )
        )

    test_commands = _target_commands(facts, "test")
    if len(test_commands) != 1 or not _is_logged_frontdoor(
        test_commands[0],
        "test",
        (
            "bash",
            "scripts/development_test_resources.sh",
            "bash",
            "scripts/development_check.sh",
        ),
    ):
        violations.append(
            Violation(
                path,
                "make test must remain a one-line delegation to the logged development test frontdoor",
            )
        )

    certify_commands = _target_commands(facts, "certify")
    cert_frontdoors = [
        command
        for command in certify_commands
        if _is_logged_frontdoor(
            command,
            "certify",
            (
                "bash",
                "scripts/development_test_resources.sh",
                "backend/.venv/bin/oh-goon-workflow",
            ),
        )
    ]
    if len(cert_frontdoors) != 1:
        violations.append(
            Violation(
                path,
                "make certify must delegate exactly once to the logged certification frontdoor; APP ordering belongs to the Plan",
            )
        )

    executable_guards = [
        command
        for command in certify_commands
        if _command_argv(command)[:3] == ("test", "-x", "backend/.venv/bin/oh-goon-workflow")
    ]
    hint_present = any(
        "Run: make init" in token for command in certify_commands for token in command.argv
    )
    if (
        not executable_guards
        or not hint_present
        or (cert_frontdoors and executable_guards[0].line_number >= cert_frontdoors[0].line_number)
    ):
        violations.append(
            Violation(
                path,
                "make certify must fail before workflow execution when the initialized backend runner is absent and direct the operator to canonical make init",
            )
        )

    forbidden_targets = {
        "init",
        "verify-p0",
        "frontend-functional-certification",
        "local-runtime-verification",
        "strict-local-runtime-acceptance",
    }
    for command in certify_commands:
        argv = _command_argv(command)
        if (
            argv[:1] in {("make",), ("$(MAKE)",)}
            and len(argv) >= 2
            and argv[1] in forbidden_targets
        ):
            violations.append(
                Violation(
                    path,
                    "make certify must not inline APP work or dependency materialization; Plan/frontdoor owners remain authoritative: "
                    + argv[1],
                )
            )
    return violations


def _check_make_orchestration_ownership(path: Path, facts: MakeSemanticFacts) -> list[Violation]:
    violations: list[Violation] = []
    required = {
        "prepare-release": ("backend/.venv/bin/oh-goon-workflow", "release"),
        "certification-explain": ("backend/.venv/bin/oh-goon-workflow", "explain"),
    }
    for target, prefix in required.items():
        matches = [
            command
            for command in _target_commands(facts, target)
            if _command_argv(command)[: len(prefix)] == prefix
        ]
        if len(matches) != 1:
            violations.append(
                Violation(
                    path,
                    f"{target} must delegate exactly once to the certification workflow entrypoint",
                )
            )

    if "release-stamp" in facts.targets:
        violations.append(
            Violation(
                path,
                "release-stamp bypass is forbidden; release-version-set is the single effectful version-authoring transition",
            )
        )

    release_owner_argv = (
        "backend/.venv/bin/oh-goon-release-version",
        "set",
        "$(OH_GOON_RELEASE_VERSION)",
    )
    release_commands = _target_commands(facts, "release-version-set")
    if sum(_command_argv(command) == release_owner_argv for command in release_commands) != 1:
        violations.append(
            Violation(
                path,
                "release-version-set must delegate exactly once to the cohesive release-authoring transition owner",
            )
        )
    for forbidden in (
        "oh-goon-export-openapi",
        "oh-goon-generate-api-types",
        "oh-goon-generate-goon-tool-authority",
        "oh-goon-source-layout",
    ):
        if any(forbidden in _command_argv(command) for command in release_commands):
            violations.append(
                Violation(
                    path,
                    "release-version-set must not reconstruct release transition choreography in Make: "
                    + forbidden,
                )
            )

    for commands in facts.target_commands.values():
        for command in commands:
            argv = tuple(token.lower().replace("_", "-") for token in _command_argv(command))
            if (len(argv) >= 2 and argv[0].endswith("oh-goon") and argv[1] == "run") or (
                len(argv) >= 4
                and Path(argv[0]).name in {"python", "python3"}
                and argv[1:4] == ("-m", "oh-goon", "run")
            ):
                violations.append(
                    Violation(
                        path,
                        f"line {command.line_number}: Make must not create a direct Plan execution path",
                    )
                )
    return violations


def check_make_single_path(root: Path) -> list[Violation]:
    path = root / "Makefile"
    text = path.read_text(encoding="utf-8")
    facts = make_semantic_facts(text)
    return [
        *_check_make_frontdoors(root, path, facts),
        *_check_make_orchestration_ownership(path, facts),
    ]


def _check_workflow_import_boundary(path: Path) -> list[Violation]:
    violations: list[Violation] = []
    for module in sorted(_python_imports(path)):
        if (
            (module == "oh_goon" or module.startswith("oh_goon."))
            and not module.startswith("oh_goon.repository_tooling")
            and module not in _WORKFLOW_ALLOWED_PRODUCT_IMPORTS
        ):
            violations.append(
                Violation(
                    path,
                    f"workflow runner must call backend capabilities instead of importing product domain module {module}",
                )
            )

    facts = python_semantic_facts(path)
    forbidden_modules = sorted(
        module
        for module in _WORKFLOW_FORBIDDEN_LOCAL_AUTHORITY_MODULES
        if any(
            imported == module or imported.startswith(module + ".") for imported in facts.imports
        )
    )
    for module in forbidden_modules:
        violations.append(
            Violation(
                path,
                f"workflow runner must delegate domain/control-state authority instead of importing local authority module {module}",
            )
        )

    for qualified, node in facts.calls:
        if qualified in _WORKFLOW_FORBIDDEN_LOCAL_AUTHORITY_CALLS or qualified.startswith(
            "psutil."
        ):
            violations.append(
                Violation(
                    path,
                    f"workflow runner must delegate backend identity/resource semantics instead of deciding them locally via {qualified}",
                )
            )
            continue
        literal_arguments = {
            child.value
            for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str)
        }
        markers = sorted(literal_arguments & _WORKFLOW_FORBIDDEN_NATIVE_AUTHORITY_MARKERS)
        if markers:
            violations.append(
                Violation(
                    path,
                    "workflow runner must delegate strict executor/resource authority instead of invoking native authority directly: "
                    + ", ".join(markers),
                )
            )

    for class_node in facts.classes:
        bases = {base.id for base in class_node.bases if isinstance(base, ast.Name)}
        if bases & {"Exception", "RuntimeError"}:
            continue
        violations.append(
            Violation(
                path,
                f"workflow runner must not define local domain/control-state model {class_node.name}; backend capabilities own domain state",
            )
        )
    return violations


def _check_workflow_bootstrap_boundary(path: Path) -> list[Violation]:
    """Certify only public workflow protocol ownership, never private implementation spelling."""

    violations: list[Violation] = []
    commands = _backend_cli_commands(path)
    if not any(command[:2] == ("dev", "bootstrap-certification") for command in commands):
        violations.append(
            Violation(
                path,
                "workflow runner must delegate certification process authority to the backend bootstrap capability",
            )
        )
    if any(command[:2] == ("dev", "bootstrap-identity") for command in commands):
        violations.append(
            Violation(
                path,
                "workflow runner must not assemble certification context from lower-level identity calls",
            )
        )
    return violations


def check_workflow_runner_boundary(root: Path) -> list[Violation]:
    path = root / _WORKFLOW_MODULE
    return [
        *_check_workflow_import_boundary(path),
        *_check_workflow_bootstrap_boundary(path),
    ]


def check_no_persisted_success_mirror(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in _runtime_python_files(root):
        text = path.read_text(encoding="utf-8")
        for token in _MIRROR_MARKER_TOKENS:
            if token in text:
                violations.append(
                    Violation(
                        path,
                        f"durable success must come from canonical Run evidence; mirror marker is forbidden: {token}",
                    )
                )
    return violations
