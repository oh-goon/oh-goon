"""CO-R2c: actual SQLite acceptance cut, without substituting a Hashmarks identity.

The external source snapshot and physical-writer preflight are deliberately NOT
certified by these focused ledger tests. Their original owners have not changed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import oh_goon.runtime.execution_evidence_acceptance as acceptance_module
from oh_goon.core.errors import ExecutionEvidenceRejected
from oh_goon.core.models.workspace_lease import WorkspaceLease
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_evidence_acceptance import ExecutionEvidenceAcceptanceAuthority

NOW = datetime(2026, 10, 15, 8, 0, tzinfo=UTC)


def _acceptance_ledger(tmp_path: Path, monkeypatch, *, expiry_seconds: int = 60):
    database = LocalDatabase(tmp_path / "ledger.db", journal_mode="DELETE")
    workspace = SimpleNamespace(workspace_id="workspace-a")
    assignment = SimpleNamespace(
        attempt_id="attempt-a",
        assignment_id="assignment-a",
        assignment_fingerprint="sha256:assignment",
        workspace_id=workspace.workspace_id,
        input_checkpoint_id="checkpoint-a",
    )
    lease = WorkspaceLease(
        lease_id="lease-a",
        workspace_id=workspace.workspace_id,
        attempt_id=assignment.attempt_id,
        assignment_id=assignment.assignment_id,
        assignment_fingerprint=assignment.assignment_fingerprint,
        executor_id="executor-a",
        executor_generation=9,
        input_checkpoint_id=assignment.input_checkpoint_id,
        lease_generation=3,
        issued_at=NOW - timedelta(seconds=60),
        expires_at=NOW + timedelta(seconds=expiry_seconds),
    )
    materialization = SimpleNamespace(
        attempt_id=assignment.attempt_id,
        materialization_id="materialization-a",
        workspace_id=workspace.workspace_id,
        assignment_id=assignment.assignment_id,
        assignment_fingerprint=assignment.assignment_fingerprint,
        input_checkpoint_id=assignment.input_checkpoint_id,
        lease_id=lease.lease_id,
        lease_generation=lease.lease_generation,
        executor_id=lease.executor_id,
        executor_generation=lease.executor_generation,
        resolved_fingerprint="sha256:materialization",
        producer="hashmarks",
        producer_artifact_identity="sha256:producer",
        producer_contract_version="hashmarks.repository-work-selection.v1",
        producer_contract_identity="sha256:contract",
        producer_work_identity="sha256:work",
    )
    with database.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO workspaces(workspace_id,repository_id,base_revision,kind,created_at) VALUES (?,?,?,?,?)",
            (workspace.workspace_id, "repo", "base", "WORKTREE", NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO workspace_state(workspace_id,lifecycle_state,state_version,current_lease_id,current_checkpoint_id,updated_at) VALUES (?,?,?,?,?,?)",
            (
                workspace.workspace_id,
                "IN_USE",
                2,
                lease.lease_id,
                assignment.input_checkpoint_id,
                NOW.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO candidate_checkpoints(checkpoint_id,workspace_id,sha256,byte_count,entry_count,changed_paths_json) VALUES (?,?,?,?,?,?)",
            (assignment.input_checkpoint_id, workspace.workspace_id, "f" * 64, 0, 0, "[]"),
        )
        conn.execute(
            "INSERT INTO execution_assignments(assignment_id,attempt_id,workspace_id,input_checkpoint_id,assignment_fingerprint,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (
                assignment.assignment_id,
                assignment.attempt_id,
                workspace.workspace_id,
                assignment.input_checkpoint_id,
                assignment.assignment_fingerprint,
                "{}",
                NOW.isoformat(),
            ),
        )
        conn.execute(
            """INSERT INTO workspace_leases(lease_id,workspace_id,attempt_id,assignment_id,assignment_fingerprint,executor_id,
                    executor_generation,input_checkpoint_id,lease_generation,issued_at,expires_at,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'ACTIVE')""",
            (
                lease.lease_id,
                workspace.workspace_id,
                assignment.attempt_id,
                assignment.assignment_id,
                assignment.assignment_fingerprint,
                lease.executor_id,
                lease.executor_generation,
                assignment.input_checkpoint_id,
                lease.lease_generation,
                lease.issued_at.isoformat(),
                lease.expires_at.isoformat(),
            ),
        )
        conn.execute(
            """INSERT INTO execution_materializations(materialization_id,attempt_id,assignment_id,assignment_fingerprint,
                    workspace_id,input_checkpoint_id,lease_id,lease_generation,executor_id,executor_generation,resolved_fingerprint,
                    payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                materialization.materialization_id,
                materialization.attempt_id,
                materialization.assignment_id,
                materialization.assignment_fingerprint,
                materialization.workspace_id,
                materialization.input_checkpoint_id,
                materialization.lease_id,
                materialization.lease_generation,
                materialization.executor_id,
                materialization.executor_generation,
                materialization.resolved_fingerprint,
                '{"producer":"hashmarks","producer_artifact_identity":"sha256:producer",'
                '"producer_contract_version":"hashmarks.repository-work-selection.v1",'
                '"producer_contract_identity":"sha256:contract","producer_work_identity":"sha256:work"}',
            ),
        )
    # Never install a second runtime owner. In this SQL-focused test only,
    # the expensive original physical source/quiescence preflight is held stable;
    # accept() still executes original durable reads, validation and persistence.
    authority = object.__new__(ExecutionEvidenceAcceptanceAuthority)
    authority._database = database
    monkeypatch.setattr(authority, "_validate_live_authority", lambda *a, **kw: None)
    return database, workspace, assignment, lease, materialization, authority


