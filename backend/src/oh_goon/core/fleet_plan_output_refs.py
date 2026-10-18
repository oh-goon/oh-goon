from __future__ import annotations

import re
from collections.abc import Callable

_STEP_OUTPUT_REFERENCE = re.compile(
    r"\$\{\{\s*steps\.([A-Za-z0-9_.\-\[\]=]+)\.outputs\.([A-Za-z_][A-Za-z0-9_-]{0,63})\s*\}\}"
)
_GOON_OUTPUT_REFERENCE = re.compile(
    r"\$\{\{\s*goons\.([A-Za-z0-9_.\-\[\]=]+)\.outputs\.([A-Za-z_][A-Za-z0-9_-]{0,63})\s*\}\}"
)
_GOON_WORKSPACE_REFERENCE = re.compile(
    r"\$\{\{\s*goons\.([A-Za-z0-9_.\-\[\]=]+)\.workspace\s*\}\}"
)


def step_output_reference(value: str) -> tuple[str, str] | None:
    match = _STEP_OUTPUT_REFERENCE.fullmatch(value)
    return (match.group(1), match.group(2)) if match else None


def step_output_references(value: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (match.group(1), match.group(2)) for match in _STEP_OUTPUT_REFERENCE.finditer(value)
    )


def replace_step_output_references(
    value: str,
    replace: Callable[[str, str], str | None],
) -> str:
    """Rewrite embedded Step-output references while preserving untouched source text."""

    def replacement(match: re.Match[str]) -> str:
        updated = replace(match.group(1), match.group(2))
        return match.group(0) if updated is None else updated

    return _STEP_OUTPUT_REFERENCE.sub(replacement, value)


def goon_output_reference(value: str) -> tuple[str, str] | None:
    match = _GOON_OUTPUT_REFERENCE.fullmatch(value)
    return (match.group(1), match.group(2)) if match else None


def goon_workspace_reference(value: str) -> str | None:
    match = _GOON_WORKSPACE_REFERENCE.fullmatch(value)
    return match.group(1) if match else None
