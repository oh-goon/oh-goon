from __future__ import annotations

from oh_goon.services.certification_workspace_retention import (
    certification_state_root,
)

from pathlib import Path

from oh_goon.core.product_identity import repository_id
from oh_goon.runtime._database_policy import DATABASE_SCHEMA_VERSION
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services import certification_bootstrap


def _current_state(tmp_path: Path) -> tuple[Path, str, Path]:
    state_home = tmp_path / "state-home"
    source = tmp_path / "repo"
    source.mkdir()
    repo_id = repository_id(source)
    state = certification_state_root(
        source_root=source,
        state_home=state_home,
    )
    LocalDatabase(state / "oh-goon.db")
    return state_home, repo_id, state


def test_reconciliation_removes_only_idle_owned_current_scratch(tmp_path: Path) -> None:
    state_home, repo_id, state = _current_state(tmp_path)
    owned = state / "run-workspaces" / "oh-goon-plan-stale"
    foreign = state / "run-workspaces" / "foreign"
    owned.mkdir(parents=True)
    foreign.mkdir()

    result = certification_bootstrap.reconcile_certification_run_workspaces(
        state_home=state_home,
    )

    assert result.removed == (f"{repo_id}/oh-goon-plan-stale",)
    assert result.refused == (f"{repo_id}/foreign",)
    assert not owned.exists()
    assert foreign.exists()


def test_reconciliation_refuses_busy_current_namespace(tmp_path: Path) -> None:
    state_home, repo_id, state = _current_state(tmp_path)
    workspace = state / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)

    with certification_bootstrap.certification_state_lease(state):
        result = certification_bootstrap.reconcile_certification_run_workspaces(
            state_home=state_home,
        )

    assert result.removed == ()
    assert result.refused == (f"{repo_id}/oh-goon-plan-stale",)
    assert workspace.exists()


def test_reconciliation_uses_already_owned_current_lease(tmp_path: Path) -> None:
    state_home, repo_id, state = _current_state(tmp_path)
    workspace = state / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)

    with certification_bootstrap.certification_state_lease(state):
        result = certification_bootstrap.reconcile_certification_run_workspaces(
            state_home=state_home,
            owned_state_root=state,
        )

    assert result.removed == (f"{repo_id}/oh-goon-plan-stale",)
    assert result.refused == ()
    assert not workspace.exists()


def test_reconciliation_does_not_interpret_noncurrent_schema_state(
    tmp_path: Path,
) -> None:
    state_home = tmp_path / "state-home"
    source = tmp_path / "repo"
    source.mkdir()
    repo_id = repository_id(source)
    noncurrent = (
        state_home
        / "oh-goon"
        / "certification"
        / repo_id
        / f"schema-{max(1, DATABASE_SCHEMA_VERSION - 1)}"
        / "state"
        / "run-workspaces"
        / "oh-goon-plan-stale"
    )
    noncurrent.mkdir(parents=True)

    result = certification_bootstrap.reconcile_certification_run_workspaces(
        state_home=state_home,
    )

    assert result.removed == ()
    assert result.refused == ()
    assert noncurrent.exists()


def test_reconciliation_does_not_follow_symlinked_repository_namespace(
    tmp_path: Path,
) -> None:
    state_home = tmp_path / "state-home"
    certification = state_home / "oh-goon" / "certification"
    certification.mkdir(parents=True)
    outside = tmp_path / "outside"
    workspace = outside / "schema-1" / "state" / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)
    (certification / ("repo-" + "a" * 20)).symlink_to(outside, target_is_directory=True)

    result = certification_bootstrap.reconcile_certification_run_workspaces(
        state_home=state_home,
    )

    assert result.removed == ()
    assert result.refused == ()
    assert workspace.exists()


def test_reconciliation_does_not_follow_symlinked_schema_namespace(
    tmp_path: Path,
) -> None:
    state_home = tmp_path / "state-home"
    repository = state_home / "oh-goon" / "certification" / ("repo-" + "b" * 20)
    repository.mkdir(parents=True)
    outside = tmp_path / "outside-schema"
    workspace = outside / "state" / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)
    (repository / "schema-1").symlink_to(outside, target_is_directory=True)

    result = certification_bootstrap.reconcile_certification_run_workspaces(
        state_home=state_home,
    )

    assert result.removed == ()
    assert result.refused == ()
    assert workspace.exists()


def test_current_schema_without_database_is_retained_fail_closed(
    tmp_path: Path,
) -> None:
    state_home = tmp_path / "state-home"
    repository = "repo-" + "c" * 20
    state = (
        state_home
        / "oh-goon"
        / "certification"
        / repository
        / f"schema-{DATABASE_SCHEMA_VERSION}"
        / "state"
    )
    workspace = state / "run-workspaces" / "oh-goon-plan-stale"
    workspace.mkdir(parents=True)

    result = certification_bootstrap.reconcile_certification_run_workspaces(
        state_home=state_home,
    )

    assert result.removed == ()
    assert result.refused == (f"{repository}/oh-goon-plan-stale",)
    assert workspace.exists()
