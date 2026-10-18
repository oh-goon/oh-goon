from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution.coordinator import ExecutionCoordinator

RUN_WORKSPACE_PREFIX = "oh-goon-plan-"


@dataclass(frozen=True, slots=True)
class RunWorkspaceReconciliation:
    removed: tuple[str, ...]
    refused: tuple[str, ...]
    cleanup_incomplete: tuple[str, ...] = ()


def run_workspace_names(database_path: Path) -> tuple[str, ...]:
    layout = ApplicationStatePathLayout.from_database(database_path)
    return run_workspace_names_at(layout.run_workspaces)


def run_workspace_names_at(run_workspaces: Path) -> tuple[str, ...]:
    if not run_workspaces.is_dir() or run_workspaces.is_symlink():
        return ()
    try:
        return tuple(sorted(path.name for path in run_workspaces.iterdir()))
    except OSError:
        return ()


def reconcile_owned_run_workspace_tree(run_workspaces: Path) -> RunWorkspaceReconciliation:
    """Remove only workspace children proven to belong to Fleet scratch.

    This function owns no lifecycle fence by itself. Callers must first prove that the
    namespace is not in use (for example with the ExecutionCoordinator or the exact
    certification workflow lease). Unknown child names are retained fail-closed.
    """

    names = run_workspace_names_at(run_workspaces)
    if not names:
        return RunWorkspaceReconciliation(removed=(), refused=())

    removed: list[str] = []
    refused: list[str] = []
    cleanup_incomplete: list[str] = []
    for name in names:
        path = run_workspaces / name
        if not name.startswith(RUN_WORKSPACE_PREFIX):
            refused.append(name)
            continue
        try:
            _remove_owned_workspace(path)
        except OSError:
            cleanup_incomplete.append(name)
            continue
        removed.append(name)
    return RunWorkspaceReconciliation(
        removed=tuple(removed),
        refused=tuple(refused),
        cleanup_incomplete=tuple(cleanup_incomplete),
    )


def reconcile_idle_run_workspaces(database_path: Path) -> RunWorkspaceReconciliation:
    """Remove only Fleet-owned run scratch after durable execution proves idle.

    A run workspace is disposable execution scratch, not certification/control history.  The
    database is the lifecycle authority: without an existing database, or while execution is
    not durably idle, no workspace receives deletion authority.  Unknown child names are
    retained rather than inferred to be ours.
    """

    database_path = database_path.resolve()
    layout = ApplicationStatePathLayout.from_database(database_path)
    names = run_workspace_names(database_path)
    if not names:
        return RunWorkspaceReconciliation(removed=(), refused=())
    if not database_path.is_file():
        return RunWorkspaceReconciliation(removed=(), refused=names)

    database = LocalDatabase(database_path)
    coordinator = ExecutionCoordinator(database)
    with database.transaction(write=True, track_revision=False) as connection:
        coordinator.assert_idle(connection)
        return reconcile_owned_run_workspace_tree(layout.run_workspaces)


def _remove_owned_workspace(path: Path) -> None:
    """Delete owned scratch idempotently when parallel cleanup already won the race."""

    try:
        if path.is_symlink():
            path.unlink()
            return
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        # Another authorized cleanup owner may have removed the same ephemeral scratch
        # after inventory but before deletion. Absence is the desired durable outcome,
        # not CLEANUP_INCOMPLETE. Other OSErrors still surface to the caller.
        return
