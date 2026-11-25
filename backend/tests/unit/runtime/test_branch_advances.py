from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.branch_advance import (
    PreparedBranchAdvance,
    branch_round_run_id,
    is_branch_round_run_id,
)
from oh_goon.core.models.parallel_candidate_verification import ParallelCandidateVerification
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase


def _preparation(root: Path, advance_id: str = "advance-a") -> PreparedBranchAdvance:
    prior = "a" * 40
    return PreparedBranchAdvance(
        advance_id=advance_id,
        request_sha256="1" * 64,
        repository_path=root / "checkout",
        repository_id="repository-a",
        checkout_identity="checkout-anchor-a",
        branch_owner_path=root / "branch-owner.git",
        branch_owner_subject_id="branch-owner-instance-a",
        branch_ref="refs/heads/agent-a",
        expected_tip=prior,
        commit="b" * 40,
        tree="c" * 40,
        patch_sha256="d" * 64,
        verification=ParallelCandidateVerification(
            fleet_run_id="candidate-run-a",
            parallel_work_id="work-group-a",
            workspace_id="workspace-a",
            base_revision=prior,
            base_source_id="original-source",
            verifier_id="frozen-verifier-a",
            checkpoint_id="verified-checkpoint-a",
            source_sha256="e" * 64,
            result_identity="result-a",
            physical_execution_id="native-verifier-a",
            native_outcome="PASS",
            outcome="PASS",
            reason="Candidate passed its frozen verifier",
        ),
        source_byte_count=30,
        source_entry_count=2,
    )


def test_advancement_replays_only_its_exact_publication_and_successor(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    preparation = _preparation(tmp_path)
    store = BranchAdvanceStore(LocalDatabase(path))
    prepared = store.prepare(preparation)
    assert prepared.published_at is None
    assert store.prepare(preparation) == prepared

    next_run_id = branch_round_run_id(preparation.advance_id)
    assert is_branch_round_run_id(next_run_id)
    assert not is_branch_round_run_id(next_run_id + "a")
    next_hash = "2" * 64
    with pytest.raises(StateConflict, match="awaiting publication"):
        store.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id=next_run_id
        )
    with pytest.raises(StateConflict, match="prepared commit"):
        store.confirm(preparation, observed_tip=preparation.expected_tip)
    with pytest.raises(StateConflict, match="different proposal"):
        store.prepare(preparation.model_copy(update={"request_sha256": "3" * 64}))
    assert store.find_by_next_run(next_run_id) is None

    reopened = BranchAdvanceStore(LocalDatabase(path))
    assert reopened.find(preparation.advance_id) == prepared
    published = reopened.confirm(preparation, observed_tip=preparation.commit)
    assert published.published_at is not None
    with reopened.lock_next_request(preparation.advance_id):
        attached = reopened.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id=next_run_id
        )
    assert attached.published_at == published.published_at
    assert attached.next_run is not None
    assert attached.next_run.fleet_run_id == next_run_id
    assert attached.next_run.repository_subject_id is None
    with pytest.raises(StateConflict, match="original reserved request"):
        reopened.bind_next_source(
            preparation.advance_id,
            repository_subject_id="synchronized-subject",
            fleet_run_id=next_run_id,
            request_sha256="5" * 64,
        )
    source = {
        "repository_subject_id": "synchronized-subject",
        "fleet_run_id": next_run_id,
        "request_sha256": next_hash,
    }
    source_bound = reopened.bind_next_source(preparation.advance_id, **source)
    assert source_bound == reopened.bind_next_source(preparation.advance_id, **source)
    assert source_bound.next_run is not None
    assert source_bound.next_run.repository_subject_id == "synchronized-subject"
    with pytest.raises(StateConflict, match="different repository subject"):
        reopened.bind_next_source(
            preparation.advance_id, **{**source, "repository_subject_id": "foreign-subject"}
        )
    assert reopened.confirm(preparation, observed_tip=preparation.commit) == source_bound
    assert (
        reopened.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id=next_run_id
        )
        == source_bound
    )
    with pytest.raises(StateConflict, match="different successor"):
        reopened.bind_next_request(
            preparation.advance_id, request_sha256="4" * 64, fleet_run_id=next_run_id
        )
    with pytest.raises(StateConflict, match="Successor Run ID differs"):
        reopened.bind_next_request(
            preparation.advance_id, request_sha256=next_hash, fleet_run_id="other-run"
        )
    readback = BranchAdvanceStore(LocalDatabase(path, read_only=True))
    assert (
        readback.find(preparation.advance_id)
        == readback.find_by_next_run(next_run_id)
        == source_bound
    )


def test_concurrent_advances_reserve_the_same_native_tip_only_once(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    first = BranchAdvanceStore(LocalDatabase(path))
    second = BranchAdvanceStore(LocalDatabase(path))

    def prepare(store: BranchAdvanceStore, advance_id: str) -> bool:
        try:
            store.prepare(_preparation(tmp_path, advance_id))
        except StateConflict as error:
            assert str(error) == "This exact prior branch tip already has an advancement"
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as workers:
        a = workers.submit(prepare, first, "a")
        b = workers.submit(prepare, second, "b")
        assert sorted((a.result(timeout=10), b.result(timeout=10))) == [False, True]
    assert sum(store is not None for store in (first.find("a"), first.find("b"))) == 1


def test_receipt_storage_preserves_existing_current_epoch_database(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    database = LocalDatabase(path)
    with database.transaction(write=True) as connection:
        connection.execute(
            "INSERT INTO locations (path, position, added_at) VALUES (?, ?, ?)",
            (str(tmp_path / "existing-location"), 0, "2026-11-25T00:00:00+00:00"),
        )
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DROP TABLE branch_advances")
        connection.commit()

    old_readback = BranchAdvanceStore(LocalDatabase(path, read_only=True))
    assert old_readback.find("no-receipt") is None
    opened = LocalDatabase(path)
    assert not opened.initialized_new_state
    with opened.transaction() as connection:
        assert [row[0] for row in connection.execute("SELECT path FROM locations")] == [
            str(tmp_path / "existing-location")
        ]
    preparation = _preparation(tmp_path)
    assert BranchAdvanceStore(opened).prepare(preparation).preparation == preparation
