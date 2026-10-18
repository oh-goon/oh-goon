from __future__ import annotations

import re
import shlex
from pathlib import Path, PurePosixPath

from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.core.source_tree_policy import (
    canonical_repository_relative_path,
    is_package_manager_materialization_path,
)

FORWARD = re.compile(r"^# OH_GOON_FORWARD_ARGV_FROM: (?P<position>[1-9][0-9]*)$")
FUNCTION = re.compile(r"^\s*(?:function\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{")
ASSIGNMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SIMPLE_TOOL = re.compile(r"^[A-Za-z0-9_.+-]+$")
LOCAL_PREFIXES = ("./", "../", "backend/", "frontend/", "scripts/", "generated/", "templates/")
CONTROL_WORDS = frozenset(
    {
        "!",
        ".",
        ":",
        "[",
        "[[",
        "{",
        "}",
        ";;",
        "break",
        "case",
        "cd",
        "continue",
        "declare",
        "do",
        "done",
        "echo",
        "elif",
        "else",
        "esac",
        "exit",
        "export",
        "false",
        "fi",
        "for",
        "if",
        "jobs",
        "local",
        "mapfile",
        "printf",
        "pwd",
        "read",
        "readonly",
        "return",
        "set",
        "shift",
        "source",
        "test",
        "typeset",
        "then",
        "true",
        "umask",
        "unset",
        "until",
        "wait",
        "while",
    }
)
SHELLS = frozenset({"sh", "bash"})
PUNCTUATION = frozenset({"&&", "||", ";", "|", "&"})
_ESCAPED_FIND_TERMINATOR = "__OH_GOON_ESCAPED_FIND_TERMINATOR__"
XARGS_OPTION_WITH_VALUE = frozenset(
    {
        "-a",
        "--arg-file",
        "-E",
        "--eof",
        "-I",
        "--replace",
        "-L",
        "--max-lines",
        "-n",
        "--max-args",
        "-P",
        "--max-procs",
        "-s",
        "--max-chars",
    }
)
_PROCESS_LAUNCHERS = frozenset({"timeout", "nice", "nohup", "stdbuf", "setsid"})
_TIMEOUT_OPTION_WITH_VALUE = frozenset({"-k", "--kill-after", "-s", "--signal"})
_TIMEOUT_FLAG_OPTIONS = frozenset({"--foreground", "--preserve-status", "-v", "--verbose"})
_NICE_OPTION_WITH_VALUE = frozenset({"-n", "--adjustment"})
_SETSID_FLAG_OPTIONS = frozenset({"-c", "--ctty", "-f", "--fork", "-w", "--wait"})
_STDBUF_SHORT_WITH_VALUE = frozenset({"-i", "-o", "-e"})
_STDBUF_LONG_WITH_VALUE = frozenset({"--input", "--output", "--error"})


def logical_lines(text: str) -> tuple[str, ...]:
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        current = current + raw.strip() if current else raw.strip()
        if current.endswith("\\"):
            current = current[:-1] + " "
            continue
        lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return tuple(lines)


def forward_position(lines: tuple[str, ...], script: str) -> int | None:
    positions = [
        int(match.group("position")) for line in lines if (match := FORWARD.fullmatch(line))
    ]
    if len(positions) > 1:
        raise ValueError(f"{script}: OH_GOON_FORWARD_ARGV_FROM must be unique")
    return positions[0] if positions else None


def function_names(lines: tuple[str, ...]) -> frozenset[str]:
    return frozenset(match.group("name") for line in lines if (match := FUNCTION.match(line)))


def is_function_definition(line: str) -> bool:
    return FUNCTION.match(line) is not None


def _quoted_step(text: str, index: int, quote: str | None) -> tuple[int, str | None]:
    char = text[index]
    if char == "\\" and quote != "'":
        return min(index + 2, len(text)), quote
    if char not in {"'", '"'}:
        return index + 1, quote
    if quote is None:
        return index + 1, char
    return index + 1, None if quote == char else quote


def _substitution_step(
    text: str, index: int, quote: str | None, depth: int
) -> tuple[int, str | None, int]:
    char = text[index]
    if char == "\\" and quote != "'":
        return min(index + 2, len(text)), quote, depth
    if char in {"'", '"'}:
        next_index, next_quote = _quoted_step(text, index, quote)
        return next_index, next_quote, depth
    if quote is None and char == "(":
        return index + 1, quote, depth + 1
    if quote is None and char == ")":
        return index + 1, quote, depth - 1
    return index + 1, quote, depth


