from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


def prepare_attempt_private_root(root: Path) -> Path:
    """Create or reuse one real owned private directory, never a symlink or leaf."""
    try:
        mode = root.lstat().st_mode
    except FileNotFoundError:
        root.mkdir(parents=True, mode=0o700)
        mode = root.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise OSError(f"Attempt-private config root must be an owned directory: {root}")
    root.chmod(0o700)
    return root


def remove_attempt_private_root(root: Path) -> None:
    """Delete one owned private config entry and verify the entry itself is absent."""
    if not os.path.lexists(root):
        return
    mode = root.lstat().st_mode
    if stat.S_ISDIR(mode) and not stat.S_ISLNK(mode):
        shutil.rmtree(root)
    else:
        root.unlink()
    if os.path.lexists(root):
        raise OSError(f"Attempt-private config root still exists after cleanup: {root}")
