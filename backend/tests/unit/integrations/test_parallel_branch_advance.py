from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.engine.git import GitService
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.integrations.parallel_branch_advance import (
    PreparedBranchAdvance,
    prepare_branch_advance,
    publish_branch_advance,
    read_branch_tip,
    require_branch_checkout,
    require_published_branch_advance,
    sync_branch_checkout,
)
from oh_goon.integrations.source_snapshot import source_snapshot_identity

_REF = "refs/heads/agent"


def _git(path: Path, *args: str) -> str:
    result = GitService().run(*args, cwd=path)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _current() -> None:
    pass


@dataclass(frozen=True)
class _Round:
    owner: Path
    clone: Path
    candidate: Path
    patch: Path
    scratch: Path
    base: str
    tree: str
    identity: SnapshotIdentity

    def prepare(
        self, *, tree: str | None = None, identifier: str = "round-1"
    ) -> PreparedBranchAdvance:
        return prepare_branch_advance(
            GitService(),
            repository=self.owner,
            ref=_REF,
            expected_tip=self.base,
            patch=self.patch,
            patch_sha256=sha256_file_with_size(self.patch)[0],
            verified_tree=self.tree if tree is None else tree,
            operation_id=identifier,
            scratch=self.scratch,
            require_current=_current,
        )

    def tip(self) -> str:
        return read_branch_tip(
            GitService(), repository=self.owner, ref=_REF, require_current=_current
        )

    def sync(self, prepared: PreparedBranchAdvance) -> None:
        sync_branch_checkout(
            GitService(),
            repository=self.owner,
            checkout=self.clone,
            prepared=prepared,
            candidate_identity=self.identity,
            scratch=self.scratch,
            require_current=_current,
            require_checkout_current=_current,
        )


@pytest.fixture
def native_round(tmp_path: Path) -> _Round:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    _git(candidate, "init", "--quiet", "--initial-branch=agent", "--template=")
    _git(candidate, "config", "user.name", "Test Author")
    _git(candidate, "config", "user.email", "author@example.invalid")
    (candidate / "tracked.txt").write_text("original\n")
    (candidate / "delete.txt").write_text("remove me\n")
    _git(candidate, "add", ".")
    _git(candidate, "commit", "--quiet", "--no-gpg-sign", "-m", "Base")
    base = _git(candidate, "rev-parse", "HEAD")
    owner = tmp_path / "branch-owner.git"
    clone = tmp_path / "receiving"
    _git(tmp_path, "clone", "--quiet", "--bare", "--no-hardlinks", str(candidate), str(owner))
    _git(tmp_path, "clone", "--quiet", "--no-hardlinks", str(owner), str(clone))
    _git(clone, "switch", "--quiet", "--detach", base)
    (candidate / "tracked.txt").write_text("verified change\n")
    (candidate / "delete.txt").unlink()
    (candidate / "binary.dat").write_bytes(b"\0\xffbytes\0")
    _git(candidate, "add", "--all")
    patch = tmp_path / "verified.patch"
    _git(candidate, "diff", "--cached", "--full-index", "--binary", f"--output={patch}", "HEAD")
    scratch = tmp_path / "private"
    scratch.mkdir(mode=0o700)
    return _Round(
        owner,
        clone,
        candidate,
        patch,
        scratch,
        base,
        _git(candidate, "write-tree"),
        source_snapshot_identity(candidate, reject_dotenv_state=True),
    )


def test_verified_commit_prepare_cas_and_detached_sync_are_replay_safe(
    native_round: _Round,
) -> None:
    original = native_round
    prepared = original.prepare()
    assert original.prepare() == prepared
    assert original.tip() == original.base
    assert (
        _git(original.owner, "show", "--no-patch", "--format=%P", prepared.commit) == original.base
    )
    assert _git(original.owner, "rev-parse", prepared.commit + "^{tree}") == original.tree
    native_ref = _git(original.clone, "show-ref", "--verify", "--hash", _REF)
    for _ in range(2):
        publish_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
        original.sync(prepared)
        assert original.tip() == prepared.commit
        assert _git(original.clone, "rev-parse", "HEAD") == prepared.commit
        assert _git(original.clone, "show-ref", "--verify", "--hash", _REF) == native_ref
        assert (
            source_snapshot_identity(original.clone, reject_dotenv_state=True) == original.identity
        )
    assert _git(original.candidate, "rev-parse", "HEAD") == original.base


def test_git_cas_preserves_a_concurrent_branch_tip(native_round: _Round) -> None:
    original = native_round
    prepared = original.prepare()
    competing = original.prepare(identifier="competing-round")
    observed = 0

    def advance_at_effect_boundary() -> None:
        nonlocal observed
        observed += 1
        if observed == 2:
            _git(original.owner, "update-ref", _REF, competing.commit, original.base)

    with pytest.raises(StateConflict, match="did not publish the exact"):
        publish_branch_advance(
            GitService(),
            repository=original.owner,
            prepared=prepared,
            require_current=advance_at_effect_boundary,
        )
    assert original.tip() == competing.commit
    assert _git(original.clone, "rev-parse", "HEAD") == original.base


