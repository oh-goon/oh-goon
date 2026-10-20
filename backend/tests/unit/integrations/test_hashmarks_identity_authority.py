from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.hashmarks.identity import identify_path, identify_tree_and_children


def test_write_permission_changes_do_not_change_content_identity(tmp_path: Path) -> None:
    path = tmp_path / "tree"
    path.mkdir()
    file = path / "payload.txt"
    file.write_text("same bytes", encoding="utf-8")
    before = snapshot_identity(path)
    file.chmod(0o444)
    after = snapshot_identity(path)
    assert after.sha256 == before.sha256
    assert after.byte_count == before.byte_count
    assert after.entry_count == before.entry_count


def test_executable_semantic_remains_part_of_hashmarks_identity(tmp_path: Path) -> None:
    path = tmp_path / "tree"
    path.mkdir()
    file = path / "tool.sh"
    file.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    file.chmod(0o644)
    before = snapshot_identity(path)
    file.chmod(0o755)
    after = snapshot_identity(path)
    assert after.sha256 != before.sha256


def test_inventory_counts_are_evidence_not_digest_inputs(tmp_path: Path) -> None:
    path = tmp_path / "tree"
    path.mkdir()
    file = path / "payload.txt"
    file.write_text("abc", encoding="utf-8")
    identity = snapshot_identity(path)
    assert identity.byte_count == 3
    assert identity.entry_count == 1
    assert len(identity.sha256) == 64


def test_parent_and_children_keep_input_root_hashes_and_physical_counts(tmp_path: Path) -> None:
    from hashmarks import RepositoryIdentity

    root = tmp_path / "tree"
    (root / "child" / "__pycache__").mkdir(parents=True)
    (root / "child" / "kept").write_bytes(b"yes")
    (root / "child" / "__pycache__" / "ignored").write_bytes(b"physical")
    observed = identify_tree_and_children(root, ("child",))
    with RepositoryIdentity(root, mode="local", state_dir=tmp_path / "parent-state") as native:
        assert observed.identity.sha256 == native.snapshot("*", verify=True).hash
    with RepositoryIdentity(
        root / "child", mode="local", state_dir=tmp_path / "child-state"
    ) as native:
        assert observed.children["child"].sha256 == native.snapshot("*", verify=True).hash
    assert observed.identity.byte_count == observed.children["child"].byte_count == 11
    assert observed.identity.entry_count == 4
    assert observed.children["child"].entry_count == 3
    assert observed.identity.sha256 != observed.children["child"].sha256


def test_literal_exclusion_omits_bytes_and_preserves_filtered_snapshot_identity(
    tmp_path: Path,
) -> None:
    root, expected = tmp_path / "tree", tmp_path / "filtered"
    (root / "src" / "generated").mkdir(parents=True)
    (expected / "src").mkdir(parents=True)
    for tree in (root, expected):
        (tree / "src" / "keep").write_bytes(b"kept")
        (tree / "l").write_bytes(b"literal")
    (root / "src" / "generated" / "omit").write_bytes(b"excluded")
    (root / "[literal]").write_bytes(b"excluded")
    observed = identify_path(
        root, exclude_paths=(root / "src" / "generated", "[literal]", "absent")
    )
    assert observed == identify_path(expected)
    assert observed.byte_count == 11
    assert observed.entry_count == 3


def test_native_physical_progress_remains_cancellable(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    (root / "child" / "__pycache__").mkdir(parents=True)
    (root / "child" / "__pycache__" / "ignored").write_bytes(b"physical")
    progress: list[tuple[int, int]] = []
    with pytest.raises(OperationCancelledError):
        identify_tree_and_children(
            root,
            (),
            cancel_requested=lambda: bool(progress),
            progress=lambda entries, size: progress.append((entries, size)),
        )
    assert progress
    progress.clear()
    observed = identify_tree_and_children(
        root, (), progress=lambda entries, size: progress.append((entries, size))
    )
    assert progress[-1] == (observed.identity.entry_count, observed.identity.byte_count)


def test_literal_symlink_count_does_not_include_external_target_bytes(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (root / "link").symlink_to(outside)
    observed = identify_path(root)
    outside.write_bytes(b"changed external target")
    assert identify_path(root) == observed
    assert observed.byte_count == 0
    assert observed.entry_count == 1
