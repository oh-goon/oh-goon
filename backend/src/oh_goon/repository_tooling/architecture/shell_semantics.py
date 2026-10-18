from __future__ import annotations

import re
import shlex
from dataclasses import dataclass


_SHELL_VARIABLE = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*|[0-9]+)(?:(:?)-([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*|[0-9]+))"
)
_SHELL_ARRAY = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\[@\]\}$")
_MAKE_VARIABLE = re.compile(r"\$\(([A-Za-z_][A-Za-z0-9_.-]*)\)|\$\{([A-Za-z_][A-Za-z0-9_.-]*)\}")


@dataclass(frozen=True, slots=True)
class ShellCommand:
    """One bounded executable shell command after static alias expansion."""

    argv: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]
    line_number: int
    function: str | None = None

    def env(self) -> dict[str, str]:
        return dict(self.environment)


@dataclass(frozen=True, slots=True)
class MakeSemanticFacts:
    """Static Make projection used by architecture ownership checks.

    Comments, whitespace, private variable names, and simple variable indirection are
    representation details.  The projection retains target names, resolved recipes,
    and executable shell argv/environment facts only.
    """

    variables: dict[str, str]
    raw_variables: dict[str, str]
    variable_operators: dict[str, str]
    targets: dict[str, tuple[str, ...]]
    raw_targets: dict[str, tuple[str, ...]]
    target_commands: dict[str, tuple[ShellCommand, ...]]


def strip_shell_comment(line: str) -> str:
    """Remove an unquoted shell comment without interpreting shell execution."""

    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def join_shell_lines(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    joined: list[tuple[int, str]] = []
    pending = ""
    pending_line = 0
    for line_number, raw in lines:
        line = strip_shell_comment(raw).rstrip()
        if not line and not pending:
            continue
        if not pending:
            pending_line = line_number
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        pending += line
        if pending.strip():
            joined.append((pending_line, pending.strip()))
        pending = ""
    if pending.strip():
        joined.append((pending_line, pending.strip()))
    return joined


def shell_functions(text: str) -> tuple[dict[str, list[tuple[int, str]]], list[tuple[int, str]]]:
    functions: dict[str, list[tuple[int, str]]] = {}
    top_level: list[tuple[int, str]] = []
    current_name: str | None = None
    current_body: list[tuple[int, str]] = []
    depth = 0
    start = re.compile(r"^(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{\s*$")
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = strip_shell_comment(raw).strip()
        if current_name is None:
            match = start.match(stripped)
            if match is not None:
                current_name = match.group(1)
                current_body = []
                depth = 1
                continue
            top_level.append((line_number, raw))
            continue
        # Keep nested braces inside a function body.  This is intentionally bounded
        # and sufficient for shell helpers used by repository tooling.
        opens = stripped.count("{")
        closes = stripped.count("}")
        if stripped == "}" and depth == 1:
            functions[current_name] = join_shell_lines(current_body)
            current_name = None
            current_body = []
            depth = 0
            continue
        current_body.append((line_number, raw))
        depth += opens - closes
    if current_name is not None:
        functions[current_name] = join_shell_lines(current_body)
    return functions, join_shell_lines(top_level)


def _resolve_shell_value(value: str, scalars: dict[str, str]) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    def replacement(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(4)
        if name in scalars:
            value = scalars[name]
            if match.group(2) == ":" and not value:
                return match.group(3) or ""
            return value
        if match.group(2) is not None:
            return match.group(3) or ""
        return f"<VAR:{name}>"

    previous: str | None = None
    for _ in range(12):
        if value == previous:
            break
        previous = value
        value = _SHELL_VARIABLE.sub(replacement, value)
    return value


def _parse_shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return command.split()


def _split_shell_statements(line: str) -> list[str]:
    """Split active shell statements on unquoted control separators."""

    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            index += 1
            continue
        pair = line[index : index + 2]
        if pair in {"&&", "||"}:
            if "".join(current).strip():
                statements.append("".join(current).strip())
            current = []
            index += 2
            continue
        if char == ";":
            if "".join(current).strip():
                statements.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if "".join(current).strip():
        statements.append("".join(current).strip())
    return statements


def _trim_shell_control(statement: str) -> str:
    candidate = statement.strip()
    # Shell control keywords introduce executable commands but are not themselves
    # the command authority being inspected.
    for prefix in ("if ", "elif ", "then ", "while ", "until ", "do "):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :].strip()
            break
    if candidate in {"then", "do", "done", "fi", "esac", "else", "{" , "}"}:
        return ""
    if candidate.startswith("else "):
        candidate = candidate[5:].strip()
    # case labels and variable-only loop declarations are control syntax.
    if re.match(r"^(?:case\b|for\s+[A-Za-z_][A-Za-z0-9_]*\s+in\b)", candidate):
        return ""
    if re.match(r"^[^()]+\)\s*$", candidate):
        return ""
    return candidate


def _apply_shell_assignment(
    statement: str,
    scalars: dict[str, str],
    arrays: dict[str, tuple[str, ...]],
    exports: set[str],
) -> bool:
    candidate = statement.strip()
    exported = False
    if candidate.startswith("export "):
        exported = True
        candidate = candidate[7:].strip()
    elif candidate.startswith("local "):
        candidate = candidate[6:].strip()
    elif candidate.startswith("readonly "):
        candidate = candidate[9:].strip()

    if exported and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate):
        exports.add(candidate)
        return True

    array_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=\((.*)\)", candidate)
    if array_match is not None:
        name, body = array_match.groups()
        arrays[name] = tuple(_resolve_shell_value(word, scalars) for word in _parse_shell_words(body))
        if exported:
            exports.add(name)
        return True

    scalar_match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", candidate)
    if scalar_match is None:
        return False
    # Leading NAME=value followed by a command is command-local environment, not
    # a persistent assignment.
    if len(_parse_shell_words(candidate)) != 1:
        return False
    name, raw_value = scalar_match.groups()
    scalars[name] = _resolve_shell_value(raw_value, scalars)
    if exported:
        exports.add(name)
    return True


