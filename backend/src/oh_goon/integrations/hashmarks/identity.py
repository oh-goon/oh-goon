from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.hashmarks.runtime_contract import loaded_hashmarks_version

Cancel = Callable[[], bool] | None


@dataclass(frozen=True, slots=True)
class HashmarksTreeIdentity:
    sha256: str
    byte_count: int
    entry_count: int


@dataclass(frozen=True, slots=True)
class HashmarksIdentityResult:
    identity: HashmarksTreeIdentity
    children: dict[str, HashmarksTreeIdentity]


def identify_path(
    path: Path,
    *,
    cancel_requested: Cancel = None,
    exclude_paths: Iterable[str | Path] = (),
) -> HashmarksTreeIdentity:
    result = identify_tree_and_children(
        path,
        (),
        cancel_requested=cancel_requested,
        exclude_paths=exclude_paths,
    )
    return result.identity


def identify_tree_and_children(
    path: Path,
    child_names: tuple[str, ...],
    *,
    cancel_requested: Cancel = None,
    exclude_paths: Iterable[str | Path] = (),
    progress: Callable[[int, int], None] | None = None,
) -> HashmarksIdentityResult:
    """Use only Hashmarks' public identity API for canonical filesystem identity.

    Oh-Goon owns filesystem admission, exclusions, cancellation, and inventory evidence.
    Hashmarks owns content hashing and Merkle identity.  Directory inputs are expressed
    as explicit public ``InputManifest`` members so Oh-Goon can apply its own exclusion
    policy without reaching into Hashmarks implementation classes.
    """

    resolved = path.resolve()
    _check_cancel(cancel_requested, resolved)
    if resolved.is_symlink():
        raise FleetPlanInvalid(f"Cannot identify symlink snapshot root: {resolved}")
    if not resolved.is_file() and not resolved.is_dir():
        raise FleetPlanInvalid(f"Cannot identify non-file snapshot path: {resolved}")
    if len(set(child_names)) != len(child_names):
        raise FleetPlanInvalid("Snapshot direct-child names must be unique")

    for name in child_names:
        if not name or "/" in name or name in {".", ".."}:
            raise FleetPlanInvalid(f"Invalid snapshot direct-child name: {name!r}")
        child = resolved / name
        if child.is_symlink() or not child.is_dir():
            raise FleetPlanInvalid(f"Snapshot direct child is not a directory: {child}")

    try:
        identity = _identify_one(
            resolved,
            cancel_requested=cancel_requested,
            exclude_paths=exclude_paths,
            progress=progress,
            verify=True,
        )
        children = {
            name: _identify_one(
                resolved / name,
                cancel_requested=cancel_requested,
                exclude_paths=(),
                progress=None,
                verify=True,
            )
            for name in child_names
        }
        return HashmarksIdentityResult(identity=identity, children=children)
    except ImportError as exc:
        raise FleetPlanInvalid(
            "Hashmarks repository identity dependency is unavailable; install the "
            "project's pinned Hashmarks runtime dependency before Fleet admission"
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise FleetPlanInvalid(
            f"Hashmarks {loaded_hashmarks_version()} identity failed for {resolved}: {exc}"
        ) from exc


def _identify_one(
    path: Path,
    *,
    cancel_requested: Cancel,
    exclude_paths: Iterable[str | Path],
    progress: Callable[[int, int], None] | None,
    verify: bool,
) -> HashmarksTreeIdentity:
    _check_cancel(cancel_requested, path)
    if path.is_file():
        from hashmarks import hash_file

        digest = hash_file(path)
        _check_cancel(cancel_requested, path)
        return HashmarksTreeIdentity(
            sha256=digest.hash,
            byte_count=int(path.stat().st_size),
            entry_count=1,
        )

    exclusions = tuple(exclude_paths)
    byte_count, entry_count = _inventory(
        path,
        cancel_requested=cancel_requested,
        exclude_paths=exclusions,
        progress=progress,
    )
    state_dir = Path(tempfile.mkdtemp(prefix="oh-goon-hashmarks-state-"))
    view: Path | None = None
    try:
        from hashmarks import RepositoryIdentity

        workspace = path
        if exclusions:
            view = _filtered_identity_view(
                path,
                exclusions,
                cancel_requested=cancel_requested,
            )
            workspace = view
        with RepositoryIdentity(
            workspace,
            mode="local",
            state_dir=state_dir,
            local_observer="reconcile",
        ) as identity:
            snapshot = identity.snapshot(
                "*",
                require_matches=False,
                verify=verify,
            )
        _check_cancel(cancel_requested, path)
        return HashmarksTreeIdentity(
            sha256=snapshot.hash,
            byte_count=byte_count,
            entry_count=entry_count,
        )
    finally:
        if view is not None:
            shutil.rmtree(view, ignore_errors=True)
        shutil.rmtree(state_dir, ignore_errors=True)


def _filtered_identity_view(
    root: Path,
    exclude_paths: Iterable[str | Path],
    *,
    cancel_requested: Cancel,
) -> Path:
    excluded = {_normalize_exclusion(root, item) for item in exclude_paths}
    try:
        view = Path(tempfile.mkdtemp(prefix=".oh-goon-hashmarks-view-", dir=root.parent))
    except OSError:
        view = Path(tempfile.mkdtemp(prefix="oh-goon-hashmarks-view-"))

    try:
        _populate_filtered_view(
            root,
            view,
            Path(),
            excluded=excluded,
            cancel_requested=cancel_requested,
            identity_root=root,
        )
        return view
    except BaseException:
        shutil.rmtree(view, ignore_errors=True)
        raise


def _populate_filtered_view(
    source: Path,
    destination: Path,
    relative_directory: Path,
    *,
    excluded: set[str],
    cancel_requested: Cancel,
    identity_root: Path,
) -> None:
    _check_cancel(cancel_requested, identity_root)
    with os.scandir(source) as scan:
        entries = sorted(scan, key=lambda entry: entry.name)
    for entry in entries:
        relative = relative_directory / entry.name
        if not _excluded(relative, excluded):
            _copy_filtered_view_entry(
                entry,
                destination / entry.name,
                relative,
                excluded=excluded,
                cancel_requested=cancel_requested,
                identity_root=identity_root,
            )


def _copy_filtered_view_entry(
    entry: os.DirEntry[str],
    destination: Path,
    relative: Path,
    *,
    excluded: set[str],
    cancel_requested: Cancel,
    identity_root: Path,
) -> None:
    source = Path(entry.path)
    if entry.is_symlink():
        destination.symlink_to(os.readlink(source))
        return
    if entry.is_file(follow_symlinks=False):
        try:
            os.link(source, destination, follow_symlinks=False)
        except OSError:
            shutil.copy2(source, destination, follow_symlinks=False)
        return
    if entry.is_dir(follow_symlinks=False):
        destination.mkdir()
        _populate_filtered_view(
            source,
            destination,
            relative,
            excluded=excluded,
            cancel_requested=cancel_requested,
            identity_root=identity_root,
        )
        return
    raise FleetPlanInvalid(
        f"Snapshot contains unsupported filesystem entry: {relative.as_posix()}"
    )


def _inventory(
    path: Path,
    *,
    cancel_requested: Cancel,
    exclude_paths: Iterable[str | Path],
    progress: Callable[[int, int], None] | None,
) -> tuple[int, int]:
    if path.is_file():
        _check_cancel(cancel_requested, path)
        return int(path.stat().st_size), 1

    root = path.resolve()
    excluded = {_normalize_exclusion(root, item) for item in exclude_paths}
    byte_count = 0
    entry_count = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        _check_cancel(cancel_requested, root)
        current_path = Path(current)
        relative_current = current_path.relative_to(root)
        directories[:] = [
            name
            for name in sorted(directories)
            if not _excluded(relative_current / name, excluded)
        ]
        for name in (*directories, *sorted(files)):
            relative = relative_current / name
            if _excluded(relative, excluded):
                continue
            entry = current_path / name
            entry_count += 1
            if not entry.is_symlink():
                info = entry.stat()
                if stat.S_ISREG(info.st_mode):
                    byte_count += int(info.st_size)
                elif not stat.S_ISDIR(info.st_mode):
                    raise FleetPlanInvalid(
                        f"Snapshot contains unsupported filesystem entry: {relative.as_posix()}"
                    )
            if progress is not None:
                progress(entry_count, byte_count)
    return byte_count, entry_count

def _normalize_exclusion(root: Path, item: str | Path) -> str:
    candidate = Path(item)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            return ""
    return candidate.as_posix().strip("/")


def _excluded(relative: Path, excluded: set[str]) -> bool:
    rel = relative.as_posix()
    return any(value and (rel == value or rel.startswith(value + "/")) for value in excluded)


def _check_cancel(cancel_requested: Cancel, path: Path) -> None:
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError(f"Cancelled while identifying {path}")
