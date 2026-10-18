from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.content_snapshot_proof import snapshot_metadata_fingerprint, strong_tree_proof
from oh_goon.integrations.fleet_content_store import FleetContentStore

OWNER = "repo-cccccccccccccccccccc"

def _owned(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(owner_repository_id=OWNER, root=path, path=path, label="verified supply proof")

def _proof(path: Path):
    before = snapshot_metadata_fingerprint(path)
    identity = snapshot_identity(path)
    after = snapshot_metadata_fingerprint(path)
    return strong_tree_proof(path, identity, metadata_before=before, metadata_after=after)

def test_strong_source_proof_is_the_only_verified_store_authority(tmp_path: Path) -> None:
    source = tmp_path / "source"; source.mkdir(); (source / "value.txt").write_text("original", encoding="utf-8")
    proof = _proof(source)
    store = FleetContentStore(tmp_path / "store")
    first = store.put_verified_tree(_owned(source), proof)
    store.release_reference(first.reference_id, owner_repository_id=OWNER)
    receipts=[]
    store.put_verified_tree(_owned(source), proof, economics=receipts.append)
    assert receipts[0].bytes_hashed == proof.identity.byte_count
    assert receipts[0].strong_proof_reused is True

def test_stale_strong_source_proof_fails_closed_without_source_rehash_contract(tmp_path: Path) -> None:
    source = tmp_path / "source"; source.mkdir(); target=source / "value.txt"; target.write_text("original", encoding="utf-8")
    proof = _proof(source)
    store = FleetContentStore(tmp_path / "store")
    first=store.put_verified_tree(_owned(source), proof); store.release_reference(first.reference_id, owner_repository_id=OWNER)
    target.write_text("tampered", encoding="utf-8")
    with pytest.raises(FleetPlanInvalid, match="strong proof"):
        store.put_verified_tree(_owned(source), proof)
