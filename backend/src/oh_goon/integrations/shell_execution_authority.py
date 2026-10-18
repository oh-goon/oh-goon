from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.integrations._shell_authority_flow import commands_from_script_lines
from oh_goon.integrations._shell_authority_syntax import (
    CONTROL_WORDS,
    SHELLS,
    SIMPLE_TOOL,
    command_index,
    env_assignments,
    env_child_index,
    external_tool,
    extract_command_substitutions,
    find_exec_child_words,
    forward_position,
    function_names,
    logical_lines,
    mask_noncommand_expansions,
    materialized_executable_identity,
    materialized_executables_in_shell,
    prefix_environment,
    project_path,
    segments,
    shebang_tools,
    shell_child_words,
)
from oh_goon.integrations.package_script_execution_authority import (
    PackageExecutableAuthority,
    derive_npm_run_authority,
    npm_run_script,
)


@dataclass(frozen=True, slots=True)
class ShellAuthority:
    tools: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    scripts: tuple[str, ...]
    package_executables: tuple[PackageExecutableAuthority, ...]
    package_provenance: tuple[tuple[str, str], ...]
    package_sources: tuple[str, ...]
    materialized_executables: tuple[str, ...]


@dataclass(slots=True)
class _Builder:
    root: Path
    working_directory: str = "."
    tools: set[str] = field(default_factory=set)
    provenance: set[tuple[str, str]] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    scripts: set[str] = field(default_factory=set)
    package_executables: set[PackageExecutableAuthority] = field(default_factory=set)
    package_provenance: set[tuple[str, str]] = field(default_factory=set)
    package_sources: set[str] = field(default_factory=set)
    materialized_executables: set[str] = field(default_factory=set)
    visiting: list[str] = field(default_factory=list)

    def require(self, tool: str, chain: tuple[str, ...]) -> None:
        self.tools.add(tool)
        self.provenance.add((tool, " -> ".join((*chain, tool))))

    def reject(self, message: str, chain: tuple[str, ...]) -> None:
        self.unresolved.add(" -> ".join(chain) + ": " + message)


def _scan_forwarded_argv(
    builder: _Builder,
    argv: tuple[str, ...],
    position: int | None,
    chain: tuple[str, ...],
    environment: dict[str, str],
) -> None:
    if position is None:
        return
    index = position - 1
    if len(argv) <= index:
        builder.reject(f"forwarded argv position {position} is absent", chain)
        return
    _scan_words(builder, tuple(argv[index:]), chain, frozenset(), environment)


def _relative_script(builder: _Builder, path: Path, chain: tuple[str, ...]) -> str | None:
    try:
        return path.resolve().relative_to(builder.root).as_posix()
    except (OSError, ValueError):
        builder.reject(f"script escapes repository: {path}", chain)
        return None


def _read_script(
    builder: _Builder, path: Path, relative: str, chain: tuple[str, ...]
) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        builder.reject(f"cannot read owned script {relative}: {exc}", chain)
        return None


def _scan_script_body(
    builder: _Builder,
    *,
    relative: str,
    lines: tuple[str, ...],
    argv: tuple[str, ...],
    chain: tuple[str, ...],
    environment: dict[str, str],
) -> None:
    functions = function_names(lines)
    _scan_forwarded_argv(
        builder,
        argv,
        forward_position(lines, relative),
        (*chain, "<argv-forward>"),
        environment,
    )
    for command in commands_from_script_lines(lines, environment):
        _scan_fragment(builder, command, chain, functions, environment)


def _scan_owned_script(
    builder: _Builder,
    path: Path,
    argv: tuple[str, ...],
    chain: tuple[str, ...],
    environment: dict[str, str],
    *,
    direct: bool,
) -> None:
    relative = _relative_script(builder, path, chain)
    if relative is None:
        return
    if relative in builder.visiting:
        builder.reject("owned script cycle: " + " -> ".join((*builder.visiting, relative)), chain)
        return
    text = _read_script(builder, path, relative, chain)
    if text is None:
        return
    script_chain = (*chain, relative)
    builder.scripts.add(relative)
    if direct:
        for tool in shebang_tools(path):
            builder.require(tool, (*script_chain, "<shebang>"))
    builder.visiting.append(relative)
    try:
        _scan_script_body(
            builder,
            relative=relative,
            lines=logical_lines(text),
            argv=argv,
            chain=script_chain,
            environment=environment,
        )
    finally:
        builder.visiting.pop()


