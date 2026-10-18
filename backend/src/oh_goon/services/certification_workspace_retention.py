from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION
from oh_goon.runtime.run_workspace_reconciliation import (
    reconcile_idle_run_workspaces,
    run_workspace_names,
)

_CERTIFICATION_REPOSITORY_STATE_NAME = re.compile(r"^repo-[0-9a-f]{20}$")


@dataclass(frozen=True, slots=True)
class CertificationRunWorkspaceReconciliation:
    removed: tuple[str, ...]
    refused: tuple[str, ...]
    cleanup_incomplete: tuple[str, ...] = ()


def certification_state_root(*, source_root: Path, state_home: Path) -> Path:
    """Return the sole current certification control-state namespace."""

    root = source_root.resolve()
    return (
        state_home.resolve()
        / "oh-goon"
        / "certification"
        / repository_id(root)
        / f"schema-{DATABASE_SCHEMA_VERSION}"
        / "state"
    )


def certification_state_roots(*, state_home: Path) -> tuple[Path, ...]:
    """Discover only current-schema repository certification state without links."""

    root = state_home.resolve() / "oh-goon" / "certification"
    if not root.is_dir() or root.is_symlink():
        return ()
    try:
        repositories = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError:
        return ()
    states: list[Path] = []
    for repository in repositories:
        if not _is_repository_state_directory(repository):
            continue
        state = repository / f"schema-{DATABASE_SCHEMA_VERSION}" / "state"
        if state.is_symlink() or not state.is_dir():
            continue
        states.append(state)
    return tuple(states)


def certification_run_workspace_names(state_root: Path) -> tuple[str, ...]:
    return run_workspace_names(state_root / "oh-goon.db")


def reconcile_certification_run_workspaces_under_lease(
    state_root: Path,
) -> CertificationRunWorkspaceReconciliation:
    """Reconcile one current-schema namespace after its workflow lease is owned."""

    result = reconcile_idle_run_workspaces(state_root / "oh-goon.db")
    return CertificationRunWorkspaceReconciliation(
        removed=result.removed,
        refused=result.refused,
        cleanup_incomplete=result.cleanup_incomplete,
    )


def certification_state_label(state_root: Path) -> str:
    return state_root.parent.parent.name


def _is_repository_state_directory(path: Path) -> bool:
    return (
        _CERTIFICATION_REPOSITORY_STATE_NAME.fullmatch(path.name) is not None
        and not path.is_symlink()
        and path.is_dir()
    )
