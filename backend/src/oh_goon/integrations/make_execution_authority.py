from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from oh_goon.core.file_digest import sha256_file
from oh_goon.integrations.package_script_execution_authority import PackageExecutableAuthority
from oh_goon.integrations.shell_execution_authority import derive_shell_fragment_authority

_TARGET = re.compile(r"^(?P<target>[A-Za-z0-9_.-]+):(?P<rest>.*)$")
_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z0-9_]+)\s*(?::=|\?=|=)\s*(?P<value>.*)$")
_MAKE_REFERENCE = re.compile(r"\$\(MAKE\)|\$\{MAKE\}")
_RECURSIVE_MAKE_TARGET = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")
_RECURSIVE_MAKE_ASSIGNMENT = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)")
_VAR = re.compile(r"\$\((?P<name>[A-Za-z0-9_]+)\)")
_SIMPLE_TOOL = re.compile(r"^[A-Za-z0-9_.+-]+$")

_SHELL_WORDS = frozenset(
    {
        "!",
        ".",
        ":",
        "{",
        "}",
        "break",
        "case",
        "cd",
        "command",
        "continue",
        "do",
        "done",
        "echo",
        "elif",
        "else",
        "esac",
        "eval",
        "exec",
        "exit",
        "export",
        "false",
        "fi",
        "for",
        "if",
        "printf",
        "pwd",
        "read",
        "readonly",
        "return",
        "set",
        "shift",
        "test",
        "then",
        "trap",
        "true",
        "umask",
        "unset",
        "until",
        "while",
    }
)
_LOCAL_PREFIXES = (
    "./",
    "../",
    "backend/",
    "frontend/",
    "scripts/",
    "generated/",
    "templates/",
    "/workspace/",
)
_SHELL_PUNCTUATION = frozenset({"&&", "||", ";", "|", "&"})


@dataclass(frozen=True, slots=True)
class MakeTargetAuthority:
    target: str
    tools: tuple[str, ...]
    provenance: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    source_identities: tuple[tuple[str, str], ...]
    package_executables: tuple[PackageExecutableAuthority, ...]
    package_provenance: tuple[tuple[str, str], ...]
    materialized_executables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Target:
    prerequisites: tuple[str, ...]
    recipes: tuple[str, ...]


