from __future__ import annotations

import io
import os
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from typing import BinaryIO
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import (
    RepositoryPathSpace,
    canonical_path,
    repository_relative_parts,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations._copy_transport import copy_regular_file

Cancel = Callable[[], bool] | None


@dataclass(frozen=True, slots=True)
class SnapshotIdentity:
    """Deterministic identity for one frozen file/directory tree."""

    sha256: str
    byte_count: int
    entry_count: int


@dataclass(frozen=True, slots=True)
class SnapshotBinding:
    """One declared host path frozen into Fleet-owned execution storage."""

    declared_path: Path
    snapshot_path: Path
    identity: SnapshotIdentity
    sandbox_path: PurePosixPath | None = None


def replace_tree_from_tar_gz(
    data: bytes,
    destination: Path,
    *,
    label: str,
) -> SnapshotIdentity:
    """Validate one in-memory archive before publishing its prepared tree."""

    with io.BytesIO(data) as stream:
        return replace_tree_from_tar_gz_stream(stream, destination, label=label)


def replace_tree_from_tar_gz_stream(
    stream: BinaryIO,
    destination: Path,
    *,
    label: str,
) -> SnapshotIdentity:
    """Validate one archive stream completely before publishing its prepared tree.

    Archive names are treated as untrusted repository-relative syntax. The extracted
    staging tree must have no escaping symlink, nested mount, external hard link, or
    special filesystem entry before publication begins. Publication is failure-visible
    and restores an existing destination after an ordinary final-rename failure; this
    portable two-directory sequence deliberately does not claim crash atomicity.
    """

    destination = canonical_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".fleet-restore-", dir=destination.parent))
    try:
        _extract_validated_archive(stream, staging, label=label)
        space = RepositoryPathSpace.from_root(staging)
        space.require_owned_tree()
        identity = snapshot_identity(staging)
        _publish_prepared_tree(staging, destination, label=label)
        return identity
    except FleetPlanInvalid:
        raise
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise FleetPlanInvalid(f"Cannot restore {label}: {exc}") from exc
    finally:
        if staging.exists():
            _remove_path(staging)


def _extract_validated_archive(stream: BinaryIO, staging: Path, *, label: str) -> None:
    seen: set[PurePosixPath] = set()
    with tarfile.open(fileobj=stream, mode="r:gz") as archive:
        for member in archive.getmembers():
            relative = repository_relative_parts(member.name, label=f"{label} archive member")
            if relative in seen:
                raise FleetPlanInvalid(f"{label} archive contains duplicate path: {relative}")
            seen.add(relative)
        archive.extractall(staging, filter="data")


def snapshot_file(
    source: Path,
    destination: Path,
    *,
    label: str,
    cancel_requested: Cancel = None,
) -> SnapshotIdentity:
    """Freeze one regular file only after proving a stable source/copy identity."""

    source = canonical_path(source)
    if not source.is_file() or source.is_symlink():
        raise FleetPlanInvalid(f"{label} is not a regular file: {source}")
    for _attempt in range(3):
        _check_cancel(cancel_requested, label)
        before = snapshot_identity(source, cancel_requested=cancel_requested)
        _remove_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            _copy_file(source, destination, cancel_requested=cancel_requested, label=label)
        except BaseException:
            _remove_path(destination)
            raise
        after = snapshot_identity(source, cancel_requested=cancel_requested)
        copied = snapshot_identity(destination, cancel_requested=cancel_requested)
        if before == after == copied:
            return copied
    _remove_path(destination)
    raise FleetPlanInvalid(f"{label} changed while Fleet was creating its execution snapshot")