def _shell_command_operand(words: tuple[str, ...], index: int) -> tuple[str | None, int]:
    cursor = index + 1
    while cursor < len(words) and words[cursor].startswith("-") and words[cursor] != "-":
        option = words[cursor]
        cursor += 1
        if option == "-c":
            return "-c", cursor
    return None, cursor


def _scan_shell_command(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    shell = words[index]
    builder.require(shell, chain)
    mode, cursor = _shell_command_operand(words, index)
    if mode == "-c":
        if cursor >= len(words):
            builder.reject(f"{shell} -c command is missing", chain)
            return
        _scan_fragment(builder, words[cursor], (*chain, f"{shell} -c"), functions, environment)
        return
    if cursor >= len(words):
        return
    script = project_path(builder.root, words[cursor])
    if script is None:
        _reject_unresolved_shell_operand(builder, shell, words[cursor], chain)
        return
    _scan_owned_script(
        builder, script, tuple(words[cursor + 1 :]), chain, environment, direct=False
    )


def _reject_unresolved_shell_operand(
    builder: _Builder, shell: str, operand: str, chain: tuple[str, ...]
) -> None:
    if operand.endswith(".sh") or operand.startswith(("$", "${")):
        builder.reject(f"unresolved {shell} script operand: {operand}", chain)


def _scan_find_exec(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    children = find_exec_child_words(words, index)
    markers = sum(word in {"-exec", "-execdir"} for word in words[index + 1 :])
    if markers and len(children) != markers:
        builder.reject("find -exec command is missing", chain)
    for child in children:
        _scan_words(builder, child, (*chain, "find -exec"), functions, environment)


def _skip_command(command: str, functions: frozenset[str]) -> bool:
    return not command or command in CONTROL_WORDS or command in functions or command.endswith(")")


def _scan_command_wrapper(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    command: str,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> bool:
    if command not in {"command", "builtin"}:
        return False
    cursor = index + 1
    while cursor < len(words) and words[cursor].startswith("-"):
        cursor += 1
    if cursor < len(words) and words[cursor] not in {"-v", "-V"}:
        _scan_words(builder, tuple(words[cursor:]), (*chain, command), functions, environment)
    return True


def _scan_exec(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    if index + 1 >= len(words):
        builder.reject("exec target is missing", chain)
        return
    _scan_words(builder, tuple(words[index + 1 :]), (*chain, "exec"), functions, environment)


def _scan_trap(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    if index + 1 < len(words) and words[index + 1] not in {"-", "''", '""'}:
        _scan_fragment(builder, words[index + 1], (*chain, "trap"), functions, environment)


def _scan_env(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    builder.require("env", chain)
    child = env_child_index(words, index)
    if child is None:
        return
    child_environment = dict(environment)
    child_environment.update(env_assignments(words, index + 1, child))
    _scan_words(builder, tuple(words[child:]), (*chain, "env"), functions, child_environment)


def _scan_xargs(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    builder.require("xargs", chain)
    child = shell_child_words(words, index)
    if child is None:
        builder.reject("xargs command is implicit", chain)
        return
    _scan_words(builder, child, (*chain, "xargs"), functions, environment)


def _scan_process_launcher(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    command: str,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    builder.require(command, chain)
    child = shell_child_words(words, index)
    if child is None:
        builder.reject(f"{command} child command is unprovable", chain)
        return
    _scan_words(builder, child, (*chain, command), functions, environment)


def _scan_find(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    builder.require("find", chain)
    _scan_find_exec(builder, words, index, chain, functions, environment)


def _scan_npm(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    chain: tuple[str, ...],
) -> None:
    builder.require("npm", chain)
    script = npm_run_script(words, index)
    if script is None:
        return
    if "$" in script or "`" in script:
        builder.reject(f"dynamic npm script name: {script}", chain)
        return
    authority = derive_npm_run_authority(
        builder.root,
        script,
        chain=(*chain, f"npm run {script}"),
    )
    builder.tools.update(authority.host_tools)
    builder.provenance.update(authority.host_provenance)
    builder.package_executables.update(authority.package_executables)
    builder.package_provenance.update(authority.package_provenance)
    builder.package_sources.update(authority.sources)
    builder.unresolved.update(authority.unresolved)


def _scan_dynamic_command(builder: _Builder, command: str, chain: tuple[str, ...]) -> bool:
    if command == "__OH_GOON_PARAM__":
        builder.reject("unresolved executable parameter expansion", chain)
        return True
    if command == "__OH_GOON_SUBSTITUTION__":
        builder.reject("unresolved executable command substitution", chain)
        return True
    if not command.startswith(("$", "${")):
        return False
    if command not in {'"$@"', "$@", '"${@}"', "${@}"}:
        builder.reject(f"unresolved executable variable: {command}", chain)
    return True


def _scan_owned_command(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    command: str,
    chain: tuple[str, ...],
    environment: dict[str, str],
) -> bool:
    if materialized_executable_identity(command) is not None:
        # uv/npm own these generated executable wrappers. Treating a materialized
        # Python console entry point as shell made authority depend on whether
        # `make init` had already run (e.g. `__name__` became a fake host tool).
        # Canonical dependency identity is collected by the cwd-aware shell pass
        # before this owned-source guard runs.
        return True
    owned = project_path(builder.root, command)
    if owned is None or owned.suffix not in {".sh", ""} or not owned.is_file():
        return False
    _scan_owned_script(builder, owned, tuple(words[index + 1 :]), chain, environment, direct=True)
    return True


def _scan_external_command(builder: _Builder, command: str, chain: tuple[str, ...]) -> None:
    tool = external_tool(builder.root, command)
    if tool is not None:
        builder.require(tool, chain)
        return
    if SIMPLE_TOOL.fullmatch(command) is None and "/" not in command:
        builder.reject(f"unresolved executable token: {command!r}", chain)


def _scan_special_command(
    builder: _Builder,
    words: tuple[str, ...],
    index: int,
    command: str,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> bool:
    if command == "eval":
        builder.reject("dynamic eval execution is unprovable", chain)
        return True
    if _scan_command_wrapper(builder, words, index, command, chain, functions, environment):
        return True
    if command == "exec":
        _scan_exec(builder, words, index, chain, functions, environment)
        return True
    if command == "trap":
        _scan_trap(builder, words, index, chain, functions, environment)
        return True
    if command in SHELLS:
        _scan_shell_command(builder, words, index, chain, functions, environment)
        return True
    if command == "env":
        _scan_env(builder, words, index, chain, functions, environment)
        return True
    if command == "xargs":
        _scan_xargs(builder, words, index, chain, functions, environment)
        return True
    if command == "find":
        _scan_find(builder, words, index, chain, functions, environment)
        return True
    if command in {"timeout", "nice", "nohup", "stdbuf", "setsid"}:
        _scan_process_launcher(builder, words, index, command, chain, functions, environment)
        return True
    if command == "npm":
        _scan_npm(builder, words, index, chain)
        return True
    return False


def _scan_words(
    builder: _Builder,
    words: tuple[str, ...],
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    if not words:
        return
    index = command_index(words)
    if index >= len(words):
        return
    local_environment = prefix_environment(words, index, environment)
    command = words[index].lstrip("({").rstrip(")}")
    if _skip_command(command, functions):
        return
    if _scan_special_command(builder, words, index, command, chain, functions, local_environment):
        return
    if _scan_dynamic_command(builder, command, chain):
        return
    if _scan_owned_command(builder, words, index, command, chain, local_environment):
        return
    _scan_external_command(builder, command, chain)


def _scan_fragment(
    builder: _Builder,
    fragment: str,
    chain: tuple[str, ...],
    functions: frozenset[str],
    environment: dict[str, str],
) -> None:
    builder.materialized_executables.update(
        materialized_executables_in_shell(
            fragment,
            working_directory=builder.working_directory,
        )
    )
    cleaned, substitutions = extract_command_substitutions(fragment)
    for substitution in substitutions:
        _scan_fragment(
            builder, substitution, (*chain, "<command-substitution>"), functions, environment
        )
    cleaned = mask_noncommand_expansions(cleaned)
    parsed = segments(cleaned)
    if parsed is None:
        builder.reject(f"unparseable shell fragment: {fragment}", chain)
        return
    for words in parsed:
        _scan_words(builder, words, chain, functions, environment)


def derive_shell_fragment_authority(
    root: Path,
    fragment: str,
    *,
    chain: tuple[str, ...],
    environment: dict[str, str] | None = None,
    working_directory: str = ".",
) -> ShellAuthority:
    builder = _Builder(root=root.resolve(), working_directory=working_directory)
    _scan_fragment(builder, fragment, chain, frozenset(), dict(environment or {}))
    return ShellAuthority(
        tools=tuple(sorted(builder.tools)),
        provenance=tuple(sorted(builder.provenance)),
        unresolved=tuple(sorted(builder.unresolved)),
        scripts=tuple(sorted(builder.scripts)),
        package_executables=tuple(sorted(builder.package_executables)),
        package_provenance=tuple(sorted(builder.package_provenance)),
        package_sources=tuple(sorted(builder.package_sources)),
        materialized_executables=tuple(sorted(builder.materialized_executables)),
    )
