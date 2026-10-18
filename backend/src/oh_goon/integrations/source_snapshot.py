from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.core.source_tree_policy import is_transient_source_path

Cancel = Callable[[], bool] | None
_COPY_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceSnapshotBinding:
    """Exact clean repository source prepared for a read-only execution workspace."""

    snapshot_path: Path
    identity: SnapshotIdentity


def source_snapshot_identity(
    source: Path, *, cancel_requested: Cancel = None
) -> SnapshotIdentity:
    """Identify certifiable source with Hashmarks after Oh-Goon policy exclusions."""

    from oh_goon.integrations.hashmarks.identity import identify_path

    root = source.resolve()
    if not root.is_dir():
        raise FleetPlanInvalid(f"Repository source snapshot is not a directory: {root}")
    exclusions = _transient_source_exclusions(root, cancel_requested=cancel_requested)
    identity = identify_path(
        root,
        cancel_requested=cancel_requested,
        exclude_paths=exclusions,
    )
    return SnapshotIdentity(identity.sha256, identity.byte_count, identity.entry_count)


def _transient_source_exclusions(root: Path, *, cancel_requested: Cancel) -> tuple[str, ...]:
    excluded: list[str] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        _check_cancel(cancel_requested, root)
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        kept: list[str] = []
        for name in sorted(directories):
            relative = relative_current / name
            entry = current_path / name
            if is_transient_source_path(relative):
                excluded.append(relative.as_posix())
            else:
                if entry.is_symlink():
                    _validated_symlink_target(root, entry, relative)
                kept.append(name)
        directories[:] = kept
        for name in sorted(files):
            relative = relative_current / name
            entry = current_path / name
            if is_transient_source_path(relative):
                excluded.append(relative.as_posix())
            elif entry.is_symlink():
                _validated_symlink_target(root, entry, relative)
    return tuple(excluded)


def verify_clean_source_snapshot(
    binding: SourceSnapshotBinding,
    *,
    label: str = "read-only repository source",
    cancel_requested: Cancel = None,
) -> None:
    """Re-prove clean source identity immediately before read-only execution.

    ``source_snapshot_identity`` intentionally excludes operational/transient paths. A
    managed READ_SNAPSHOT must additionally prove those paths are physically absent,
    otherwise a host-side ``.git``/cache injection could be invisible to content identity
    while still becoming visible inside the sandbox.
    """

    root = binding.snapshot_path.resolve()
    if not root.is_dir():
        raise FleetPlanInvalid(f"{label} is not a directory: {root}")
    excluded = _transient_source_exclusions(root, cancel_requested=cancel_requested)
    if excluded:
        raise FleetPlanInvalid(
            f"{label} contains transient/operational paths: {', '.join(excluded[:5])}"
        )
    observed = source_snapshot_identity(root, cancel_requested=cancel_requested)
    if observed != binding.identity:
        raise FleetPlanInvalid(f"{label} identity changed after materialization")


def snapshot_source_directory(
    source: Path,
    destination: Path,
    *,
    label: str,
    cancel_requested: Cancel = None,
) -> SnapshotIdentity:
    """Freeze source-only repository bytes and prove the filtered source stayed stable."""

    root = source.resolve()
    if not root.is_dir():
        raise FleetPlanInvalid(f"{label} is not a directory: {root}")
    for _attempt in range(3):
        _check_cancel(cancel_requested, root)
        before = source_snapshot_identity(root, cancel_requested=cancel_requested)
        _remove(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            _copy_source_tree(root, root, destination, cancel_requested, label)
        except BaseException:
            _remove(destination)
            raise
        after = source_snapshot_identity(root, cancel_requested=cancel_requested)
        copied = source_snapshot_identity(destination, cancel_requested=cancel_requested)
        if before == after == copied:
            return copied
    _remove(destination)
    raise FleetPlanInvalid(f"{label} changed while Fleet was creating its source snapshot")


def _copy_source_tree(
    root: Path,
    source: Path,
    destination: Path,
    cancel_requested: Cancel,
    label: str,
) -> None:
    _check_cancel(cancel_requested, root)
    destination.mkdir()
    destination.chmod(stat.S_IMODE(source.stat().st_mode))
    relative_current = source.relative_to(root)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        relative = relative_current / entry.name
        if is_transient_source_path(relative):
            continue
        target = destination / entry.name
        if entry.is_symlink():
            symlink_target = _validated_symlink_target(root, entry, relative)
            target.symlink_to(symlink_target)
        elif entry.is_dir():
            _copy_source_tree(root, entry, target, cancel_requested, label)
        elif entry.is_file():
            _copy_file(entry, target, cancel_requested, label)
        else:
            raise FleetPlanInvalid(f"{label} contains unsupported filesystem entry: {relative}")



def _validated_symlink_target(root: Path, entry: Path, relative: Path) -> str:
    raw = os.readlink(entry)
    target = Path(raw)
    if target.is_absolute():
        raise FleetPlanInvalid(
            f"Repository source contains escaping symlink: {relative} -> {raw}"
        )
    resolved = (entry.parent / target).resolve(strict=False)
    try:
        target_relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise FleetPlanInvalid(
            f"Repository source contains escaping symlink: {relative} -> {raw}"
        ) from exc
    if is_transient_source_path(target_relative):
        raise FleetPlanInvalid(
            f"Repository source symlink targets generated/operational state: {relative} -> {raw}"
        )
    return raw

def _copy_file(source: Path, destination: Path, cancel_requested: Cancel, label: str) -> None:
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode):
            raise FleetPlanInvalid(f"{label} source file changed type while copying: {source}")
        destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            while True:
                _check_cancel(cancel_requested, source)
                chunk = os.read(source_fd, _COPY_CHUNK_BYTES)
                if not chunk:
                    break
                os.write(destination_fd, chunk)
            os.fchmod(destination_fd, stat.S_IMODE(info.st_mode))
        finally:
            os.close(destination_fd)
    finally:
        os.close(source_fd)


def _check_cancel(cancel_requested: Cancel, label: Path) -> None:
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError(f"Cancelled while snapshotting {label}")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        import shutil

        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)