def _find_substitution_end(text: str, start: int) -> int | None:
    depth = 1
    index = start
    quote: str | None = None
    while index < len(text) and depth:
        previous = index
        index, quote, depth = _substitution_step(text, index, quote, depth)
        if depth == 0:
            return previous
    return None


def _next_substitution_start(text: str, start: int) -> int | None:
    index = start
    quote: str | None = None
    while index < len(text):
        char = text[index]
        if char == "\\" and quote != "'":
            index = min(index + 2, len(text))
            continue
        if char in {"'", '"'}:
            index, quote = _quoted_step(text, index, quote)
            continue
        if quote != "'" and text.startswith("$(", index):
            if text.startswith("$((", index):
                index += 3
                continue
            return index
        index += 1
    return None


def extract_command_substitutions(fragment: str) -> tuple[str, tuple[str, ...]]:
    pieces: list[str] = []
    substitutions: list[str] = []
    cursor = 0
    emit_from = 0
    while (found := _next_substitution_start(fragment, cursor)) is not None:
        end = _find_substitution_end(fragment, found + 2)
        if end is None:
            cursor = found + 2
            continue
        pieces.extend((fragment[emit_from:found], "__OH_GOON_SUBSTITUTION__"))
        substitutions.append(fragment[found + 2 : end])
        emit_from = end + 1
        cursor = end + 1
    pieces.append(fragment[emit_from:])
    return "".join(pieces), tuple(substitutions)


def _balance_char(token: str, opening: str, closing: str, depth: int) -> int:
    if token == opening:
        return depth + 1
    if token == closing:
        return depth - 1
    return depth


def _balanced_char_end(text: str, start: int, opening: str, closing: str) -> int | None:
    depth = 1
    cursor = start
    while cursor < len(text) and depth:
        depth = _balance_char(text[cursor], opening, closing, depth)
        cursor += 1
    return cursor if depth == 0 else None


def _arithmetic_end(text: str, start: int) -> int | None:
    depth = 1
    cursor = start
    while cursor + 1 < len(text):
        token = text[cursor : cursor + 2]
        if token == "((":
            depth += 1
            cursor += 2
            continue
        if token == "))":
            depth -= 1
            cursor += 2
            if depth == 0:
                return cursor
            continue
        cursor += 1
    return None


