from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from oh_goon.repository_tooling.public_dependency_privacy import (
    backend_uv_lock_privacy_issues,
    frontend_package_lock_privacy_issues,
)
from oh_goon.repository_tooling.source_layout import canonical_source_layout_issues
from oh_goon.integrations.native_uv_lock import native_uv_locked_project_issue as _product_native_uv_locked_project_issue

ROOT = Path(__file__).resolve().parents[4]
BACKEND_LOCK = Path("backend/uv.lock")
BACKEND_PYPROJECT = Path("backend/pyproject.toml")
FRONTEND_LOCK = Path("frontend/package-lock.json")
Runner = Callable[..., subprocess.CompletedProcess[str]]


def _contained_in_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _native_check(
    command: tuple[str, ...],
    *,
    cwd: Path,
    runner: Runner,
    label: str,
) -> str | None:
    try:
        result = runner(command, cwd=cwd, check=False, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{label} could not run: {exc}"
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or f"{label} failed").strip()
    return f"{label} failed: {detail}"


def backend_dependency_lock_issues(
    root: Path = ROOT, *, runner: Runner = subprocess.run
) -> tuple[tuple[Path, str], ...]:
    """Validate the committed native uv authority without interpreting its graph."""

    root = root.resolve()
    lock = root / BACKEND_LOCK
    pyproject = root / BACKEND_PYPROJECT
    issues: list[tuple[Path, str]] = []
    for path, label in (
        (pyproject, "backend dependency intent"),
        (lock, "committed backend uv.lock"),
    ):
        if path.is_symlink() or not _contained_in_root(path, root) or not path.is_file():
            issues.append((path, f"{label} must be a contained regular non-symlink file"))
    if issues:
        return tuple(issues)

    # Privacy is an Oh-Goon source-publication boundary. Package/version/graph
    # semantics remain exclusively uv-owned.
    for issue in backend_uv_lock_privacy_issues(root):
        issues.append((lock, issue))
    if issues:
        return tuple(issues)

    native = _native_check(
        ("uv", "lock", "--project", str(root / "backend"), "--check"),
        cwd=root,
        runner=runner,
        label="uv lock --check",
    )
    if native is not None:
        issues.append((lock, native))
    return tuple(issues)


def frontend_dependency_lock_issues(
    root: Path = ROOT, *, runner: Runner = subprocess.run
) -> tuple[tuple[Path, str], ...]:
    npm_lock = root / FRONTEND_LOCK
    if npm_lock.is_symlink() or not _contained_in_root(npm_lock, root):
        return ((npm_lock, "must be a contained regular non-symlink file"),)
    if not npm_lock.is_file():
        return ((npm_lock, "is missing"),)
    privacy = frontend_package_lock_privacy_issues(root)
    if privacy:
        return tuple((npm_lock, issue) for issue in privacy)
    issue = _native_check(
        (
            "npm",
            "ci",
            "--package-lock-only",
            "--ignore-scripts",
            "--dry-run",
            "--offline",
            "--no-audit",
            "--no-fund",
        ),
        cwd=root / "frontend",
        runner=runner,
        label="npm ci lock check",
    )
    return () if issue is None else ((npm_lock, issue),)


def dependency_lock_issues(
    root: Path = ROOT, *, runner: Runner = subprocess.run
) -> tuple[tuple[Path, str], ...]:
    """Validate packageable backend/frontend native lock authorities."""

    layout = tuple(
        (issue.path, issue.reason)
        for issue in canonical_source_layout_issues(root, check_tracked_generated=False)
        if issue.path.name == "uv.lock"
    )
    return (
        *layout,
        *backend_dependency_lock_issues(root, runner=runner),
        *frontend_dependency_lock_issues(root, runner=runner),
    )


def native_uv_locked_project_issue(project: Path, *, runner: Runner = subprocess.run) -> str | None:
    """Compatibility entrypoint: one installed native uv observer, no duplicate decision."""
    return _product_native_uv_locked_project_issue(project, runner=runner)