def snapshot_directory(
    source: Path,
    destination: Path,
    *,
    label: str,
    cancel_requested: Cancel = None,
) -> SnapshotIdentity:
    """Freeze one directory only after proving one stable source/copy identity."""

    source = source.resolve()
    if not source.is_dir():
        raise FleetPlanInvalid(f"{label} is not a directory: {source}")
    _validate_contained_symlinks(source, label=label, cancel_requested=cancel_requested)
    for _attempt in range(3):
        _check_cancel(cancel_requested, label)
        before = snapshot_identity(source, cancel_requested=cancel_requested)
        _remove_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            _copy_tree(source, destination, cancel_requested=cancel_requested, label=label)
            _validate_contained_symlinks(
                destination,
                label=f"copied {label}",
                cancel_requested=cancel_requested,
            )
        except BaseException:
            _remove_path(destination)
            raise
        after = snapshot_identity(source, cancel_requested=cancel_requested)
        copied = snapshot_identity(destination, cancel_requested=cancel_requested)
        if before == after == copied:
            return copied
    _remove_path(destination)
    raise FleetPlanInvalid(f"{label} changed while Fleet was creating its execution snapshot")


def copy_frozen_directory(
    source: Path,
    destination: Path,
    *,
    label: str,
    cancel_requested: Cancel = None,
) -> SnapshotIdentity:
    """Copy one already-frozen Fleet-owned tree and identify only the copied bytes.

    The source was made immutable and bound to an identity when Fleet captured or
    published it.  Consumers therefore do not re-hash that private source before
    and after every attempt.  They copy once, identify the exact attempt bytes,
    and compare that identity with the previously bound identity before execution.
    """

    source = source.resolve()
    if not source.is_dir():
        raise FleetPlanInvalid(f"{label} is not a directory: {source}")
    _check_cancel(cancel_requested, label)
    _remove_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        _copy_tree(source, destination, cancel_requested=cancel_requested, label=label)
        return snapshot_identity(destination, cancel_requested=cancel_requested)
    except BaseException:
        _remove_path(destination)
        raise


def snapshot_host_path(
    source: Path,
    destination: Path,
    *,
    label: str,
    cancel_requested: Cancel = None,
) -> SnapshotBinding:
    """Freeze one explicitly declared read-only host capability into Fleet storage."""

    source = canonical_path(source)
    if not source.exists():
        raise FleetPlanInvalid(f"{label} is unavailable: {source}")
    if source.is_dir():
        identity = snapshot_directory(
            source,
            destination,
            label=label,
            cancel_requested=cancel_requested,
        )
    elif source.is_file():
        before = snapshot_identity(source, cancel_requested=cancel_requested)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _remove_path(destination)
        try:
            _copy_file(source, destination, cancel_requested=cancel_requested, label=label)
        except BaseException:
            _remove_path(destination)
            raise
        after = snapshot_identity(source, cancel_requested=cancel_requested)
        copied = snapshot_identity(destination, cancel_requested=cancel_requested)
        if before != after or before != copied:
            destination.unlink(missing_ok=True)
            raise FleetPlanInvalid(
                f"{label} changed while Fleet was creating its execution snapshot"
            )
        identity = copied
    else:
        raise FleetPlanInvalid(f"{label} must be a regular file or directory: {source}")
    return SnapshotBinding(
        declared_path=source, snapshot_path=destination.resolve(), identity=identity
    )


def snapshot_identity(path: Path, *, cancel_requested: Cancel = None) -> SnapshotIdentity:
    """Return canonical Hashmarks content identity plus Oh-Goon inventory evidence."""

    from oh_goon.integrations.hashmarks.identity import identify_path

    identity = identify_path(path, cancel_requested=cancel_requested)
    return SnapshotIdentity(identity.sha256, identity.byte_count, identity.entry_count)


