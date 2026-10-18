"""Native uv owner for one selected locked project (read-only, offline).

This observes uv's authority; it never decides dependency graph semantics or
modifies the lock. It is shared by the installed issuer and tooling adapter.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

Runner = Callable[..., subprocess.CompletedProcess[str]]


def native_uv_locked_project_issue(project: Path, *, runner: Runner = subprocess.run) -> str | None:
    if not isinstance(project, Path) or not project.is_absolute() or project.is_symlink():
        return "canonical native uv project directory is required"
    try:
        result = runner(("uv", "lock", "--project", str(project), "--check", "--offline"),
                        cwd=project, check=False, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"native uv lock --check --offline could not run: {exc}"
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "native uv lock --check --offline failed").strip()
    return f"native uv lock --check --offline failed: {detail}"
