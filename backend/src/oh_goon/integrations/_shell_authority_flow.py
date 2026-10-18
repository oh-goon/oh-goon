from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from oh_goon.integrations._shell_authority_syntax import is_function_definition


def _resolve_assignment(line: str, known: dict[str, str]) -> tuple[str, str] | None:
    match = re.fullmatch(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s;]+)", line)
    if match is None:
        return None
    name = match.group("name")
    value = match.group("value")
    default = re.fullmatch(r"\$\{(?P<source>[A-Za-z_][A-Za-z0-9_]*):-(?P<default>[^}]*)\}", value)
    if default is not None:
        return name, known.get(default.group("source"), default.group("default"))
    required = re.fullmatch(r"\$\{(?P<source>[A-Za-z_][A-Za-z0-9_]*):\?[^}]*\}", value)
    if required is not None and required.group("source") in known:
        return name, known[required.group("source")]
    if "$" not in value and "`" not in value:
        return name, value.strip("\"'")
    return None


def _known_test_condition(line: str, known: dict[str, str]) -> bool | None:
    match = re.fullmatch(
        r"if\s+\[\s+[\"']?\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']?\s+"
        r"(?P<op>=|!=)\s+[\"']?(?P<value>[^\s\"']+)[\"']?\s+\];\s+then",
        line,
    )
    if match is None or match.group("name") not in known:
        return None
    equal = known[match.group("name")] == match.group("value")
    return equal if match.group("op") == "=" else not equal


def _case_variable(line: str) -> str | None:
    match = re.fullmatch(r"case\s+[\"']?\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']?\s+in", line)
    return match.group("name") if match is not None else None


@dataclass(slots=True)
class _Flow:
    known: dict[str, str]
    frames: list[dict[str, object]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)

    def active(self) -> bool:
        return all(bool(frame["active"]) for frame in self.frames)

    def consume(self, raw: str) -> None:
        line = raw.strip()
        if not line or line.startswith("#") or is_function_definition(line):
            return
        if self._case_control(line):
            return
        if self._if_control(line):
            return
        if not self.active():
            return
        assignment = _resolve_assignment(line, self.known)
        if assignment is not None:
            self.known[assignment[0]] = assignment[1]
            return
        self.commands.append(line)

    def _case_control(self, line: str) -> bool:
        name = _case_variable(line)
        if name is not None:
            self.frames.append(
                {
                    "kind": "case",
                    "active": False,
                    "parent": self.active(),
                    "value": self.known.get(name),
                    "matched": False,
                }
            )
            return True
        if line == "esac" and self.frames and self.frames[-1]["kind"] == "case":
            self.frames.pop()
            return True
        if not self.frames or self.frames[-1]["kind"] != "case" or ")" not in line:
            return False
        pattern, remainder = line.split(")", 1)
        self._select_case_branch(pattern)
        if remainder.strip():
            self.consume(remainder.strip())
        return True

    def _select_case_branch(self, pattern: str) -> None:
        frame = self.frames[-1]
        value = frame["value"]
        already = bool(frame["matched"])
        branch = True if not isinstance(value, str) else _case_matches(pattern, value, already)
        frame["matched"] = already or branch
        frame["active"] = bool(frame["parent"]) and branch

    def _if_control(self, line: str) -> bool:
        if line.startswith("if ") and line.endswith("; then"):
            condition = _known_test_condition(line, self.known)
            parent = self.active()
            self.frames.append(
                {
                    "kind": "if",
                    "active": parent and (condition if condition is not None else True),
                    "parent": parent,
                    "condition": condition,
                }
            )
            if condition is None:
                self.commands.append(line)
            return True
        if line == "else" and self.frames and self.frames[-1]["kind"] == "if":
            self._flip_if()
            return True
        if line == "fi" and self.frames and self.frames[-1]["kind"] == "if":
            self.frames.pop()
            return True
        return False

    def _flip_if(self) -> None:
        frame = self.frames[-1]
        condition = frame["condition"]
        branch = (not bool(condition)) if condition is not None else True
        frame["active"] = bool(frame["parent"]) and branch


def _case_matches(pattern: str, value: str, already_matched: bool) -> bool:
    if already_matched:
        return False
    return any(fnmatchcase(value, item.strip()) for item in pattern.split("|") if item.strip())


def commands_from_script_lines(
    lines: tuple[str, ...], environment: dict[str, str]
) -> tuple[str, ...]:
    flow = _Flow(known=dict(environment))
    for line in lines:
        flow.consume(line)
    return tuple(flow.commands)
