"""The native branch effect follows durable preparation, including interrupted publication."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.parallel_candidate_verification import ParallelCandidateVerification
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.engine.git import GitService
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.parallel_branch_advance import PreparedBranchAdvance
from oh_goon.integrations.parallel_candidate_patch import NativeCandidatePatch
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase
from oh_goon.services import branch_advance
from oh_goon.services.parallel_candidate_verification import VerifiedCandidatePatch


def test_exact_retry_recovers_published_git_before_confirming_the_original_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare, checkout, source = (tmp_path / name for name in ("bare", "checkout", "source"))
    native = GitService().run("init", "-q", "--bare", str(bare), cwd=tmp_path)
    assert native.returncode == 0, native.stderr
    for path in (checkout, source):
        path.mkdir()
    (checkout / ".git").mkdir()
    (checkout / ".git/config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    database = LocalDatabase(tmp_path / "state" / "app.sqlite")
    store = BranchAdvanceStore(database)
    request = branch_advance.BranchAdvanceRequest(
        advance_id="next-change",
        fleet_run_id="candidate-1",
        checkpoint_id="checkpoint",
        branch_owner=bare,
        workspace=checkout,
        branch_ref="refs/heads/agents/first",
        expected_tip="a" * 40,
    )
    commit, tree = "b" * 40, "c" * 40
    proposed = PreparedBranchAdvance(request.branch_ref, request.expected_tip, commit, tree)
    patch = tmp_path / "native.patch"
    patch.write_bytes(b"exact native patch")
    verification = ParallelCandidateVerification(
        fleet_run_id=request.fleet_run_id,
        parallel_work_id="original",
        workspace_id="candidate",
        base_revision=request.expected_tip,
        base_source_id="base-source",
        verifier_id="frozen-verifier",
        checkpoint_id=request.checkpoint_id,
        source_sha256="d" * 64,
        result_identity="accepted-result",
        physical_execution_id="native-verifier",
        native_outcome="PASS",
        outcome="PASS",
        reason="frozen verifier passed",
    )
    original = SimpleNamespace(
        outcome=FleetRunOutcome.SUCCESS,
        run=SimpleNamespace(targets=(SimpleNamespace(path=source),)),
    )
    inspect = Mock(return_value=original)
    journal = Mock()
    journal.return_value.read_view.return_value.required_run_detail = inspect
    monkeypatch.setattr(branch_advance, "Journal", journal)
    monkeypatch.setattr(branch_advance, "require_branch_checkout", Mock())
    selected = Mock(
        return_value=VerifiedCandidatePatch(
            NativeCandidatePatch(patch, ("changed.txt",), tree),
            verification,
            SnapshotIdentity("d" * 64, 12, 1),
            Mock(),
        )
    )
    monkeypatch.setattr(branch_advance, "prepare_verified_candidate_patch", selected)
    prepare_git = Mock(return_value=proposed)
    monkeypatch.setattr(branch_advance, "prepare_branch_advance", prepare_git)
    repositories = Mock()
    repositories.require_included.return_value.path = checkout
    published = False

    def publish(*_: object, **options: object) -> None:
        nonlocal published
        assert options["prepared"] == proposed
        pending = store.find(request.advance_id)
        assert pending is not None and pending.published_at is None
        assert pending.preparation.branch_owner_subject_id.startswith("bare-branch-owner-")
        assert pending.preparation.patch_sha256 == hashlib.sha256(patch.read_bytes()).hexdigest()
        if not published:
            lock = bare / "HEAD.lock"
            lock.touch()
            lock.unlink()
            require_current = options["require_current"]
            assert callable(require_current)
            require_current()
            published = True
            raise OSError("Git acknowledgement interrupted")

    monkeypatch.setattr(branch_advance, "publish_branch_advance", publish)
    monkeypatch.setattr(branch_advance, "read_branch_tip", lambda *_, **_options: commit)
    with pytest.raises(OSError, match="acknowledgement interrupted"):
        branch_advance.advance_verified_candidate(
            request, database=database, repositories=repositories
        )
    assert published
    pending = store.find(request.advance_id)
    assert pending is not None and pending.published_at is None

    reopened = LocalDatabase(database.path)
    confirmed = branch_advance.advance_verified_candidate(
        request, database=reopened, repositories=repositories
    )
    assert confirmed.preparation == pending.preparation and confirmed.published_at is not None
    assert (
        branch_advance.advance_verified_candidate(
            request, database=reopened, repositories=repositories
        )
        == confirmed
    )
    selected.assert_called_once()
    prepare_git.assert_called_once()
    inspect.assert_called_once_with(request.fleet_run_id)
    with pytest.raises(StateConflict, match="different request"):
        branch_advance.advance_verified_candidate(
            request.model_copy(update={"checkpoint_id": "other"}),
            database=reopened,
            repositories=repositories,
        )
