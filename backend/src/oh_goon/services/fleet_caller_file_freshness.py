"""One read-only source freshness owner shared by CLI and Fleet admission/worker.

File identity is a necessary stale-work guard, not a trust root or certificate.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.caller_input_file import CallerInputFileIdentity

_MAX_BYTES = 65536


def locate_caller_file(path: Path, *, root: Path) -> tuple[Path, str]:
    root = root.resolve(strict=True)
    if ".." in path.parts:
        raise FleetPlanInvalid("--inputs-file cannot contain parent-directory traversal")
    selected = path if path.is_absolute() else root / path
    normalized = Path(os.path.normpath(selected))
    relative = normalized.relative_to(root) if normalized.is_relative_to(root) else None
    if (
        relative is None
        or not relative.parts
        or any(part in {"..", "."} for part in relative.parts)
    ):
        raise FleetPlanInvalid(
            "--inputs-file must be a file contained in the caller working directory"
        )
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise FleetPlanInvalid("--inputs-file path is not a readable regular file") from exc
        if stat.S_ISLNK(info.st_mode):
            raise FleetPlanInvalid("--inputs-file symlink components are not allowed")
    if not stat.S_ISREG(info.st_mode):
        raise FleetPlanInvalid("--inputs-file must refer to a regular file")
    return current, relative.as_posix()


def read_caller_file_bytes(path: Path, *, expected: os.stat_result) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (expected.st_dev, expected.st_ino):
                raise FleetPlanInvalid(
                    "--inputs-file was replaced before its bytes could be captured"
                )
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_BYTES:
                raise FleetPlanInvalid(
                    "--inputs-file must be a regular file of at most 65536 bytes"
                )
            contents = stream.read(_MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(contents) > _MAX_BYTES or (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise FleetPlanInvalid("--inputs-file is oversized or changed during snapshot")
            return contents
    except OSError as exc:
        raise FleetPlanInvalid("--inputs-file cannot be read as an ordinary regular file") from exc


def require_caller_file_fresh(identity: CallerInputFileIdentity) -> None:
    """Re-prove transported identity, never reparse YAML or select replacement values."""
    selected, relative = locate_caller_file(Path(identity.relative_path), root=identity.caller_root)
    if relative != identity.relative_path:
        raise FleetPlanInvalid("--inputs-file source path no longer matches its frozen identity")
    initial = selected.stat(follow_symlinks=False)
    if (initial.st_dev, initial.st_ino) != (identity.source_device, identity.source_inode):
        raise FleetPlanInvalid("--inputs-file source was replaced after snapshot")
    observed = read_caller_file_bytes(selected, expected=initial)
    selected_after, relative_after = locate_caller_file(
        Path(identity.relative_path), root=identity.caller_root
    )
    final = selected_after.stat(follow_symlinks=False)
    if (
        selected_after != selected
        or relative_after != relative
        or (final.st_dev, final.st_ino) != (identity.source_device, identity.source_inode)
        or (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns)
        or hashlib.sha256(observed).hexdigest() != identity.sha256
    ):
        raise FleetPlanInvalid("--inputs-file source changed after its frozen snapshot")


def require_target_caller_file_fresh(target: object) -> None:
    """Original Fleet boundaries can call this on any target without a new ingress."""
    from oh_goon.core.models.target import FleetPlanTarget

    if isinstance(target, FleetPlanTarget) and target.caller_input_file is not None:
        require_caller_file_fresh(target.caller_input_file)
