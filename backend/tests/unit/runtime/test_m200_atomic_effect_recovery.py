"""M200: native recovery may not release its SQLite lease cut before settlement."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from oh_goon.engine.git import GitService
from tests.unit.runtime.test_group_a_recovery_ingress_authority import _stack


def test_native_observation_and_journal_settlement_share_one_write_cut(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database, attempts, journal, _execution, _git, attempt, recovery = _stack(tmp_path)
    original_transaction = database.transaction
    original_fingerprint = GitService.fingerprint
    original_settle = recovery._settlement.apply_reconciliation_in
    active: list[tuple[bool, object]] = []
    observed: list[object] = []
    settled: list[object] = []

    @contextmanager
    def observe_cut(*, write: bool = False, track_revision: bool = True):
        with original_transaction(write=write, track_revision=track_revision) as connection:
            active.append((write, connection))
            try:
                yield connection
            finally:
                active.pop()

    def native_observation(self: GitService, repository: Path):
        assert active and active[-1][0], "native observation must own a write reservation"
        observed.append(active[-1][1])
        return original_fingerprint(self, repository)

    def settlement(connection, current, evidence):
        assert active and active[-1] == (True, connection)
        settled.append(connection)
        return original_settle(connection, current, evidence)

    monkeypatch.setattr(database, "transaction", observe_cut)
    monkeypatch.setattr(GitService, "fingerprint", native_observation)
    monkeypatch.setattr(recovery._settlement, "apply_reconciliation_in", settlement)

    evidence = recovery.reconcile(attempt.operation_id)
    assert evidence.reconciliation.retry_safe
    assert len(observed) == len(settled) == 1
    assert observed[0] is settled[0]
    assert journal.read_view().latest_effect_reconciliation(attempt.operation_id) == evidence