def snapshot_identity_with_direct_children(
    path: Path,
    child_names: tuple[str, ...],
    *,
    cancel_requested: Cancel = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[SnapshotIdentity, dict[str, SnapshotIdentity]]:
    """Strongly verify a Hashmarks Merkle tree and reuse cached direct-child nodes."""

    from oh_goon.integrations.hashmarks.identity import identify_tree_and_children

    result = identify_tree_and_children(
        path,
        child_names,
        cancel_requested=cancel_requested,
        progress=progress,
    )
    return (
        SnapshotIdentity(
            result.identity.sha256,
            result.identity.byte_count,
            result.identity.entry_count,
        ),
        {
            name: SnapshotIdentity(child.sha256, child.byte_count, child.entry_count)
            for name, child in result.children.items()
        },
    )

def verify_snapshot(
    path: Path,
    expected: SnapshotIdentity,
    *,
    label: str,
    cancel_requested: Cancel = None,
) -> None:
    observed = snapshot_identity(path, cancel_requested=cancel_requested)
    if observed != expected:
        raise FleetPlanInvalid(f"{label} changed after Fleet bound its execution identity")


def _copy_tree(source: Path, destination: Path, *, cancel_requested: Cancel, label: str) -> None:
    _check_cancel(cancel_requested, label)
    # The source may be an intentionally read-only immutable tree.  A staging
    # directory must stay writable while children are materialized; copying the
    # source directory mode before its children exist can make the stage itself
    # unwritable (for example 0555) and fail on the first child mkdir.  Preserve
    # the exact source mode only after the derived copy is complete.
    destination.mkdir(mode=0o700)
    with os.scandir(source) as scan:
        entries = sorted(scan, key=lambda entry: entry.name)
    for entry in entries:
        _check_cancel(cancel_requested, label)
        source_entry = Path(entry.path)
        target = destination / entry.name
        if entry.is_symlink():
            target.symlink_to(os.readlink(source_entry))
        elif entry.is_dir(follow_symlinks=False):
            _copy_tree(source_entry, target, cancel_requested=cancel_requested, label=label)
        elif entry.is_file(follow_symlinks=False):
            _copy_file(source_entry, target, cancel_requested=cancel_requested, label=label)
        else:
            raise FleetPlanInvalid(f"{label} contains unsupported filesystem entry: {source_entry}")
    _copy_mode(source, destination)


def _copy_file(source: Path, destination: Path, *, cancel_requested: Cancel, label: str) -> None:
    copy_regular_file(
        source,
        destination,
        check_cancel=lambda: _check_cancel(cancel_requested, label),
        label=f"snapshot file for {label}",
    )


def _copy_mode(source: Path, destination: Path) -> None:
    destination.chmod(source.stat(follow_symlinks=False).st_mode & 0o7777)


def _publish_prepared_tree(staging: Path, destination: Path, *, label: str) -> None:
    """Publish a validated tree and restore the prior tree on ordinary rename failure."""

    backup: Path | None = None
    if destination.exists() or destination.is_symlink():
        backup = _unused_sibling_path(destination, prefix=".fleet-restore-backup-")
        _replace_path(destination, backup)
    try:
        _replace_path(staging, destination)
    except OSError as publish_error:
        if backup is None:
            raise FleetPlanInvalid(f"Cannot publish {label}: {publish_error}") from publish_error
        try:
            _replace_path(backup, destination)
        except OSError as restore_error:
            raise FleetPlanInvalid(
                f"Cannot publish {label}: {publish_error}; previous destination remains at "
                f"{backup} because restoration also failed: {restore_error}"
            ) from publish_error
        raise FleetPlanInvalid(
            f"Cannot publish {label}: {publish_error}; previous destination was restored"
        ) from publish_error
    else:
        if backup is not None and (backup.exists() or backup.is_symlink()):
            _remove_path(backup)


def _unused_sibling_path(destination: Path, *, prefix: str) -> Path:
    reserved = Path(tempfile.mkdtemp(prefix=prefix, dir=destination.parent))
    reserved.rmdir()
    return reserved


def _replace_path(source: Path, destination: Path) -> None:
    source.replace(destination)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _validate_contained_symlinks(root: Path, *, label: str, cancel_requested: Cancel) -> None:
    root = root.resolve()
    for current, directories, files in os.walk(root, followlinks=False):
        _check_cancel(cancel_requested, label)
        current_path = Path(current)
        for name in (*directories, *files):
            path = current_path / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=False)
            except OSError as exc:
                raise FleetPlanInvalid(f"Cannot resolve symlink in {label}: {path}: {exc}") from exc
            if not target.is_relative_to(root):
                relative = path.relative_to(root)
                raise FleetPlanInvalid(f"{label} contains escaping symlink: {relative}")


def _check_cancel(cancel_requested: Cancel, label: str) -> None:
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError(f"Fleet execution cancelled while snapshotting {label}")
