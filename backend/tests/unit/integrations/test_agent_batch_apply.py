import hashlib
import json
import os

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.agent_patch_apply import BatchApplyIncomplete, apply_batch_proposal


def batch(operations):
    return json.dumps(
        dict(
            schema="agent-patch-batch-v1",
            candidate_identity="candidate",
            summary="Scoped repair",
            operations=operations,
        )
    )


def op(action, path, original=None, replacement=None):
    return dict(
        action=action,
        path="repo/" + path,
        source_sha256=hashlib.sha256(original.encode()).hexdigest()
        if original is not None
        else None,
        replacement=replacement,
    )


def test_batch_creates_updates_deletes_with_preflight_and_preserves_mode(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/change").write_text("old")
    (tmp_path / "src/change").chmod(0o755)
    (tmp_path / "src/remove").write_text("remove")
    proposal = batch(
        [
            op("create", "src/new", replacement=""),
            op("update", "src/change", "old", "fixed"),
            op("delete", "src/remove", "remove"),
        ]
    )
    receipt = apply_batch_proposal(
        proposal, workspace=tmp_path, candidate_identity="candidate", write_roots=("src",)
    )
    assert receipt.complete
    assert set(receipt.affected_paths) == {"src/change", "src/new", "src/remove"}
    assert (tmp_path / "src/new").read_bytes() == b""
    assert (tmp_path / "src/change").read_text() == "fixed"
    assert (tmp_path / "src/change").stat().st_mode & 0o777 == 0o755
    assert not (tmp_path / "src/remove").exists()


@pytest.mark.parametrize(
    "attack",
    [
        "stale",
        "escape",
        "duplicate",
        "symlink",
        "create_exists",
        "wrong_candidate",
        "delete_missing",
        "hidden",
    ],
)
def test_invalid_later_member_never_changes_an_earlier_member(tmp_path, attack):
    (tmp_path / "a").write_text("original")
    (tmp_path / "z").write_text("last")
    later = op("update", "z", "last", "new")
    candidate = "candidate"
    if attack == "stale":
        later["source_sha256"] = "0" * 64
    elif attack == "escape":
        later["path"] = "repo/../outside"
    elif attack == "duplicate":
        later = op("update", "a", "original", "new")
    elif attack == "symlink":
        (tmp_path / "z").unlink()
        (tmp_path / "z").symlink_to(tmp_path / "a")
    elif attack == "create_exists":
        later = op("create", "z", replacement="new")
    elif attack == "wrong_candidate":
        candidate = "different"
    elif attack == "delete_missing":
        later = op("delete", "missing", "old")
    else:
        later["path"] = "repo/.git/config"
    with pytest.raises((FleetPlanInvalid, OSError)):
        apply_batch_proposal(
            batch([op("update", "a", "original", "first"), later]),
            workspace=tmp_path,
            candidate_identity=candidate,
            write_roots=("a", "z", "missing"),
        )
    assert (tmp_path / "a").read_text() == "original"
    assert not list(tmp_path.glob(".oh-goon-batch-*"))


def test_partial_native_io_failure_is_explicit_and_does_not_revert_unrelated_edits(
    tmp_path, monkeypatch
):
    for name in ("a", "z"):
        (tmp_path / name).write_text("old")
    native = os.replace

    def fail_second(source, target, **kwargs):
        if target == "z":
            raise OSError("injected publication failure")
        return native(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", fail_second)
    with pytest.raises(BatchApplyIncomplete) as incomplete:
        apply_batch_proposal(
            batch([op("update", "a", "old", "new"), op("update", "z", "old", "new")]),
            workspace=tmp_path,
            candidate_identity="candidate",
            write_roots=("a", "z"),
        )
    assert incomplete.value.affected_paths == ("a", "z")
    assert (tmp_path / "a").read_text() == "new"
    assert (tmp_path / "z").read_text() == "old"
    assert not list(tmp_path.glob(".oh-goon-batch-*"))


def test_candidate_identity_is_required_before_any_batch_effect(tmp_path):
    with pytest.raises(FleetPlanInvalid, match="explicit candidate"):
        apply_batch_proposal(
            batch([op("create", "new", replacement="new")]),
            workspace=tmp_path,
            candidate_identity=None,
            write_roots=("new",),
        )
    assert not list(tmp_path.iterdir())


def test_preimage_change_after_preparation_stops_before_the_first_publication(
    tmp_path, monkeypatch
):
    for name in ("a", "z"):
        (tmp_path / name).write_text("old")
    native_fsync = os.fsync
    syncs = 0

    def change_last_preimage(fd):
        nonlocal syncs
        native_fsync(fd)
        syncs += 1
        if syncs == 2:
            (tmp_path / "z").write_text("concurrent edit")

    monkeypatch.setattr(os, "fsync", change_last_preimage)
    with pytest.raises(FleetPlanInvalid, match="preimage changed"):
        apply_batch_proposal(
            batch([op("update", "a", "old", "new"), op("update", "z", "old", "new")]),
            workspace=tmp_path,
            candidate_identity="candidate",
            write_roots=("a", "z"),
        )
    assert (tmp_path / "a").read_text() == "old"
    assert (tmp_path / "z").read_text() == "concurrent edit"
    assert not list(tmp_path.glob(".oh-goon-batch-*"))


@pytest.mark.parametrize("moved_directory", ["workspace", "parent"])
def test_directory_replacement_during_preflight_cannot_redirect_batch_writes(
    tmp_path, monkeypatch, moved_directory
):
    workspace = tmp_path / "workspace"
    parent = workspace / "src"
    parent.mkdir(parents=True)
    (parent / "file").write_text("old")
    moved = tmp_path / "moved"
    native_fsync = os.fsync
    swapped = False

    def move_after_staging(fd):
        nonlocal swapped
        native_fsync(fd)
        if swapped:
            return
        swapped = True
        directory = workspace if moved_directory == "workspace" else parent
        directory.rename(moved)
        directory.mkdir()

    monkeypatch.setattr(os, "fsync", move_after_staging)
    with pytest.raises(FleetPlanInvalid, match="directory changed"):
        apply_batch_proposal(
            batch([op("update", "src/file", "old", "new")]),
            workspace=workspace,
            candidate_identity="candidate",
            write_roots=("src",),
        )
    original = moved / "src/file" if moved_directory == "workspace" else moved / "file"
    assert original.read_text() == "old"
    assert not (workspace / "src/file").exists()
    assert not list(moved.rglob(".oh-goon-batch-*"))
