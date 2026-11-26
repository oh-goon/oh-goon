from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid, StateConflict
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerifier,
)
from oh_goon.engine.git import GitService
from oh_goon.integrations.agent_native_execution import native_executable_sha256
from oh_goon.integrations.parallel_candidate_patch import prepare_native_candidate_patch
from oh_goon.integrations.parallel_candidate_verifier import (
    observe_parallel_verifier_result,
    prepare_parallel_verifier,
    require_verifier_executable,
)
from oh_goon.integrations.source_snapshot import snapshot_source_directory, source_snapshot_identity
from oh_goon.services.fleet_plan_candidate import workspace_control_git


def _git(git: GitService, repository: Path, *argv: str) -> str:
    result = git.run(*argv, cwd=repository)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _repository(tmp_path: Path) -> tuple[GitService, Path, Path, str]:
    git = workspace_control_git()
    root = tmp_path / "repository"
    root.mkdir()
    _git(git, root, "init", "--template=")
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (root / "remove.txt").write_text("old source\n", encoding="utf-8")
    (root / "edit.txt").write_text("old text\n", encoding="utf-8")
    _git(git, root, "add", "--all")
    _git(
        git,
        root,
        "-c",
        "user.name=Local",
        "-c",
        "user.email=test@example.test",
        "commit",
        "--message",
        "base",
    )
    base = _git(git, root, "rev-parse", "HEAD")
    candidate = tmp_path / "worktree"
    _git(git, root, "worktree", "add", "--detach", str(candidate), base)
    return git, root, candidate, base


def test_native_patch_accounts_for_nested_binary_addition_deletion_and_edit(tmp_path: Path) -> None:
    git, root, candidate, base = _repository(tmp_path)
    frozen = tmp_path / "frozen"
    base_identity = snapshot_source_directory(root, frozen, label="test base")
    (candidate / "remove.txt").unlink()
    (candidate / "edit.txt").write_text("new text\n", encoding="utf-8")
    (candidate / "new").mkdir()
    (candidate / "new" / "file.bin").write_bytes(b"\x00\xff\x01\x00")
    verifier_candidate = source_snapshot_identity(candidate)
    stage = tmp_path / "stage"
    stage.mkdir()

    patch = prepare_native_candidate_patch(
        git,
        original=candidate,
        original_revision=base,
        base_snapshot=frozen,
        base_identity=base_identity,
        candidate_identity=verifier_candidate,
        scratch=stage,
    )

    assert patch.changed_paths == ("edit.txt", "new/file.bin", "remove.txt")
    assert b"GIT binary patch" in patch.path.read_bytes()
    assert _git(git, stage / "replay", "write-tree") == patch.tree
    assert source_snapshot_identity(root) == base_identity
    assert _git(git, root, "rev-parse", "HEAD") == base


@pytest.mark.parametrize("unsupported", ["ignored", "empty_directory"])
def test_native_patch_refuses_changes_it_cannot_represent(tmp_path: Path, unsupported: str) -> None:
    git, root, candidate, base = _repository(tmp_path)
    frozen = tmp_path / "frozen"
    base_identity = snapshot_source_directory(root, frozen, label="test base")
    (candidate / "edit.txt").write_text("new text\n", encoding="utf-8")
    if unsupported == "ignored":
        (candidate / "ignored.txt").write_text("out of scope\n", encoding="utf-8")
        expected_error = "ignored output"
    else:
        (candidate / "empty").mkdir()
        expected_error = "does not reconstruct"
    stage = tmp_path / "stage"
    stage.mkdir()

    with pytest.raises((StateConflict, FleetPlanInvalid), match=expected_error):
        prepare_native_candidate_patch(
            git,
            original=candidate,
            original_revision=base,
            base_snapshot=frozen,
            base_identity=base_identity,
            candidate_identity=source_snapshot_identity(candidate),
            scratch=stage,
        )


def test_verifier_snapshot_binds_original_input_and_detects_mode_only_mutation(
    tmp_path: Path,
) -> None:
    native = Path("/usr/bin/true").resolve()
    if not native.is_file():
        pytest.skip("test requires a native executable under the standard Bubblewrap system bind")
    body = "from pathlib import Path\nassert Path('expected').read_text() == 'verified'\n"
    verifier = ParallelCandidateVerifier(
        acceptance="The expected file has the admitted value",
        executable=str(native),
        executable_sha256=native_executable_sha256(native),
        arguments=("-I", "{input:check.py}", "{candidate}"),
        inputs=(
            FrozenVerifierInput(
                name="check.py", content=body, sha256=hashlib.sha256(body.encode()).hexdigest()
            ),
        ),
    )
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "expected").write_text("verified", encoding="utf-8")
    (candidate / "check.py").write_text("candidate cannot replace oracle", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    identity, fingerprint, mounts = prepare_parallel_verifier(
        candidate,
        scratch,
        verifier,
        cancel_requested=None,
    )
    assert (mounts[1].source / "check.py").read_text() == body
    assert (mounts[0].source / "expected").read_text() == "verified"
    assert verifier.argv[-2:] == (str(mounts[1].target / "check.py"), str(mounts[0].target))
    unchanged = observe_parallel_verifier_result(
        candidate, identity, fingerprint, verifier, cancel_requested=None
    )
    assert unchanged.postcondition == "UNCHANGED"
    assert unchanged.verifier_id == verifier.identity

    (candidate / "expected").chmod(0o755)
    changed = observe_parallel_verifier_result(
        candidate, identity, fingerprint, verifier, cancel_requested=None
    )
    assert changed.postcondition == "MISMATCH"

    with pytest.raises(FleetPlanInvalid, match="executable changed"):
        require_verifier_executable(verifier.model_copy(update={"executable_sha256": "0" * 64}))
