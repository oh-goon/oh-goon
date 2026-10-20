from __future__ import annotations

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

    Oh-Goon owns filesystem admission, exclusions, and cancellation. Hashmarks owns
    native contents identity and physical materialization counts. Each requested
    child retains its own input-root observation; a directory member revision is
    a different native identity and cannot replace that observation.
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

    exclusions = tuple(
        value for item in exclude_paths if (value := _normalize_exclusion(path, item))
    )
    with tempfile.TemporaryDirectory(prefix="oh-goon-hashmarks-state-") as directory:
        from hashmarks import RepositoryIdentity

        with RepositoryIdentity(
            path,
            mode="local",
            state_dir=Path(directory),
            local_observer="reconcile",
        ) as identity:
            observation = identity.observe_directory(
                exclude_paths=exclusions,
                verify=verify,
                checkpoint=lambda: _check_cancel(cancel_requested, path),
                progress=(lambda counts: progress(counts.entry_count, counts.regular_file_bytes))
                if progress is not None
                else None,
            )
        _check_cancel(cancel_requested, path)
        return HashmarksTreeIdentity(
            sha256=observation.inputs.snapshot.hash,
            byte_count=observation.materialization_counts.regular_file_bytes,
            entry_count=observation.materialization_counts.entry_count,
        )


def _normalize_exclusion(root: Path, item: str | Path) -> str:
    candidate = Path(item)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            return ""
    return candidate.as_posix().strip("/")


def _check_cancel(cancel_requested: Cancel, path: Path) -> None:
    if cancel_requested is not None and cancel_requested():
        raise OperationCancelledError(f"Cancelled while identifying {path}")
