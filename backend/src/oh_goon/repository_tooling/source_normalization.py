from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from oh_goon.core.source_tree_policy import LIVE_CHECKOUT_PROTECTED_PATHS
from oh_goon.repository_tooling.source_layout import (
    TRANSIENT_DIRECTORY_NAMES,
    TRANSIENT_FILE_NAMES,
    TRANSIENT_RELATIVE_PATHS,
    TRANSIENT_SUFFIXES,
    is_transient_source_path,
)

ROOT = Path.cwd().resolve()

EXACT_DIRS = tuple(
    sorted(
        path.as_posix()
        for path in TRANSIENT_RELATIVE_PATHS
        if not any(path == protected or protected in path.parents for protected in LIVE_CHECKOUT_PROTECTED_PATHS)
    )
)
EXACT_FILES = tuple(sorted(TRANSIENT_FILE_NAMES))
TRANSIENT_DIR_NAMES = set(TRANSIENT_DIRECTORY_NAMES)


def _contained(path: Path, *, root: Path) -> Path:
    resolved_parent = path.parent.resolve()
    root = root.resolve()
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"cleanup target escaped source root: {path}") from exc
    return path


def _make_tree_removable(path: Path) -> None:
    """Restore owner write authority only to directory nodes we are about to delete."""

    for current, directories, _files in os.walk(path, topdown=True, followlinks=False):
        current_path = Path(current)
        if not current_path.is_symlink():
            current_path.chmod(stat.S_IMODE(current_path.stat().st_mode) | stat.S_IWUSR)
        # Thaw children before os.walk descends. Nested symlinks are unlinkable leaves;
        # changing their targets would escape cleanup ownership.
        for name in directories:
            child = current_path / name
            if child.is_symlink():
                continue
            child.chmod(stat.S_IMODE(child.stat().st_mode) | stat.S_IWUSR)


def _remove_path(path: Path, *, root: Path) -> bool:
    path = _contained(path, root=root)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(mode):
        raise RuntimeError(f"refusing symlink cleanup target: {path.relative_to(root)}")
    if stat.S_ISDIR(mode):
        _make_tree_removable(path)
        shutil.rmtree(path)
    elif stat.S_ISREG(mode):
        path.unlink()
    else:
        raise RuntimeError(f"refusing special cleanup target: {path.relative_to(root)}")
    return True


def clean(*, root: Path | None = None) -> tuple[str, ...]:
    root = (root or ROOT).resolve()
    removed: list[str] = []
    for rel in EXACT_DIRS:
        path = root / rel
        if _remove_path(path, root=root):
            removed.append(rel)
    for rel in EXACT_FILES:
        path = root / rel
        if _remove_path(path, root=root):
            removed.append(rel)

    candidates = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for path in candidates:
        relative_path = path.relative_to(root)
        if any(
            relative_path == protected or protected in relative_path.parents
            for protected in LIVE_CHECKOUT_PROTECTED_PATHS
        ):
            continue
        rel = relative_path.as_posix()
        if not path.exists() and not path.is_symlink():
            continue
        if is_transient_source_path(relative_path):
            if _remove_path(path, root=root):
                removed.append(rel)

    return tuple(sorted(set(removed)))


def main() -> int:
    if not (ROOT / "backend" / "pyproject.toml").is_file():
        raise SystemExit("source normalization must run from an Oh-Goon checkout root")
    removed = clean()
    print(f"Source normalization removed {len(removed)} transient paths")
    for value in removed:
        print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
