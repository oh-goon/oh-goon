import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.content_snapshot_proof import snapshot_metadata_fingerprint, strong_tree_proof


def test_strong_tree_proof_detects_same_size_write_with_restored_mtime(tmp_path: Path) -> None:
    source = tmp_path / "source"; source.mkdir(); target = source / "value.txt"; target.write_text("original", encoding="utf-8")
    before = snapshot_metadata_fingerprint(source); identity = snapshot_identity(source); after = snapshot_metadata_fingerprint(source)
    proof = strong_tree_proof(source, identity, metadata_before=before, metadata_after=after)
    assert proof.proves_current(source)
    observed = target.stat(follow_symlinks=False)
    target.write_text("tampered", encoding="utf-8")
    os.utime(target, ns=(observed.st_atime_ns, observed.st_mtime_ns), follow_symlinks=False)
    assert not proof.proves_current(source)


def test_strong_tree_proof_fails_when_metadata_changes_across_verification(tmp_path: Path) -> None:
    source = tmp_path / "source"; source.mkdir(); target = source / "value.txt"; target.write_text("original", encoding="utf-8")
    before = snapshot_metadata_fingerprint(source); identity = snapshot_identity(source); target.chmod(0o600); after = snapshot_metadata_fingerprint(source)
    with pytest.raises(FleetPlanInvalid, match="changed during verification"):
        strong_tree_proof(source, identity, metadata_before=before, metadata_after=after)