def _accept(authority, workspace, assignment, lease, materialization, result="sha256:result"):
    return authority.accept(
        workspace,
        assignment,
        lease,
        materialization,
        output_checkpoint_id=assignment.input_checkpoint_id,
        result_identity=result,
        producer_work_identity="sha256:work",
    )


def _count(database):
    with database.transaction() as conn:
        return conn.execute("SELECT count(*) FROM execution_evidence_acceptances").fetchone()[0]


def test_acceptance_uses_one_write_cut_clock_for_expiry_and_receipt(tmp_path, monkeypatch):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    instant = NOW + timedelta(seconds=59)
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: instant)
    accepted = _accept(authority, w, a, lease, m)
    assert accepted.accepted_at == instant
    assert authority.acceptance(accepted.acceptance_id) == accepted
    assert _count(db) == 1


@pytest.mark.parametrize("offset", [60, 61])
def test_expiry_at_or_before_original_write_cut_rejects_with_no_receipt(
    tmp_path, monkeypatch, offset
):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: NOW + timedelta(seconds=offset))
    with pytest.raises(ExecutionEvidenceRejected, match="expired before evidence acceptance"):
        _accept(authority, w, a, lease, m)
    assert _count(db) == 0


def test_time_advances_between_preflight_and_durable_write_cut(tmp_path, monkeypatch):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    clock = [NOW + timedelta(seconds=59)]

    def preflight(*args, **kwargs):
        assert clock[0] < lease.expires_at
        clock[0] = NOW + timedelta(seconds=61)

    monkeypatch.setattr(authority, "_validate_live_authority", preflight)
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: clock[0])
    with pytest.raises(ExecutionEvidenceRejected, match="expired"):
        _accept(authority, w, a, lease, m)
    assert _count(db) == 0


def test_durable_expiration_shorten_after_preflight_cannot_be_extended_by_snapshot(
    tmp_path, monkeypatch
):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)

    def preflight(*args, **kwargs):
        with db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE workspace_leases SET expires_at=? WHERE lease_id=?",
                ((NOW + timedelta(seconds=10)).isoformat(), lease.lease_id),
            )

    monkeypatch.setattr(authority, "_validate_live_authority", preflight)
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: NOW + timedelta(seconds=20))
    with pytest.raises(ExecutionEvidenceRejected, match="timing changed"):
        _accept(authority, w, a, lease, m)
    assert _count(db) == 0


@pytest.mark.parametrize(
    "column, new_value", [("lease_generation", 4), ("executor_generation", 10)]
)
def test_durable_lease_generation_changes_between_preflight_and_cut(
    tmp_path, monkeypatch, column, new_value
):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    with db.transaction(write=True) as conn:
        conn.execute(
            f"UPDATE workspace_leases SET {column}=? WHERE lease_id=?", (new_value, lease.lease_id)
        )
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: NOW)
    with pytest.raises(ExecutionEvidenceRejected, match="Lease authority changed"):
        _accept(authority, w, a, lease, m)
    assert _count(db) == 0


def test_lost_ack_retry_cannot_create_second_acceptance(tmp_path, monkeypatch):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: NOW)
    _accept(authority, w, a, lease, m)  # committed, caller loses acknowledgment
    reopened = LocalDatabase(tmp_path / "ledger.db", journal_mode="DELETE")
    authority = object.__new__(ExecutionEvidenceAcceptanceAuthority)
    authority._database = reopened
    monkeypatch.setattr(authority, "_validate_live_authority", lambda *args, **kwargs: None)
    with pytest.raises(ExecutionEvidenceRejected, match="already accepted"):
        _accept(authority, w, a, lease, m, "sha256:retry")
    assert _count(db) == 1


def test_precommit_failure_rolls_back_and_retry_commits_once(tmp_path, monkeypatch):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: NOW)
    original = authority._persist_acceptance

    def fail_before_commit(connection, value):
        original(connection, value)
        raise RuntimeError("injected failure after INSERT before commit")

    monkeypatch.setattr(authority, "_persist_acceptance", fail_before_commit)
    with pytest.raises(RuntimeError, match="injected failure"):
        _accept(authority, w, a, lease, m)
    assert _count(db) == 0
    monkeypatch.setattr(authority, "_persist_acceptance", original)
    _accept(authority, w, a, lease, m)
    assert _count(db) == 1


def test_bad_persisted_expiry_fails_closed(tmp_path, monkeypatch):
    db, w, a, lease, m, authority = _acceptance_ledger(tmp_path, monkeypatch)
    with db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE workspace_leases SET expires_at=? WHERE lease_id=?", ("invalid", lease.lease_id)
        )
    monkeypatch.setattr(acceptance_module, "utc_now", lambda: NOW)
    with pytest.raises(ExecutionEvidenceRejected, match="timing is invalid"):
        _accept(authority, w, a, lease, m)
    assert _count(db) == 0
