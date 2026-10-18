from __future__ import annotations

from pathlib import Path

from oh_goon.integrations.content_snapshot import snapshot_identity


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
