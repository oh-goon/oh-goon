from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from oh_goon.core.source_transport_metadata import is_disposable_source_transport_artifact_name

CANONICAL_BACKEND_LOCK = PurePosixPath("backend/uv.lock")
CANONICAL_CERTIFICATION_FIXTURE_ROOT = PurePosixPath("template-certification/fixtures")
CANONICAL_FRONTEND_LOCK = PurePosixPath("frontend/package-lock.json")
CANONICAL_BACKEND_VENV = PurePosixPath("backend/.venv")
PACKAGE_MANAGER_MATERIALIZATION_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {".venv", "node_modules"}
)
EXECUTION_RUNTIME_MATERIALIZATION_ROOTS: tuple[PurePosixPath, ...] = (
    PurePosixPath(".venv"),
    CANONICAL_BACKEND_VENV,
)
OPERATIONAL_STATE_ROOT = PurePosixPath(".oh-goon-data")
LIVE_CHECKOUT_PROTECTED_PATHS: frozenset[PurePosixPath] = frozenset({PurePosixPath(".git")})

TRANSIENT_RELATIVE_PATHS: frozenset[PurePosixPath] = frozenset(
    {
        PurePosixPath(".git"),
        PurePosixPath(".hashmarks"),
        OPERATIONAL_STATE_ROOT,
        PurePosixPath(".venv"),
        CANONICAL_BACKEND_VENV,
        PurePosixPath("frontend/node_modules"),
        PurePosixPath("frontend/.oh-goon-playwright-browsers"),
        PurePosixPath("frontend/.oh-goon-vite-cache"),
        PurePosixPath(".pytest_cache"),
        PurePosixPath(".mypy_cache"),
        PurePosixPath(".ruff_cache"),
        PurePosixPath("coverage"),
        PurePosixPath("playwright-report"),
        PurePosixPath("playwright-runtime-report"),
        PurePosixPath("test-results"),
        PurePosixPath("frontend/dist"),
        PurePosixPath("generated/release/dist"),
        PurePosixPath("generated/release/static"),
        PurePosixPath("release-artifacts"),
    }
)
TRANSIENT_DIRECTORY_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hashmarks",
        ".oh-goon-playwright-browsers",
        ".oh-goon-vite-cache",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
) | PACKAGE_MANAGER_MATERIALIZATION_DIRECTORY_NAMES
TRANSIENT_FILE_NAMES: frozenset[str] = frozenset({".coverage", "coverage-python.json"})
TRANSIENT_FILE_PREFIXES: tuple[str, ...] = (".coverage.",)
TRANSIENT_SUFFIXES: frozenset[str] = frozenset({".pyc", ".pyo", ".tsbuildinfo"})


def repository_relative_path(path: Path | PurePosixPath | str) -> PurePosixPath:
    value = PurePosixPath(str(path).replace(os.sep, "/"))
    if value.is_absolute() or ".." in value.parts:
        raise ValueError(f"source-tree path must be repository-relative: {path}")
    return value


def canonical_repository_relative_path(
    path: Path | PurePosixPath | str,
    *,
    working_directory: Path | PurePosixPath | str = ".",
) -> PurePosixPath:
    """Lexically bind a literal path to one canonical repository-relative identity."""

    raw_path = PurePosixPath(str(path).replace("\\", "/"))
    raw_working_directory = PurePosixPath(str(working_directory).replace("\\", "/"))
    if raw_path.is_absolute() or raw_working_directory.is_absolute():
        raise ValueError("source-tree path and working directory must be repository-relative")

    parts: list[str] = []
    for part in (*raw_working_directory.parts, *raw_path.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"source-tree path escapes repository: {path}")
            parts.pop()
            continue
        parts.append(part)
    return PurePosixPath(*parts)


def is_package_manager_materialization_path(
    path: Path | PurePosixPath | str,
) -> bool:
    """Whether a canonical source-tree path belongs to package-manager materialization."""

    relative = repository_relative_path(path)
    return any(
        part in PACKAGE_MANAGER_MATERIALIZATION_DIRECTORY_NAMES for part in relative.parts
    )


