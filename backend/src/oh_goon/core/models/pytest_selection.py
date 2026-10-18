from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


class StructuredPytestMemberError(ValueError):
    """Fail-closed error for one admitted structured pytest member."""


def _identity(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _contains_control_character(value: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in value)


def _validate_path(path: str) -> None:
    if not path:
        raise StructuredPytestMemberError("pytest member path must be nonempty")
    if _contains_control_character(path):
        raise StructuredPytestMemberError("pytest member path must not contain control characters")
    if "::" in path:
        raise StructuredPytestMemberError("pytest member path must not embed a pytest node separator")
    if "\\" in path or _DRIVE_PREFIX.match(path):
        raise StructuredPytestMemberError("pytest member path must use unambiguous repository-relative POSIX separators")
    if path.startswith("/"):
        raise StructuredPytestMemberError("pytest member path must be repository-relative")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise StructuredPytestMemberError("pytest member path must already be lexically normalized")


def _bracket_depth(char: str, depth: int) -> int:
    if char == "[":
        return depth + 1
    if char == "]":
        if depth == 0:
            raise StructuredPytestMemberError("pytest member test_symbol has an unmatched closing bracket")
        return depth - 1
    return depth


def _require_component(value: str) -> str:
    if not value:
        raise StructuredPytestMemberError("pytest member test_symbol contains an empty node component")
    return value


def _split_symbol(symbol: str) -> tuple[str, ...]:
    if not symbol or _contains_control_character(symbol):
        raise StructuredPytestMemberError("pytest member test_symbol must be nonempty and free of control characters")
    parts: list[str] = []
    start = 0
    bracket_depth = 0
    index = 0
    while index < len(symbol):
        previous = bracket_depth
        bracket_depth = _bracket_depth(symbol[index], bracket_depth)
        if previous == bracket_depth == 0 and symbol.startswith("::", index):
            parts.append(_require_component(symbol[start:index]))
            index += 2
            start = index
            continue
        index += 1
    if bracket_depth:
        raise StructuredPytestMemberError("pytest member test_symbol has an unmatched opening bracket")
    parts.append(_require_component(symbol[start:]))
    return tuple(parts)


@dataclass(frozen=True, slots=True)
class StructuredPytestMember:
    """Exact admitted member plus an Oh-Goon-safe structured pytest interpretation.

    `member_id` remains the producer-owned logical identity. The projection identity is
    execution-facing evidence only and never replaces the producer membership identity.
    """

    path: str
    test_symbol: str | None
    member_id: str
    node_components: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not isinstance(self.member_id, str):
            raise StructuredPytestMemberError("pytest member path/member_id must be strings")
        _validate_path(self.path)
        if not _SHA256.fullmatch(self.member_id):
            raise StructuredPytestMemberError("pytest member_id must be a sha256 identity")
        if self.test_symbol is not None and not isinstance(self.test_symbol, str):
            raise StructuredPytestMemberError("pytest member test_symbol must be string or null")
        components = () if self.test_symbol is None else _split_symbol(self.test_symbol)
        object.__setattr__(self, "node_components", components)

    @property
    def executable_selector(self) -> str:
        if not self.node_components:
            return self.path
        return self.path + "::" + "::".join(self.node_components)

    @property
    def projection_identity(self) -> str:
        return _identity(
            {
                "schema": "oh-goon.structured-pytest-member/v1",
                "path": self.path,
                "test_symbol": self.test_symbol,
                "member_id": self.member_id,
                "node_components": list(self.node_components),
            }
        )

    def evidence_payload(self) -> dict[str, object]:
        return {
            "path": self.path,
            "test_symbol": self.test_symbol,
            "member_id": self.member_id,
            "node_components": list(self.node_components),
            "projection_identity": self.projection_identity,
        }