def mask_noncommand_expansions(fragment: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(fragment):
        if fragment.startswith("${", index):
            end = _balanced_char_end(fragment, index + 2, "{", "}")
            if end is not None:
                result.append("__OH_GOON_PARAM__")
                index = end
                continue
        if fragment.startswith("$((", index):
            end = _arithmetic_end(fragment, index + 3)
            if end is not None:
                result.append("__OH_GOON_ARITH__")
                index = end
                continue
        result.append(fragment[index])
        index += 1
    return "".join(result)


def _protect_escaped_find_terminators(command: str) -> str:
    """Keep ``find -exec ... \\;`` terminators distinct from shell separators."""

    result: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(command):
        char = command[index]
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            result.append(char)
            index += 1
            continue
        if (
            quote is None
            and char == "\\"
            and index + 1 < len(command)
            and command[index + 1] == ";"
        ):
            result.append(_ESCAPED_FIND_TERMINATOR)
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def shell_tokens(command: str) -> tuple[str, ...] | None:
    try:
        lexer = shlex.shlex(
            _protect_escaped_find_terminators(command), posix=True, punctuation_chars=";&|"
        )
        lexer.whitespace_split = True
        lexer.commenters = "#"
        return tuple(lexer)
    except ValueError:
        return None


def segments(command: str) -> tuple[tuple[str, ...], ...] | None:
    tokens = shell_tokens(command)
    if tokens is None:
        return None
    parts: list[tuple[str, ...]] = []
    current: list[str] = []
    for token in tokens:
        if token in PUNCTUATION or (token and set(token) <= set(";&|")):
            if current:
                parts.append(tuple(current))
                current = []
            continue
        current.append(token)
    if current:
        parts.append(tuple(current))
    return tuple(parts)


def command_index(words: tuple[str, ...]) -> int:
    index = 0
    while index < len(words) and ASSIGNMENT_NAME.match(words[index]):
        index += 1
    while index < len(words) and words[index] in {
        "if",
        "elif",
        "then",
        "while",
        "until",
        "do",
        "!",
    }:
        index += 1
    return index


def materialized_executable_identity(
    token: str,
    *,
    working_directory: str = ".",
) -> str | None:
    """Return the canonical repository identity for package-manager executable supply."""

    value = token.strip("\"'").lstrip("({").rstrip(")}").replace("\\", "/")
    if not value or any(marker in value for marker in ("$", "`", "*", "?", "[")):
        return None
    raw = PurePosixPath(value)
    try:
        relative = (
            canonical_repository_relative_path(raw.relative_to(SANDBOX_PATHS.workspace))
            if raw.is_absolute() and raw.is_relative_to(SANDBOX_PATHS.workspace)
            else canonical_repository_relative_path(
                value,
                working_directory=working_directory,
            )
        )
    except ValueError:
        return None
    if not is_package_manager_materialization_path(relative):
        return None
    return relative.as_posix()


def is_materialized_executable_path(token: str) -> bool:
    """Return whether a command path belongs to package-manager materialization.

    Static authority is derived from repository source plus package-manager authority.
    Generated console wrappers under .venv/node_modules are runtime materialization and
    must not be reinterpreted as repository-owned shell when they happen to exist.
    """

    return materialized_executable_identity(token) is not None


def project_path(root: Path, token: str) -> Path | None:
    value = token.strip("\"'")
    if not value or "$" in value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return _contained_absolute(root, candidate)
    if not value.startswith(LOCAL_PREFIXES):
        return None
    return _contained_absolute(root, root / candidate)


def _contained_absolute(root: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def external_tool(root: Path, token: str) -> str | None:
    value = token.strip("\"'").lstrip("({").rstrip(")}")
    if _ignored_external_token(value) or project_path(root, value) is not None:
        return None
    if value.startswith("/"):
        workspace = str(SANDBOX_PATHS.workspace)
        return (
            None
            if value == workspace or value.startswith(f"{workspace}/")
            else (Path(value).name or None)
        )
    if "/" in value:
        return None
    return value if SIMPLE_TOOL.fullmatch(value) is not None else None


def _ignored_external_token(value: str) -> bool:
    return (
        not value
        or value in {"-", "--", "__OH_GOON_MAKEVAR__"}
        or value in CONTROL_WORDS
        or value.isdigit()
    )


def shebang_tools(path: Path) -> tuple[str, ...]:
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return ()
    if not first.startswith("#!"):
        return ()
    try:
        words = shlex.split(first[2:].strip(), posix=True)
    except ValueError:
        return ()
    if not words:
        return ()
    executable = Path(words[0]).name
    return ("env", words[1]) if executable == "env" and len(words) >= 2 else (executable,)


def prefix_environment(
    words: tuple[str, ...], index: int, environment: dict[str, str]
) -> dict[str, str]:
    result = dict(environment)
    result.update(_literal_assignments(words[:index]))
    return result


def _literal_assignments(words: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for word in words:
        if not ASSIGNMENT_NAME.match(word):
            continue
        name, _sep, value = word.partition("=")
        if name and "$" not in value and "`" not in value:
            result[name] = value.strip("\"'")
    return result


def env_child_index(words: tuple[str, ...], index: int) -> int | None:
    cursor = index + 1
    while cursor < len(words):
        word = words[cursor]
        if word == "--":
            cursor += 1
            break
        if word.startswith("-") or ASSIGNMENT_NAME.match(word):
            cursor += 1
            continue
        break
    return cursor if cursor < len(words) else None


def env_assignments(words: tuple[str, ...], start: int, end: int) -> dict[str, str]:
    return _literal_assignments(tuple(words[start:end]))


def xargs_child_index(words: tuple[str, ...], index: int) -> int | None:
    cursor = index + 1
    while cursor < len(words):
        word = words[cursor]
        if word == "--":
            cursor += 1
            break
        option_name = word.split("=", 1)[0]
        if not word.startswith("-"):
            break
        cursor += 1
        if option_name in XARGS_OPTION_WITH_VALUE and "=" not in word and cursor < len(words):
            cursor += 1
    return cursor if cursor < len(words) else None


def _grouped_shell_tokens(command: str) -> tuple[str, ...] | None:
    try:
        lexer = shlex.shlex(
            _protect_escaped_find_terminators(command),
            posix=True,
            punctuation_chars=";&|()",
        )
        lexer.whitespace_split = True
        lexer.commenters = "#"
        raw = tuple(lexer)
    except ValueError:
        return None
    result: list[str] = []
    for token in raw:
        if token and set(token) <= set(";&|()"):
            cursor = 0
            while cursor < len(token):
                pair = token[cursor : cursor + 2]
                if pair in {"&&", "||", ";;"}:
                    result.append(pair)
                    cursor += 2
                else:
                    result.append(token[cursor])
                    cursor += 1
            continue
        result.append(token)
    return tuple(result)


def _process_launcher_child_index(words: tuple[str, ...], index: int) -> int | None:
    command = words[index]
    cursor = index + 1
    if command == "timeout":
        while cursor < len(words):
            word = words[cursor]
            if word == "--":
                cursor += 1
                break
            option = word.split("=", 1)[0]
            if option in _TIMEOUT_OPTION_WITH_VALUE:
                cursor += 1
                if "=" not in word:
                    if cursor >= len(words):
                        return None
                    cursor += 1
                continue
            if word in _TIMEOUT_FLAG_OPTIONS:
                cursor += 1
                continue
            if word.startswith("-"):
                return None
            break
        if cursor >= len(words):
            return None
        cursor += 1  # duration
        return cursor if cursor < len(words) else None
    if command == "nice":
        while cursor < len(words):
            word = words[cursor]
            if word == "--":
                cursor += 1
                break
            option = word.split("=", 1)[0]
            if option in _NICE_OPTION_WITH_VALUE:
                cursor += 1
                if "=" not in word:
                    if cursor >= len(words):
                        return None
                    cursor += 1
                continue
            if re.fullmatch(r"-[0-9]+", word):
                cursor += 1
                continue
            if word.startswith("-"):
                return None
            break
        return cursor if cursor < len(words) else None
    if command == "nohup":
        if cursor < len(words) and words[cursor] == "--":
            cursor += 1
        elif cursor < len(words) and words[cursor].startswith("-"):
            return None
        return cursor if cursor < len(words) else None
    if command == "setsid":
        while cursor < len(words):
            word = words[cursor]
            if word == "--":
                cursor += 1
                break
            if word in _SETSID_FLAG_OPTIONS:
                cursor += 1
                continue
            if word.startswith("-"):
                return None
            break
        return cursor if cursor < len(words) else None
    if command == "stdbuf":
        while cursor < len(words):
            word = words[cursor]
            if word == "--":
                cursor += 1
                break
            option = word.split("=", 1)[0]
            if option in _STDBUF_LONG_WITH_VALUE:
                cursor += 1
                if "=" not in word:
                    if cursor >= len(words):
                        return None
                    cursor += 1
                continue
            if word in _STDBUF_SHORT_WITH_VALUE:
                cursor += 1
                if cursor >= len(words):
                    return None
                cursor += 1
                continue
            if any(
                word.startswith(prefix) and len(word) > len(prefix)
                for prefix in _STDBUF_SHORT_WITH_VALUE
            ):
                cursor += 1
                continue
            if word.startswith("-"):
                return None
            break
        return cursor if cursor < len(words) else None
    return None


def shell_child_words(words: tuple[str, ...], index: int) -> tuple[str, ...] | None:
    """Return a statically provable child command for shell/process launch wrappers."""

    command = words[index]
    if command == "env":
        child = env_child_index(words, index)
        return tuple(words[child:]) if child is not None else None
    if command == "xargs":
        child = xargs_child_index(words, index)
        return tuple(words[child:]) if child is not None else None
    if command in {"command", "builtin"}:
        cursor = index + 1
        while cursor < len(words) and words[cursor].startswith("-"):
            cursor += 1
        return tuple(words[cursor:]) if cursor < len(words) else None
    if command == "exec":
        return tuple(words[index + 1 :]) if index + 1 < len(words) else None
    if command in _PROCESS_LAUNCHERS:
        child = _process_launcher_child_index(words, index)
        return tuple(words[child:]) if child is not None else None
    return None


def find_exec_child_words(words: tuple[str, ...], index: int) -> tuple[tuple[str, ...], ...]:
    """Return each statically delimited child argv launched by find -exec/-execdir."""

    children: list[tuple[str, ...]] = []
    cursor = index + 1
    while cursor < len(words):
        if words[cursor] not in {"-exec", "-execdir"}:
            cursor += 1
            continue
        start = cursor + 1
        end = start
        while end < len(words) and words[end] not in {";", "+", _ESCAPED_FIND_TERMINATOR}:
            end += 1
        if start < end:
            children.append(tuple(words[start:end]))
        cursor = end + 1
    return tuple(children)


def _literal_cd_working_directory(
    words: tuple[str, ...],
    index: int,
    working_directory: str,
) -> str | None:
    cursor = index + 1
    while cursor < len(words) and words[cursor] in {"-L", "-P", "--"}:
        cursor += 1
    if cursor >= len(words):
        return None
    target = words[cursor]
    if any(marker in target for marker in ("$", "`", "*", "?", "[")):
        return None
    try:
        return canonical_repository_relative_path(
            target,
            working_directory=working_directory,
        ).as_posix()
    except ValueError:
        return None


def _materialized_from_shell_words(
    words: tuple[str, ...],
    working_directory: str,
) -> tuple[set[str], str]:
    if not words:
        return set(), working_directory
    index = command_index(words)
    if index >= len(words):
        return set(), working_directory
    command = words[index].lstrip("{").rstrip("}")
    if command == "cd":
        changed = _literal_cd_working_directory(words, index, working_directory)
        return set(), changed if changed is not None else working_directory

    child = shell_child_words(words, index)
    if child:
        return _materialized_from_shell_words(child, working_directory)

    if command == "find":
        found: set[str] = set()
        for find_child in find_exec_child_words(words, index):
            matches, _ = _materialized_from_shell_words(find_child, working_directory)
            found.update(matches)
        return found, working_directory

    if command in SHELLS:
        cursor = index + 1
        while cursor < len(words) and words[cursor].startswith("-") and words[cursor] != "-":
            option = words[cursor]
            cursor += 1
            if option == "-c" and cursor < len(words):
                return set(
                    materialized_executables_in_shell(
                        words[cursor],
                        working_directory=working_directory,
                    )
                ), working_directory
        return set(), working_directory

    identity = materialized_executable_identity(
        command,
        working_directory=working_directory,
    )
    return ({identity} if identity is not None else set()), working_directory


def materialized_executables_in_shell(
    fragment: str,
    *,
    working_directory: str = ".",
) -> tuple[str, ...]:
    """Find package-manager executable supply under bounded literal shell cwd changes."""

    try:
        initial = canonical_repository_relative_path(
            ".",
            working_directory=working_directory,
        ).as_posix()
    except ValueError:
        return ()
    tokens = _grouped_shell_tokens(fragment)
    if tokens is None:
        return ()

    def scan_sequence(sequence: tuple[str, ...], cwd: str) -> set[str]:
        found: set[str] = set()
        words: list[str] = []
        cursor = 0

        def flush(current_cwd: str) -> str:
            nonlocal words
            matches, next_cwd = _materialized_from_shell_words(tuple(words), current_cwd)
            found.update(matches)
            words = []
            return next_cwd

        while cursor < len(sequence):
            token = sequence[cursor]
            if token == "(":
                cwd = flush(cwd)
                depth = 1
                end = cursor + 1
                while end < len(sequence) and depth:
                    if sequence[end] == "(":
                        depth += 1
                    elif sequence[end] == ")":
                        depth -= 1
                    end += 1
                if depth:
                    return found
                found.update(scan_sequence(sequence[cursor + 1 : end - 1], cwd))
                cursor = end
                continue
            if token == ")":
                cwd = flush(cwd)
                cursor += 1
                continue
            if token in {"&&", "||", ";", ";;", "|", "&"}:
                cwd = flush(cwd)
                cursor += 1
                continue
            words.append(token)
            cursor += 1
        flush(cwd)
        return found

    return tuple(sorted(scan_sequence(tokens, initial)))