def is_transient_source_path(path: Path | PurePosixPath | str) -> bool:
    """Whether a repository-relative path is generated/operational and never source identity."""

    relative = repository_relative_path(path)
    for root in TRANSIENT_RELATIVE_PATHS:
        if relative == root or root in relative.parents:
            return True
    if any(part in TRANSIENT_DIRECTORY_NAMES for part in relative.parts):
        return True
    if relative.name in TRANSIENT_FILE_NAMES:
        return True
    if any(relative.name.startswith(prefix) for prefix in TRANSIENT_FILE_PREFIXES):
        return True
    if is_disposable_source_transport_artifact_name(relative.name):
        return True
    return Path(relative.name).suffix in TRANSIENT_SUFFIXES


def _is_certification_fixture_lock(relative: PurePosixPath) -> bool:
    return (
        relative.name == "uv.lock"
        and CANONICAL_CERTIFICATION_FIXTURE_ROOT in relative.parents
        and relative.parent != CANONICAL_CERTIFICATION_FIXTURE_ROOT
    )


def _is_regular_non_symlink(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except OSError:
        return False
    import stat

    return stat.S_ISREG(mode) and not stat.S_ISLNK(mode)


def canonical_lock_layout_issues(root: Path) -> tuple[tuple[Path, str], ...]:
    """Validate source lock placement without conflating isolated fixture authority."""

    root = root.resolve()
    issues: list[tuple[Path, str]] = []
    lock = root / Path(CANONICAL_BACKEND_LOCK)
    if not lock.exists() and not lock.is_symlink():
        issues.append((lock, "committed backend dependency lock is missing"))
    else:
        try:
            mode = lock.lstat().st_mode
        except OSError as exc:
            issues.append((lock, f"cannot inspect committed backend dependency lock: {exc}"))
        else:
            import stat

            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                issues.append((lock, "committed backend dependency lock must be a regular non-symlink file"))

    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        relative_dir = current.relative_to(root)
        retained: list[str] = []
        for name in dirnames:
            relative = PurePosixPath(*relative_dir.parts, name)
            if relative.parts and relative.parts[0] == ".git":
                continue
            if is_transient_source_path(relative):
                continue
            retained.append(name)
        dirnames[:] = retained
        for name in filenames:
            relative = PurePosixPath(*relative_dir.parts, name)
            if relative.parts and relative.parts[0] == ".git":
                continue
            if relative == CANONICAL_BACKEND_LOCK:
                continue
            if is_transient_source_path(relative):
                continue
            if name == "uv.lock":
                candidate = root / Path(relative)
                if _is_certification_fixture_lock(relative):
                    if not _is_regular_non_symlink(candidate):
                        issues.append(
                            (
                                candidate,
                                "committed certification fixture lock must be a regular "
                                "non-symlink file",
                            )
                        )
                        continue
                    pyproject = candidate.parent / "pyproject.toml"
                    if not _is_regular_non_symlink(pyproject):
                        issues.append(
                            (
                                candidate,
                                "certification fixture uv.lock requires a sibling regular "
                                "non-symlink pyproject.toml",
                            )
                        )
                    continue
                issues.append(
                    (
                        candidate,
                        "unexpected uv.lock source path; Oh-Goon dependency authority "
                        f"belongs at {CANONICAL_BACKEND_LOCK} or under "
                        f"{CANONICAL_CERTIFICATION_FIXTURE_ROOT}",
                    )
                )
    return tuple(issues)


def require_canonical_lock_layout(root: Path) -> None:
    issues = canonical_lock_layout_issues(root)
    if not issues:
        return
    rendered = "; ".join(f"{path.relative_to(root.resolve())}: {reason}" for path, reason in issues)
    raise ValueError(f"repository source layout is invalid: {rendered}")
