from __future__ import annotations

import ast
import json
import re
import shlex
from pathlib import Path

import yaml

from oh_goon.repository_tooling.architecture.common import (
    Violation,
    dependency_policy_bootstrap_source_path,
    project_scripts_from_text,
)
from oh_goon.repository_tooling.architecture.shell_semantics import (
    MakeSemanticFacts,
    ShellCommand,
    make_semantic_facts,
    shell_semantic_commands,
    strip_shell_comment,
)

_REQUIRED_ENTRYPOINTS = (
    "oh-goon-workflow",
    "oh-goon-architecture",
    "oh-goon-release-version",
    "oh-goon-package-source",
    "oh-goon-local-state",
    "oh-goon-native-command",
    "oh-goon-source-layout",
    "oh-goon-container",
)


def _target_body(text: str, name: str) -> str:
    match = re.search(rf"(?m)^{re.escape(name)}:[^\n]*\n", text)
    if match is None:
        return ""
    return text[match.end() :].split("\n\n", 1)[0]


def _check_required_entrypoints(root: Path, pyproject_text: str) -> list[Violation]:
    pyproject = root / "backend" / "pyproject.toml"
    scripts = project_scripts_from_text(pyproject_text)
    return [
        Violation(pyproject, f"missing canonical pyproject entrypoint: {entrypoint}")
        for entrypoint in _REQUIRED_ENTRYPOINTS
        if entrypoint not in scripts
    ]


def _check_python_tooling_owner(root: Path) -> list[Violation]:
    scripts_root = root / "scripts"
    if not scripts_root.is_dir():
        return []
    python_modules = [
        path
        for path in scripts_root.rglob("*.py")
        if "tests" not in path.relative_to(scripts_root).parts
    ]
    if not python_modules:
        return []
    return [
        Violation(
            scripts_root,
            "repository Python implementations must live under backend/src/oh_goon/repository_tooling and be owned by backend/pyproject.toml",
        )
    ]


def _effective_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    """Drop transparent shell launch wrappers before authority classification."""

    values = list(argv)
    while values and values[0] in {"command", "exec"}:
        values.pop(0)
    if values and values[0] == "env":
        values.pop(0)
        while values and "=" in values[0] and not values[0].startswith("-"):
            values.pop(0)
    return tuple(values)


def _is_direct_python(command: ShellCommand) -> bool:
    argv = _effective_argv(command.argv)
    if not argv:
        return False
    executable = Path(argv[0]).name
    if executable in {"python", "python3"}:
        return True
    if len(argv) >= 2 and argv[0] == "uv" and argv[1] == "run":
        # ``uv run <project entrypoint>`` is the intended authority.  Spawning a
        # generic Python interpreter through uv still creates a second command
        # surface and is forbidden outside the one explicit bootstrap capability.
        options_with_value = {
            "--project",
            "--directory",
            "--python",
            "--with",
            "--with-editable",
            "--env-file",
        }
        index = 2
        while index < len(argv):
            token = argv[index]
            if token == "--":
                index += 1
                break
            if token in options_with_value:
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            break
        return index < len(argv) and Path(argv[index]).name in {"python", "python3"}
    return False


def _docker_commands(text: str) -> tuple[ShellCommand, ...]:
    active: list[str] = []
    for raw in text.splitlines():
        line = strip_shell_comment(raw).strip()
        if not line:
            continue
        instruction, _, body = line.partition(" ")
        if instruction.upper() == "RUN" and body:
            active.append(body)
        elif instruction.upper() == "CMD" and body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                active.append(body)
            else:
                if isinstance(payload, list) and all(isinstance(item, str) for item in payload):
                    quoted = " ".join(shlex.quote(item) for item in payload)
                    active.append(quoted)
    return shell_semantic_commands("\n".join(active), include_function_bodies=False)