def _logical_lines(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for raw in text.splitlines():
        current = current + raw.lstrip() if current else raw
        if current.endswith("\\"):
            current = current[:-1] + " "
            continue
        out.append(current)
        current = ""
    if current:
        out.append(current)
    return out


def _merge_target(targets: dict[str, _Target], name: str, prerequisites: tuple[str, ...]) -> None:
    previous = targets.get(name)
    recipes = previous.recipes if previous is not None else ()
    inherited = previous.prerequisites if previous is not None else ()
    targets[name] = _Target(
        prerequisites=(*inherited, *prerequisites),
        recipes=recipes,
    )


def _parse_makefile(path: Path) -> tuple[dict[str, str], dict[str, _Target]]:
    variables: dict[str, str] = {}
    targets: dict[str, _Target] = {}
    current: str | None = None
    for line in _logical_lines(path.read_text(encoding="utf-8")):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assignment = _ASSIGNMENT.match(line) if not line.startswith("\t") else None
        if assignment is not None:
            variables[assignment.group("name")] = assignment.group("value").strip()
            current = None
            continue
        if line.startswith("\t"):
            if current is not None:
                node = targets[current]
                targets[current] = _Target(node.prerequisites, (*node.recipes, line[1:]))
            continue
        match = _TARGET.match(line)
        current = match.group("target") if match is not None else None
        if match is not None:
            rest = match.group("rest").split(";", 1)[0]
            prerequisites = tuple(
                token
                for token in rest.replace("|", " ").split()
                if token and not token.startswith("#")
            )
            _merge_target(targets, match.group("target"), prerequisites)
    return variables, targets


def _expand_leading_variable(command: str, variables: dict[str, str]) -> str:
    value = command.strip()
    for _ in range(4):
        match = _VAR.match(value)
        replacement = variables.get(match.group("name")) if match is not None else None
        if match is None or replacement is None:
            break
        value = replacement + value[match.end() :]
    return value


def _shell_tokens(recipe: str) -> tuple[str, ...]:
    lexer = shlex.shlex(recipe.lstrip("@-+").strip(), posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return tuple(lexer)


def _segments(recipe: str) -> tuple[str, ...]:
    try:
        tokens = _shell_tokens(recipe)
    except ValueError:
        return (recipe.lstrip("@-+").strip(),)
    parts: list[str] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_PUNCTUATION:
            if current:
                parts.append(shlex.join(current))
                current = []
            continue
        current.append(token)
    if current:
        parts.append(shlex.join(current))
    return tuple(parts)


def _recipe_requires_shell(recipe: str, variables: dict[str, str]) -> bool:
    value = recipe.lstrip("@-+").strip()
    expanded = _expand_leading_variable(value, variables)
    try:
        words = _shell_tokens(expanded)
    except ValueError:
        return True
    if not words:
        return False
    if any(word in _SHELL_PUNCTUATION for word in words):
        return True
    first = words[0]
    return ("=" in first and not first.startswith(("/", "./", "../"))) or first in _SHELL_WORDS


def _strip_command_group(value: str) -> str:
    result = value.strip()
    while result.startswith(("(", "{")):
        result = result[1:].lstrip()
    while result.endswith((")", "}")):
        result = result[:-1].rstrip()
    return result


def _command_words(segment: str, variables: dict[str, str]) -> tuple[list[str] | None, str | None]:
    expanded = _strip_command_group(_expand_leading_variable(segment, variables))
    if expanded.startswith("$(MAKE)") or expanded.startswith("${MAKE}"):
        return [], None
    if expanded.startswith("$(") or expanded.startswith("${"):
        return None, f"unresolved command variable: {segment.strip()}"
    try:
        return shlex.split(expanded, posix=True), None
    except ValueError:
        return None, f"unparseable recipe command: {segment.strip()}"


def _command_index(words: list[str]) -> int:
    index = 0
    while index < len(words):
        word = words[index]
        if "=" not in word or word.startswith(("/", "./", "../")):
            break
        name, _sep, _value = word.partition("=")
        if not name or not name.replace("_", "A").isalnum():
            break
        index += 1
    return index


def _resolve_variable_command(
    words: list[str], index: int, variables: dict[str, str]
) -> tuple[str | None, str | None] | None:
    variable = _VAR.fullmatch(words[index])
    if variable is None:
        return None
    if variable.group("name") == "MAKE":
        return None, None
    replacement = variables.get(variable.group("name"))
    if replacement is None:
        return None, f"unresolved command variable: {words[index]}"
    suffix = " ".join(shlex.quote(item) for item in words[index + 1 :])
    return _command_tool((replacement + " " + suffix).strip(), variables)


def _expand_segment_command(segment: str, variables: dict[str, str]) -> str:
    """Substitute bounded authored literals before shell quoting loses their shape.

    This does not evaluate Make functions or consult ambient variable values.
    Unproved bindings remain parameter expansions: data stays data, while an
    executable forwarded through a wrapper fails closed in the shell owner.
    """
    unknown = "${__OH_GOON_MAKEVAR__}"
    resolved: dict[str, str] = {}

    def expand(value: str, active: tuple[str, ...]) -> str:
        pieces: list[str] = []
        length = 0
        cursor = 0
        for match in _VAR.finditer(value):
            name = match.group("name")
            replacement = unknown
            if name == "MAKE":
                replacement = match.group(0)
            elif name in resolved:
                replacement = resolved[name]
            elif name not in active and len(active) < 16 and name in variables:
                authored = variables[name]
                # Anything beyond the existing simple-reference grammar belongs
                # to native Make. Do not reinterpret it as a shell executable.
                if "$" not in _VAR.sub("", authored):
                    replacement = expand(authored, (*active, name))
                resolved[name] = replacement
            literal = value[cursor : match.start()]
            length += len(literal) + len(replacement)
            if length > 65536:
                return unknown
            pieces.extend((literal, replacement))
            cursor = match.end()
        tail = value[cursor:]
        if length + len(tail) > 65536:
            return unknown
        pieces.append(tail)
        return "".join(pieces)

    return expand(segment, ()).replace("$$", "$")


def _classify_tool(word: str) -> tuple[str | None, str | None]:
    word = word.lstrip("({").rstrip(")}")
    if not word or word.isdigit():
        return None, None
    if word == "env":
        return "env", None
    if word in {"make", "gmake"} or word in _SHELL_WORDS:
        return None, None
    if word.startswith(_LOCAL_PREFIXES) or "/" in word:
        return None, None
    if _SIMPLE_TOOL.fullmatch(word) is None:
        return None, f"unresolved executable token: {word!r}"
    return word, None


def _command_tool(segment: str, variables: dict[str, str]) -> tuple[str | None, str | None]:
    words, issue = _command_words(segment, variables)
    if issue is not None or not words:
        return None, issue
    index = _command_index(words)
    if index >= len(words):
        return None, None
    resolved = _resolve_variable_command(words, index, variables)
    if resolved is not None:
        return resolved
    word = words[index]
    if word == "exec" and index + 1 < len(words):
        word = words[index + 1]
    return _classify_tool(word)


@dataclass(slots=True)
class _AuthorityBuilder:
    root: Path
    variables: dict[str, str]
    targets: dict[str, _Target]
    tools: set[str] = field(default_factory=set)
    provenance: set[tuple[str, str]] = field(default_factory=set)
    unresolved: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    package_executables: set[PackageExecutableAuthority] = field(default_factory=set)
    package_provenance: set[tuple[str, str]] = field(default_factory=set)
    materialized_executables: set[str] = field(default_factory=set)
    visiting: list[str] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)

    def require(self, tool: str, chain: tuple[str, ...]) -> None:
        self.tools.add(tool)
        self.provenance.add((tool, " -> ".join((*chain, tool))))

    def _visit_prerequisite(self, prerequisite: str, chain: tuple[str, ...]) -> None:
        if "$" in prerequisite or "%" in prerequisite:
            self.unresolved.add("dynamic make prerequisite: " + " -> ".join((*chain, prerequisite)))
            return
        if prerequisite in self.targets:
            self.visit(prerequisite, chain)

    def _visit_recursive_make(self, recipe: str, chain: tuple[str, ...]) -> None:
        # Only the existing literal target grammar is admitted. Native Make
        # retains option, alternate-Makefile and ambient configuration semantics.
        try:
            words = _shell_tokens(recipe)
        except ValueError:
            words = ()
        positions = [index for index, word in enumerate(words) if word == "$(MAKE)"]
        if len(positions) != 1 or len(_MAKE_REFERENCE.findall(recipe)) != 1:
            self.unresolved.add("unproven recursive make invocation: " + " -> ".join(chain))
            return
        children: list[str] = []
        bindings: dict[str, str] = {}
        for word in words[positions[0] + 1 :]:
            assignment = _RECURSIVE_MAKE_ASSIGNMENT.fullmatch(word)
            if assignment is not None:
                name, value = assignment.group("name", "value")
                if (
                    name.startswith("MAKE")
                    or name in {"MFLAGS", "GNUMAKEFLAGS", "SHELL"}
                    or any(marker in value for marker in ("$", "`"))
                ):
                    self.unresolved.add("unproven recursive make invocation: " + " -> ".join(chain))
                    return
                bindings[name] = value
            elif not word.startswith("-") and _RECURSIVE_MAKE_TARGET.fullmatch(word) is not None:
                children.append(word)
            else:
                self.unresolved.add("unproven recursive make invocation: " + " -> ".join(chain))
                return
        if not children:
            self.unresolved.add("unproven recursive make invocation: " + " -> ".join(chain))
            return
        # Literal call-site assignments must affect the same child analysis;
        # otherwise a command override could hide a different executable. Reuse
        # the existing owner with its parsed targets and current cycle context.
        previous_variables, previous_visited = self.variables, self.visited
        if bindings:
            self.variables = {**self.variables, **bindings}
            self.visited = set()
        try:
            for child in children:
                if child in self.targets:
                    self.visit(child, chain)
                else:
                    self.unresolved.add(
                        "missing recursive make target: " + " -> ".join((*chain, child))
                    )
        finally:
            self.variables, self.visited = previous_variables, previous_visited

    def _scan_recipe(self, recipe: str, chain: tuple[str, ...]) -> None:
        expanded_recipe = _expand_segment_command(recipe, self.variables)
        if _recipe_requires_shell(expanded_recipe, self.variables):
            self.require("sh", (*chain, "<recipe-shell>"))
        for segment in _segments(expanded_recipe):
            tool, issue = _command_tool(segment, self.variables)
            if tool is not None:
                self.require(tool, chain)
            if issue is not None:
                self.unresolved.add(" -> ".join(chain) + ": " + issue)
            if "$(MAKE)" in segment or "${MAKE}" in segment:
                self._visit_recursive_make(segment, chain)
                continue
            shell = derive_shell_fragment_authority(self.root, segment, chain=chain)
            self.tools.update(shell.tools)
            self.provenance.update(shell.provenance)
            self.unresolved.update(shell.unresolved)
            self.sources.update(shell.scripts)
            self.sources.update(shell.package_sources)
            self.package_executables.update(shell.package_executables)
            self.package_provenance.update(shell.package_provenance)
            self.materialized_executables.update(shell.materialized_executables)

    def visit(self, name: str, parent_chain: tuple[str, ...]) -> None:
        if name in self.visited:
            return
        if name in self.visiting:
            self.unresolved.add("make target cycle: " + " -> ".join((*parent_chain, name)))
            return
        node = self.targets.get(name)
        if node is None:
            self.unresolved.add("missing make target: " + " -> ".join((*parent_chain, name)))
            return
        self.visiting.append(name)
        chain = (*parent_chain, name)
        for prerequisite in node.prerequisites:
            self._visit_prerequisite(prerequisite, chain)
        for recipe in node.recipes:
            self._scan_recipe(recipe, chain)
        self.visiting.pop()
        self.visited.add(name)


def derive_make_target_authority(root: Path, target: str) -> MakeTargetAuthority:
    """Derive a project-owned Make execution closure without running Make."""

    variables, targets = _parse_makefile(root.resolve() / "Makefile")
    builder = _AuthorityBuilder(root=root.resolve(), variables=variables, targets=targets)
    builder.visit(target, ())
    source_paths = {"Makefile", *builder.sources}
    source_identities = tuple(
        sorted(
            (relative, "sha256:" + sha256_file(root.resolve() / relative))
            for relative in source_paths
        )
    )
    return MakeTargetAuthority(
        target=target,
        tools=tuple(sorted(builder.tools)),
        provenance=tuple(sorted(builder.provenance)),
        unresolved=tuple(sorted(builder.unresolved)),
        source_identities=source_identities,
        package_executables=tuple(sorted(builder.package_executables)),
        package_provenance=tuple(sorted(builder.package_provenance)),
        materialized_executables=tuple(sorted(builder.materialized_executables)),
    )
