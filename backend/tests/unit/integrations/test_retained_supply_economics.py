from pathlib import Path

from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.content_snapshot_proof import (
    snapshot_metadata_fingerprint,
    strong_tree_proof,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _owned(path: Path) -> RepositoryScopedPath:
    return RepositoryScopedPath.create(
        owner_repository_id=OWNER,
        root=path,
        path=path,
        label="economics retained content",
    )


def test_verified_tree_economics_distinguish_cold_copy_from_warm_reuse(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "entry.txt").write_text("verified-economics", encoding="utf-8")
    expected = snapshot_identity(source)
    store = FleetContentStore(tmp_path / "store")

    cold = []
    metadata = snapshot_metadata_fingerprint(source)
    proof = strong_tree_proof(source, expected, metadata_before=metadata, metadata_after=metadata)
    first = store.put_verified_tree(_owned(source), proof, economics=cold.append)
    store.release_reference(first.reference_id, owner_repository_id=OWNER)
    metadata = snapshot_metadata_fingerprint(source)
    proof = strong_tree_proof(source, expected, metadata_before=metadata, metadata_after=metadata)
    warm = []
    second = store.put_verified_tree(_owned(source), proof, economics=warm.append)

    assert len(cold) == len(warm) == 1
    cold_receipt, warm_receipt = cold[0], warm[0]
    assert (cold_receipt.outcome, cold_receipt.bytes_copied, cold_receipt.bytes_hashed) == (
        "PERFORMED",
        expected.byte_count,
        expected.byte_count,
    )
    assert cold_receipt.bytes_read == expected.byte_count * 2
    assert cold_receipt.bytes_written == expected.byte_count
    assert cold_receipt.reused_bytes == 0
    assert (warm_receipt.outcome, warm_receipt.bytes_copied, warm_receipt.bytes_written) == (
        "REUSED",
        0,
        0,
    )
    assert warm_receipt.bytes_hashed == expected.byte_count
    assert warm_receipt.bytes_read == expected.byte_count
    assert warm_receipt.reused_bytes == expected.byte_count
    assert cold_receipt.strong_proof_performed is warm_receipt.strong_proof_performed is True
    assert cold_receipt.strong_proof_reused is False
    assert warm_receipt.strong_proof_reused is True
    assert warm_receipt.operation_identity == cold_receipt.operation_identity
    assert second.object_id == first.object_id
