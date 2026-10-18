from __future__ import annotations

import re
from dataclasses import dataclass

from oh_goon.core.errors import InvalidRequest

_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
_CLAUSE = re.compile(r"^(<=|>=|<|>|==)\s*([0-9]+(?:\.[0-9]+)*)$")


@dataclass(frozen=True, slots=True)
class NumericVersionClause:
    operator: str
    version: tuple[int, ...]


def parse_numeric_version(value: str) -> tuple[int, ...] | None:
    value = value.strip()
    if not _VERSION.fullmatch(value):
        return None
    return tuple(int(part) for part in value.split("."))


def parse_version_specifier(value: str) -> tuple[NumericVersionClause, ...]:
    clauses: list[NumericVersionClause] = []
    for raw in value.split(","):
        match = _CLAUSE.fullmatch(raw.strip())
        if match is None:
            raise InvalidRequest(
                "Affected version range must use stable numeric comparisons such as <2.18.0 or >=2.0,<2.18.0"
            )
        clauses.append(
            NumericVersionClause(
                operator=match.group(1),
                version=tuple(int(part) for part in match.group(2).split(".")),
            )
        )
    if not clauses:
        raise InvalidRequest("Affected version range must contain at least one comparison")
    return tuple(clauses)


def version_matches_specifier(version: str, specifier: str) -> bool | None:
    parsed = parse_numeric_version(version)
    if parsed is None:
        return None
    return all(_matches(parsed, clause) for clause in parse_version_specifier(specifier))


def _matches(version: tuple[int, ...], clause: NumericVersionClause) -> bool:
    width = max(len(version), len(clause.version))
    left = version + (0,) * (width - len(version))
    right = clause.version + (0,) * (width - len(clause.version))
    if clause.operator == "<":
        return left < right
    if clause.operator == "<=":
        return left <= right
    if clause.operator == ">":
        return left > right
    if clause.operator == ">=":
        return left >= right
    return left == right
