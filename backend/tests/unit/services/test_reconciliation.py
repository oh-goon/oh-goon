from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.vocabulary import CommandSafety, ReconciliationOutcome, WorktreeState
from oh_goon.engine.git import GitService
from oh_goon.services.reconciliation import RepositoryReconciler


@pytest.mark.process
def test_another_source_edit_in_dirty_worktree_cannot_authorize_replay(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    source = repository / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    git = GitService()
    for args in (
        ("init", "-q", "-b", "main"),
        ("add", "module.py"),
        (
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-q",
            "-m",
            "baseline",
        ),
    ):
        result = git.run(*args, cwd=repository)
        assert result.returncode == 0, result.stderr
    source.write_text("value = 2\n", encoding="utf-8")
    baseline = git.fingerprint(repository)
    assert baseline.worktree_state is WorktreeState.DIRTY

    # This effect changes neither Git position nor the two dependency files in
    # the historical fingerprint, and the worktree was already dirty.
    source.write_text("value = 3\n", encoding="utf-8")
    recovered = RepositoryReconciler(git).reconcile(
        repository,
        "op-unknown-source-edit",
        report=None,
        safety=CommandSafety.WORKTREE_MUTATION,
        baseline=baseline,
    )

    assert recovered.current.state_id == baseline.state_id
    assert recovered.outcome is ReconciliationOutcome.INDETERMINATE
    assert not recovered.retry_safe
    assert recovered.requires_operator


@pytest.mark.parametrize(
    "safety", (CommandSafety.ENVIRONMENT_MUTATION, CommandSafety.OPAQUE_MUTATION)
)
def test_opaque_effects_remain_indeterminate(tmp_path: Path, safety: CommandSafety) -> None:
    git = GitService()
    baseline = git.fingerprint(tmp_path)

    recovered = RepositoryReconciler(git).reconcile(
        tmp_path, "op-unknown-effect", report=None, safety=safety, baseline=baseline
    )

    assert recovered.outcome is ReconciliationOutcome.INDETERMINATE
    assert not recovered.retry_safe