def _expand_shell_command(
    statement: str,
    scalars: dict[str, str],
    arrays: dict[str, tuple[str, ...]],
) -> tuple[list[str], dict[str, str]]:
    statement = statement.lstrip().lstrip("@-+").lstrip()
    words = _parse_shell_words(statement)
    expanded: list[str] = []
    for word in words:
        array_match = _SHELL_ARRAY.fullmatch(word)
        if array_match is not None:
            expanded.extend(arrays.get(array_match.group(1), (word,)))
        else:
            expanded.append(_resolve_shell_value(word, scalars))
    environment: dict[str, str] = {}
    while expanded and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", expanded[0]):
        name, value = expanded.pop(0).split("=", 1)
        environment[name] = value
    return expanded, environment


def shell_semantic_commands(text: str, *, include_function_bodies: bool = True) -> tuple[ShellCommand, ...]:
    """Project executable shell commands with bounded static alias resolution.

    The projection intentionally ignores comments and function/private variable
    spelling.  It resolves simple scalar/array aliases and command-local environment
    assignments, which is enough to make architecture checks representation-invariant
    without trying to become a shell interpreter.
    """

    functions, top_level = shell_functions(text)
    commands: list[ShellCommand] = []

    def scan(
        lines: list[tuple[int, str]],
        function: str | None,
        scalars: dict[str, str] | None = None,
        arrays: dict[str, tuple[str, ...]] | None = None,
        exports: set[str] | None = None,
        stack: tuple[str, ...] = (),
    ) -> tuple[dict[str, str], dict[str, tuple[str, ...]], set[str]]:
        local_scalars = {} if scalars is None else scalars
        local_arrays = {} if arrays is None else arrays
        local_exports = set() if exports is None else exports
        for line_number, line in lines:
            for raw_statement in _split_shell_statements(line):
                statement = _trim_shell_control(raw_statement)
                if not statement:
                    continue
                if _apply_shell_assignment(statement, local_scalars, local_arrays, local_exports):
                    continue
                argv, local_environment = _expand_shell_command(statement, local_scalars, local_arrays)
                if not argv:
                    continue
                command_name = argv[0]
                if command_name in functions and command_name not in stack:
                    nested_scalars = dict(local_scalars)
                    nested_arrays = dict(local_arrays)
                    nested_exports = set(local_exports)
                    for index, value in enumerate(argv[1:], start=1):
                        nested_scalars[str(index)] = value
                    scan(
                        functions[command_name],
                        command_name,
                        nested_scalars,
                        nested_arrays,
                        nested_exports,
                        (*stack, command_name),
                    )
                    continue
                environment = {
                    name: local_scalars.get(name, f"<VAR:{name}>")
                    for name in local_exports
                }
                environment.update(local_environment)
                commands.append(
                    ShellCommand(
                        argv=tuple(argv),
                        environment=tuple(sorted(environment.items())),
                        line_number=line_number,
                        function=function,
                    )
                )
        return local_scalars, local_arrays, local_exports

    globals_scalars, globals_arrays, globals_exports = scan(top_level, None)
    if include_function_bodies:
        for name, lines in functions.items():
            scan(
                lines,
                name,
                dict(globals_scalars),
                dict(globals_arrays),
                set(globals_exports),
                (name,),
            )
    return tuple(commands)


