"""Execution tool-name policy and physical workspace path classification.

Authored Make/shell syntax and relationships are observed by Hashmarks. These
helpers operate on explicit literal argv/path inputs and grant no effects.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from oh_goon.core.path_layouts import SANDBOX_PATHS
from oh_goon.core.source_tree_policy import (
    canonical_repository_relative_path,
    is_package_manager_materialization_path,
)

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
SHELLS = frozenset({"sh", "bash", "dash"})


def materialized_executable_identity(
    token: str,
    *,
    working_directory: str = ".",
) -> str | None:
    """Return the canonical repository identity for package-manager executable supply."""

    value = token
    if "\\" in value:
        return None
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


def project_path(root: Path, token: str, *, working_directory: str = ".") -> Path | None:
    value = token
    if not value or "$" in value or "\\" in value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return _contained_absolute(root, candidate)
    if not value.startswith(LOCAL_PREFIXES):
        return None
    try:
        relative = canonical_repository_relative_path(value, working_directory=working_directory)
    except ValueError:
        return None
    return _contained_absolute(root, root / relative)


def _contained_absolute(root: Path, candidate: Path) -> Path | None:
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    return resolved


def external_tool(root: Path, token: str, *, working_directory: str = ".") -> str | None:
    value = token
    if (
        _ignored_external_token(value)
        or project_path(root, value, working_directory=working_directory) is not None
    ):
        return None
    if value.startswith("/"):
        workspace = str(SANDBOX_PATHS.workspace)
        return (
            None
            if value == workspace or value.startswith(f"{workspace}/")
            else Path(value).name
            if SIMPLE_TOOL.fullmatch(Path(value).name)
            else None
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
