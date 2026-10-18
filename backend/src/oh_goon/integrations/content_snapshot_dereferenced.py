from __future__ import annotations

import tempfile
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.content_snapshot import (
    Cancel,
    SnapshotIdentity,
    _check_cancel,
    _copy_file,
    _copy_mode,
    _remove_path,
    snapshot_identity,
)


def snapshot_directory_dereferenced(
    source: Path,
    destination: Path,
    *,
    label: str,
    cancel_requested: Cancel = None,
    excluded_entries: tuple[str, ...] = (),
) -> SnapshotIdentity:
    """Freeze a trusted runtime tree while materializing symlink targets as ordinary entries.

    Fleet-owned virtual environments commonly contain interpreter symlinks that point
    outside the environment. Strict execution must not keep those live host edges, so
    this operation copies the target bytes/tree at the logical symlink path. Two
    independently materialized identities must agree; the second proved tree becomes
    the destination directly, avoiding a third full materialization.
    """

    source = source.resolve()
    if not source.is_dir():
        raise FleetPlanInvalid(f"{label} is not a directory: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(3):
        _check_cancel(cancel_requested, label)
        before_root, before = _materialize_dereferenced_tree(
            source,
            destination.parent,
            prefix=".oh-goon-dereferenced-before-",
            cancel_requested=cancel_requested,
            label=label,
            excluded_entries=excluded_entries,
        )
        try:
            before_identity = snapshot_identity(before, cancel_requested=cancel_requested)
            _remove_path(destination)
            after_identity = _prove_and_promote_dereferenced_tree(
                source,
                destination,
                expected_identity=before_identity,
                cancel_requested=cancel_requested,
                label=label,
                excluded_entries=excluded_entries,
            )
            if after_identity is not None:
                return after_identity
        except BaseException:
            _remove_path(destination)
            raise
        finally:
            _remove_path(before_root)
    _remove_path(destination)
    raise FleetPlanInvalid(f"{label} changed while Fleet was creating its execution snapshot")


def _prove_and_promote_dereferenced_tree(
    source: Path,
    destination: Path,
    *,
    expected_identity: SnapshotIdentity,
    cancel_requested: Cancel,
    label: str,
    excluded_entries: tuple[str, ...],
) -> SnapshotIdentity | None:
    after_root, after = _materialize_dereferenced_tree(
        source,
        destination.parent,
        prefix=".oh-goon-dereferenced-after-",
        cancel_requested=cancel_requested,
        label=label,
        excluded_entries=excluded_entries,
    )
    try:
        after_identity = snapshot_identity(after, cancel_requested=cancel_requested)
        if expected_identity != after_identity:
            return None
        after.replace(destination)
        return after_identity
    finally:
        _remove_path(after_root)


def _materialize_dereferenced_tree(
    source: Path,
    parent: Path,
    *,
    prefix: str,
    cancel_requested: Cancel,
    label: str,
    excluded_entries: tuple[str, ...],
) -> tuple[Path, Path]:
    staging_root = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    tree = staging_root / "tree"
    try:
        _copy_tree_dereferenced(
            source,
            tree,
            cancel_requested=cancel_requested,
            label=label,
            active=(),
            excluded_entries=excluded_entries,
        )
        return staging_root, tree
    except BaseException:
        _remove_path(staging_root)
        raise


def _copy_tree_dereferenced(
    source: Path,
    destination: Path,
    *,
    cancel_requested: Cancel,
    label: str,
    active: tuple[Path, ...],
    excluded_entries: tuple[str, ...],
) -> None:
    _check_cancel(cancel_requested, label)
    resolved = source.resolve(strict=True)
    if resolved in active:
        raise FleetPlanInvalid(f"{label} contains a symlink directory cycle")
    destination.mkdir()
    _copy_mode(resolved, destination)
    next_active = (*active, resolved)
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        _check_cancel(cancel_requested, label)
        if entry.name in excluded_entries:
            continue
        target = destination / entry.name
        resolved_entry = entry.resolve(strict=True) if entry.is_symlink() else entry
        if resolved_entry.is_dir():
            _copy_tree_dereferenced(
                resolved_entry,
                target,
                cancel_requested=cancel_requested,
                label=label,
                active=next_active,
                excluded_entries=(),
            )
        elif resolved_entry.is_file():
            _copy_file(
                resolved_entry,
                target,
                cancel_requested=cancel_requested,
                label=label,
            )
        else:
            raise FleetPlanInvalid(f"{label} contains unsupported filesystem entry: {entry}")