def _expand_make_value(value: str, variables: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return variables.get(name, match.group(0))

    previous: str | None = None
    for _ in range(20):
        if value == previous:
            break
        previous = value
        value = _MAKE_VARIABLE.sub(replace, value)
    return value


def make_semantic_facts(text: str) -> MakeSemanticFacts:
    """Parse active Make variables/recipes and resolve simple variable aliases."""

    raw_variables: dict[str, str] = {}
    variable_operators: dict[str, str] = {}
    raw_targets: dict[str, list[str]] = {}
    current_target: str | None = None
    for raw_line in text.splitlines():
        line = strip_shell_comment(raw_line).rstrip()
        if not line.strip():
            continue
        if raw_line.startswith("\t"):
            if current_target is not None:
                raw_targets.setdefault(current_target, []).append(raw_line[1:])
            continue
        current_target = None
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*(\?=|:=|\+=|=)\s*(.*)$", line)
        if match is not None:
            name, operator, value = match.groups()
            if operator == "+=":
                old = raw_variables.get(name, "")
                raw_variables[name] = (old + " " + value.strip()).strip()
                variable_operators[name] = operator
            elif operator == "?=":
                if name not in raw_variables:
                    raw_variables[name] = value.strip()
                    variable_operators[name] = operator
            else:
                raw_variables[name] = value.strip()
                variable_operators[name] = operator
            continue
        target_match = re.match(r"^([A-Za-z0-9_.-]+)\s*:(?:\s.*)?$", line)
        if target_match is not None and not target_match.group(1).startswith("."):
            current_target = target_match.group(1)
            raw_targets.setdefault(current_target, [])

    variables = dict(raw_variables)
    for _ in range(20):
        changed = False
        for name, value in list(variables.items()):
            expanded = _expand_make_value(value, variables)
            if expanded != value:
                variables[name] = expanded
                changed = True
        if not changed:
            break

    targets: dict[str, tuple[str, ...]] = {}
    target_commands: dict[str, tuple[ShellCommand, ...]] = {}
    for name, raw_lines in raw_targets.items():
        joined = join_shell_lines(list(enumerate(raw_lines, start=1)))
        resolved_lines = tuple(_expand_make_value(line, variables) for _, line in joined)
        targets[name] = resolved_lines
        target_commands[name] = shell_semantic_commands("\n".join(resolved_lines), include_function_bodies=False)
    return MakeSemanticFacts(
        variables=variables,
        raw_variables=dict(raw_variables),
        variable_operators=variable_operators,
        targets=targets,
        raw_targets={name: tuple(lines) for name, lines in raw_targets.items()},
        target_commands=target_commands,
    )