def test_only_original_git_publication_can_be_replayed_after_a_reset(native_round: _Round) -> None:
    original = native_round
    prepared = original.prepare()
    _git(original.owner, "update-ref", _REF, prepared.commit, original.base)
    with pytest.raises(StateConflict, match="no longer has the expected tip"):
        publish_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
    with pytest.raises(StateConflict, match="beyond the selected receipt"):
        original.sync(prepared)
    with pytest.raises(StateConflict, match="beyond the selected receipt"):
        require_published_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
    _git(original.owner, "update-ref", _REF, original.base, prepared.commit)
    publish_branch_advance(
        GitService(), repository=original.owner, prepared=prepared, require_current=_current
    )
    require_published_branch_advance(
        GitService(), repository=original.owner, prepared=prepared, require_current=_current
    )
    _git(original.owner, "update-ref", _REF, original.base, prepared.commit)
    with pytest.raises(StateConflict, match="changed after its original advancement"):
        publish_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
    assert original.tip() == original.base
    assert _git(original.clone, "rev-parse", "HEAD") == original.base


def test_rejects_wrong_tree_and_linked_worktree_before_moving_ref(
    native_round: _Round, tmp_path: Path
) -> None:
    original = native_round
    base_tree = _git(original.owner, "rev-parse", original.base + "^{tree}")
    with pytest.raises(StateConflict, match="differs from the verified Git tree"):
        original.prepare(tree=base_tree)
    prepared = original.prepare()
    linked = tmp_path / "linked"
    _git(original.owner, "worktree", "add", "--quiet", str(linked), "agent")
    with pytest.raises(FleetPlanInvalid, match="without linked worktrees"):
        publish_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
    assert _git(original.owner, "rev-parse", _REF) == original.base


def test_native_branch_storage_cannot_write_through_external_aliases(
    native_round: _Round, tmp_path: Path
) -> None:
    original = native_round
    outside = tmp_path / "outside"
    outside.mkdir()
    blob = _git(original.candidate, "hash-object", "tracked.txt")
    object_slot = original.owner / "objects" / blob[:2]
    object_slot.symlink_to(outside, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="object storage aliases"):
        original.prepare()
    object_slot.unlink()

    prepared = original.prepare()
    _git(original.owner, "config", "core.logAllRefUpdates", "always")
    log_directory = original.owner / "logs"
    log_directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(FleetPlanInvalid, match="storage aliases"):
        publish_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
    assert _git(original.owner, "rev-parse", _REF) == original.base
    assert not tuple(outside.iterdir())

    log_directory.unlink()
    reflog = log_directory / _REF
    reflog.parent.mkdir(parents=True)
    outside_log = outside / "external-reflog"
    outside_log.touch()
    reflog.hardlink_to(outside_log)
    with pytest.raises(FleetPlanInvalid, match="reflog aliases"):
        publish_branch_advance(
            GitService(), repository=original.owner, prepared=prepared, require_current=_current
        )
    assert _git(original.owner, "rev-parse", _REF) == original.base
    assert not outside_log.read_bytes()
    reflog.unlink()
    publish_branch_advance(
        GitService(), repository=original.owner, prepared=prepared, require_current=_current
    )
    (original.clone / ".git/objects/info/alternates").write_text(str(outside) + "\n")
    with pytest.raises(FleetPlanInvalid, match="without alternates"):
        original.sync(prepared)
    assert _git(original.clone, "rev-parse", "HEAD") == original.base


def test_sync_rejects_dirty_hidden_bytes_and_native_rewrites(native_round: _Round) -> None:
    original = native_round
    prepared = original.prepare()
    publish_branch_advance(
        GitService(), repository=original.owner, prepared=prepared, require_current=_current
    )
    _git(original.clone, "update-index", "--assume-unchanged", "tracked.txt")
    (original.clone / "tracked.txt").write_text("unapproved bytes\n")
    assert not _git(original.clone, "status", "--porcelain")
    with pytest.raises(StateConflict, match="outside native clean status"):
        require_branch_checkout(
            GitService(),
            checkout=original.clone,
            allowed_tips=(original.base,),
            require_current=_current,
            scratch=original.scratch,
        )
    with pytest.raises(StateConflict, match="outside native clean status"):
        original.sync(prepared)
    assert (original.clone / "tracked.txt").read_text() == "unapproved bytes\n"
    (original.clone / "tracked.txt").write_text("original\n")
    included = original.scratch / "local.cfg"
    included.write_text(f'[url "file:///redirected"]\n\tinsteadOf = {original.owner}\n')
    _git(original.clone, "config", "include.path", str(included))
    with pytest.raises(FleetPlanInvalid, match="transfer rewrites"):
        original.sync(prepared)
    assert _git(original.clone, "rev-parse", "HEAD") == original.base