def _check_direct_python_surfaces(root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for surface in (root / "scripts" / "dev.sh", root / "scripts" / "release" / "build_release.sh"):
        if not surface.is_file():
            continue
        if any(
            _is_direct_python(command)
            for command in shell_semantic_commands(surface.read_text(encoding="utf-8"))
        ):
            violations.append(
                Violation(
                    surface,
                    "project Python must execute through uv; direct python/python3 execution is forbidden",
                )
            )

    for dockerfile in (root / "docker").glob("**/Dockerfile"):
        if any(
            _is_direct_python(command)
            for command in _docker_commands(dockerfile.read_text(encoding="utf-8"))
        ):
            violations.append(
                Violation(
                    dockerfile,
                    "project Python must execute through uv; direct python/python3 execution is forbidden",
                )
            )

    package = root / "frontend" / "package.json"
    if package.is_file():
        payload = json.loads(package.read_text(encoding="utf-8"))
        scripts = payload.get("scripts", {}) if isinstance(payload, dict) else {}
        if isinstance(scripts, dict):
            for value in scripts.values():
                if isinstance(value, str) and any(
                    _is_direct_python(command)
                    for command in shell_semantic_commands(value, include_function_bodies=False)
                ):
                    violations.append(
                        Violation(
                            package,
                            "project Python must execute through uv; direct python/python3 execution is forbidden",
                        )
                    )
                    break
    return violations


def _module_has_main_guard(module_tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(
            isinstance(value, ast.Constant) and value.value == "__main__"
            for value in node.test.comparators
        )
        for node in module_tree.body
    )


def _runtime_owned_repository_tooling_modules(root: Path) -> set[str]:
    registry = root / "docs" / "development" / "RUNTIME_AUTHORITY_REGISTRY.json"
    if not registry.is_file():
        return set()
    payload = json.loads(registry.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for relative in payload.get("owners", {}):
        path = Path(str(relative))
        if path.suffix == ".py" and tuple(path.parts[:4]) == (
            "backend",
            "src",
            "oh_goon",
            "repository_tooling",
        ):
            modules.add(".".join(path.with_suffix("").parts[2:]))
    return modules


def _externally_invoked_repository_tooling_modules(root: Path) -> set[str]:
    modules: set[str] = set()
    for surface_root in (root / "scripts", root / "docker"):
        if not surface_root.is_dir():
            continue
        for surface in surface_root.rglob("*"):
            if not surface.is_file() or surface.suffix not in {".sh", ".Dockerfile", ""}:
                continue
            try:
                text = surface.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            commands = (
                _docker_commands(text)
                if surface.name == "Dockerfile"
                else shell_semantic_commands(text)
            )
            for command in commands:
                argv = command.argv
                for index, token in enumerate(argv[:-1]):
                    if token == "-m" and argv[index + 1].startswith("oh_goon.repository_tooling."):
                        modules.add(argv[index + 1])
    return modules


def _repository_tooling_module_violation(
    module: Path,
    *,
    source_root: Path,
    script_targets: set[str],
    runtime_owned_modules: set[str],
    externally_invoked_modules: set[str],
) -> list[Violation]:
    violations: list[Violation] = []
    module_text = module.read_text(encoding="utf-8")
    first_line = module_text.splitlines()[:1]
    if first_line and first_line[0].startswith("#!") and "python" in first_line[0].casefold():
        violations.append(
            Violation(
                module,
                "repository tooling must not expose a direct Python shebang; invoke it through uv",
            )
        )
    try:
        module_tree = ast.parse(module_text)
    except SyntaxError:
        return violations
    module_name = ".".join(module.relative_to(source_root).with_suffix("").parts)
    if (
        _module_has_main_guard(module_tree)
        and module_name not in script_targets
        and module_name not in runtime_owned_modules
        and module_name not in externally_invoked_modules
    ):
        violations.append(
            Violation(
                module,
                "repeated repository Python command must be a named backend/pyproject.toml project script and run through uv",
            )
        )
    return violations


def _project_script_modules(pyproject_text: str) -> set[str]:
    modules: set[str] = set()
    for target in project_scripts_from_text(pyproject_text).values():
        if not isinstance(target, str):
            continue
        module, separator, _callable = target.partition(":")
        if separator and module:
            modules.add(module)
    return modules


def _check_repository_tooling_commands(root: Path, pyproject_text: str) -> list[Violation]:
    repository_tooling_root = root / "backend" / "src" / "oh_goon" / "repository_tooling"
    if not repository_tooling_root.is_dir():
        return []
    script_targets = _project_script_modules(pyproject_text)
    source_root = root / "backend" / "src"
    runtime_owned_modules = _runtime_owned_repository_tooling_modules(root)
    externally_invoked_modules = _externally_invoked_repository_tooling_modules(root)
    violations: list[Violation] = []
    for module in repository_tooling_root.rglob("*.py"):
        violations.extend(
            _repository_tooling_module_violation(
                module,
                source_root=source_root,
                script_targets=script_targets,
                runtime_owned_modules=runtime_owned_modules,
                externally_invoked_modules=externally_invoked_modules,
            )
        )
    return violations


def _check_precommit(root: Path) -> list[Violation]:
    precommit = root / ".pre-commit-config.yaml"
    if not precommit.is_file():
        return []
    try:
        payload = yaml.safe_load(precommit.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [Violation(precommit, f"pre-commit configuration must remain parseable YAML: {exc}")]

    entries: list[str] = []
    if isinstance(payload, dict):
        repos = payload.get("repos")
        if isinstance(repos, list):
            for repo in repos:
                hooks = repo.get("hooks") if isinstance(repo, dict) else None
                if not isinstance(hooks, list):
                    continue
                for hook in hooks:
                    entry = hook.get("entry") if isinstance(hook, dict) else None
                    if isinstance(entry, str):
                        entries.append(entry)

    violations: list[Violation] = []
    for entry in entries:
        commands = list(shell_semantic_commands(entry, include_function_bodies=False))
        # Parse shell -c payload from the original YAML scalar so outer-shell
        # single quotes keep inner $variables intact until the nested shell owns
        # their expansion.
        try:
            entry_argv = tuple(shlex.split(entry, posix=True))
        except ValueError:
            entry_argv = ()
        if (
            entry_argv[:1] in {("bash",), ("sh",)}
            and len(entry_argv) >= 3
            and entry_argv[1] in {"-c", "-lc"}
        ):
            commands.extend(shell_semantic_commands(entry_argv[2], include_function_bodies=False))
        for command in commands:
            argv = _effective_argv(command.argv)
            forbidden_reason: str | None = None
            if "PYTHONPATH" in command.env():
                forbidden_reason = "PYTHONPATH environment authority"
            elif _is_direct_python(command):
                forbidden_reason = "direct python/python3 execution"
            elif argv[:2] == ("uv", "run") and "--isolated" in argv and "--script" in argv:
                forbidden_reason = "uv run --isolated --script"
            if forbidden_reason is not None:
                violations.append(
                    Violation(
                        precommit,
                        "pre-commit repository Python must use uv + named project entrypoints, not "
                        + forbidden_reason,
                    )
                )
                break
    return violations


def _all_make_commands(facts: MakeSemanticFacts) -> tuple[ShellCommand, ...]:
    return tuple(command for commands in facts.target_commands.values() for command in commands)


def _is_dependency_policy_bootstrap(argv: tuple[str, ...], allowed_path: str | None) -> bool:
    argv = _effective_argv(argv)
    if allowed_path is None or len(argv) < 5 or argv[:2] != ("uv", "run"):
        return False
    try:
        python_index = argv.index("python", 2)
    except ValueError:
        return False
    return (
        "--no-project" in argv[2:python_index]
        and python_index + 1 < len(argv)
        and argv[python_index + 1] == allowed_path
    )


def _check_make_command_owners(
    root: Path,
    makefile: Path,
    facts: MakeSemanticFacts,
    pyproject_text: str,
) -> list[Violation]:
    violations: list[Violation] = []
    commands = _all_make_commands(facts)
    bootstrap_source = dependency_policy_bootstrap_source_path(root, pyproject_text)
    if bootstrap_source is None or not bootstrap_source.is_file():
        violations.append(
            Violation(
                root / "backend" / "pyproject.toml",
                "pre-environment dependency bootstrap capability must map to one existing :main project-script source module",
            )
        )
        allowed_bootstrap_path = None
    else:
        allowed_bootstrap_path = bootstrap_source.relative_to(root).as_posix()

    if any(
        _is_direct_python(command)
        and not _is_dependency_policy_bootstrap(command.argv, allowed_bootstrap_path)
        for command in commands
    ):
        violations.append(
            Violation(
                makefile,
                "Makefile direct python/python3 execution is forbidden; use uv run project entrypoints so uv owns interpreter selection",
            )
        )

    if any("PYTHONPATH" in command.env() for command in commands):
        violations.append(
            Violation(
                makefile,
                "repository commands must use native tool/project entrypoints, not ghost bootstrap authority: PYTHONPATH",
            )
        )

    if any(
        (
            _effective_argv(command.argv)[:1]
            and Path(_effective_argv(command.argv)[0]).name in {"node", "npx"}
        )
        for command in commands
    ):
        violations.append(
            Violation(
                makefile,
                "frontend repository commands must execute through npm scripts; direct Make node/npx execution is forbidden",
            )
        )

    bootstrap_commands = [
        command
        for command in commands
        if _is_dependency_policy_bootstrap(command.argv, allowed_bootstrap_path)
    ]
    if not bootstrap_commands:
        violations.append(
            Violation(
                makefile, "Make must project the pre-environment dependency-policy capability"
            )
        )

    for command in commands:
        argv = _effective_argv(command.argv)
        source_paths = [
            token
            for token in argv
            if token.startswith("backend/src/oh_goon/repository_tooling/") and token.endswith(".py")
        ]
        if not source_paths:
            continue
        if _is_dependency_policy_bootstrap(command.argv, allowed_bootstrap_path):
            if source_paths == [allowed_bootstrap_path]:
                continue
            violations.append(
                Violation(
                    makefile,
                    "Make dependency policy must source-execute exactly the module owned by the oh-goon-dependency-policy capability",
                )
            )
            continue
        violations.append(
            Violation(
                makefile,
                "Make must invoke repeated repository Python through named pyproject entrypoints; only the explicit pre-environment dependency-policy capability may source-execute its owned module",
            )
        )
        break
    return violations


def _check_make_progress_and_frontend(
    root: Path, makefile: Path, facts: MakeSemanticFacts
) -> list[Violation]:
    del root
    violations: list[Violation] = []
    if "--no-print-directory" not in facts.variables.get("MAKEFLAGS", "").split():
        violations.append(
            Violation(
                makefile,
                "recursive Make progress must suppress absolute Entering/Leaving directory chatter",
            )
        )
    init_commands = facts.target_commands.get("init", ())
    if init_commands:
        progress_commands = [
            command
            for command in init_commands
            if _effective_argv(command.argv)[:2] == ("sh", "scripts/run_step.sh")
        ]
        if len(progress_commands) != 3:
            violations.append(
                Violation(
                    makefile,
                    "init must retain one shared elapsed-time progress owner for all declared phases (3)",
                )
            )
    frontend_commands = facts.target_commands.get("frontend-install", ())
    npm_ci = [
        _effective_argv(command.argv)
        for command in frontend_commands
        if _effective_argv(command.argv)[:2] == ("npm", "ci")
    ]
    required_flags = {"--prefer-offline", "--ignore-scripts", "--no-audit", "--no-fund"}
    if frontend_commands and (len(npm_ci) != 1 or not required_flags.issubset(set(npm_ci[0][2:]))):
        violations.append(
            Violation(
                makefile,
                "frontend materialization must delegate directly to native npm ci without an Oh-Goon cache implementation",
            )
        )
    return violations


def _check_operator_uv_environment(makefile: Path, facts: MakeSemanticFacts) -> list[Violation]:
    violations: list[Violation] = []
    commands = _all_make_commands(facts)
    environment_names = set(facts.variables)
    for command in commands:
        environment_names.update(command.env())
    if "UV_CONCURRENT_DOWNLOADS" in environment_names:
        violations.append(
            Violation(
                makefile,
                "Oh-Goon must not hardcode uv download concurrency; operator uv configuration owns network tuning",
            )
        )
    for name in ("UV_SYSTEM_CERTS", "UV_DEFAULT_INDEX", "UV_INDEX"):
        if name in environment_names:
            violations.append(
                Violation(
                    makefile,
                    f"Oh-Goon must inherit operator uv/TLS environment rather than own or source it: {name}",
                )
            )
    if any("--default-index" in command.argv for command in commands):
        violations.append(
            Violation(
                makefile,
                "Oh-Goon must inherit operator uv/TLS environment rather than own or source it: --default-index",
            )
        )
    if any(
        _effective_argv(command.argv)[:2] in {("source", "~/.bashrc"), (".", "~/.bashrc")}
        for command in commands
    ):
        violations.append(
            Violation(
                makefile,
                "Oh-Goon must inherit operator uv/TLS environment rather than own or source it: shell rc sourcing",
            )
        )
    return violations


def _check_native_uv_environment_ownership(root: Path) -> list[Violation]:
    owner = root / "backend" / "src" / "oh_goon" / "engine" / "native_uv_environment.py"
    if not owner.is_file():
        return [Violation(owner, "native uv environment transport must have one shared owner")]
    violations: list[Violation] = []
    consumers = (
        "backend/src/oh_goon/engine/fleet_tools.py",
        "backend/src/oh_goon/engine/fleet_extensions.py",
        "backend/src/oh_goon/integrations/fleet_plan_managed_runtime.py",
    )
    for relative in consumers:
        path = root / relative
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports_owner = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "oh_goon.engine.native_uv_environment"
            for node in tree.body
        )
        if not imports_owner:
            violations.append(
                Violation(path, "managed uv consumer must use the shared native uv transport owner")
            )
        if relative.startswith("backend/src/oh_goon/engine/"):
            for node in tree.body:
                if (
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "_runtime_environment"
                ):
                    violations.append(
                        Violation(
                            path,
                            "managed uv consumer must not own a second uv runtime-environment implementation",
                        )
                    )
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                    if value is not None and any(
                        isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                        and item.value.startswith("UV_")
                        for item in ast.walk(value)
                    ):
                        violations.append(
                            Violation(
                                path,
                                "managed uv consumer must not own an independent UV_* environment policy",
                            )
                        )
    return violations


def check_project_tooling_contract(root: Path) -> list[Violation]:
    pyproject = root / "backend" / "pyproject.toml"
    pyproject_text = pyproject.read_text(encoding="utf-8") if pyproject.is_file() else ""
    violations = [
        *_check_required_entrypoints(root, pyproject_text),
        *_check_python_tooling_owner(root),
        *_check_direct_python_surfaces(root),
        *_check_repository_tooling_commands(root, pyproject_text),
        *_check_precommit(root),
        *_check_native_uv_environment_ownership(root),
    ]
    makefile = root / "Makefile"
    if not makefile.is_file():
        return violations
    text = makefile.read_text(encoding="utf-8")
    facts = make_semantic_facts(text)
    return [
        *violations,
        *_check_make_command_owners(root, makefile, facts, pyproject_text),
        *_check_make_progress_and_frontend(root, makefile, facts),
        *_check_operator_uv_environment(makefile, facts),
    ]
