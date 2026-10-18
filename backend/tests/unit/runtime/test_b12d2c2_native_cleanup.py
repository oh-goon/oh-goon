"""B12D2C2: an executable callback needs new original-session authority."""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from oh_goon.runtime.enforcer_lace_native_cleanup import NativeCleanupResult
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.unit.runtime.test_b12d2c1_cleanup_preparation import seeded
from tests.unit.core.test_enforcer_lace_trust import authenticator


def latest(journal):
    return journal.verify("run-one", auth=authenticator())


def test_native_action_has_durable_distinct_start_and_no_teardown_claim(tmp_path):
    conn, journal, owner, subject = seeded(tmp_path)
    ticket = owner.prepare_cleanup(subject)
    calls = []
    def action():
        calls.append("native")
        # The original Fleet transaction is held across the actual native effect.
        assert conn.in_transaction
        return NativeCleanupResult(("CGROUP",), "CLEANUP_ACTIONS_ATTEMPTED")
    result = owner.execute_native_cleanup(ticket, action)
    assert result.result == "CLEANUP_ACTIONS_ATTEMPTED"
    assert calls == ["native"]
    events = latest(journal)
    assert [e["kind"] for e in events[-2:]] == [
        "RESOURCE_CLEANUP_NATIVE_STARTED", "RESOURCE_CLEANUP_NATIVE_RECORDED"]
    assert events[-1]["payload"]["teardown_proven"] is False
    assert events[-1]["payload"]["native_absence_proven"] is False
    with pytest.raises(CausalJournalError):
        owner.execute_native_cleanup(ticket, action)
    assert calls == ["native"]
    conn.close()
    reopened = sqlite3.connect(tmp_path / "original.sqlite", isolation_level=None)
    assert CausalRunJournal(reopened).verify("run-one", auth=authenticator())[-1]["kind"] == "RESOURCE_CLEANUP_NATIVE_RECORDED"
    reopened.close()


@pytest.mark.parametrize("attack", ["stale-epoch", "wrong-lease", "cancelled", "forged-ticket", "blocked", "validation"])
def test_native_cleanup_denies_attacks_before_native_effect(tmp_path, attack):
    conn, journal, owner, subject = seeded(tmp_path)
    ticket = owner.prepare_cleanup(subject)
    if attack == "stale-epoch":
        journal.transfer_control("run-one", epoch=1, lease_identity="lease-one", next_epoch=2, next_lease_identity="next")
    if attack == "wrong-lease":
        owner.session.token = "wrong"
    if attack == "cancelled":
        owner.session.cancelled = True
    if attack == "forged-ticket":
        ticket = replace(ticket, inventory_identity="sha256:" + "0" * 64)
    if attack == "blocked":
        owner.record_cleanup_blocker(ticket, failure_code="NATIVE_UNAVAILABLE")
    if attack == "validation":
        owner = replace(owner, validate_authority_in=lambda _: (_ for _ in ()).throw(CausalJournalError("invalid")))
    before = len(latest(journal))
    called = []
    with pytest.raises(CausalJournalError):
        owner.execute_native_cleanup(ticket, lambda: called.append(1))
    assert called == []
    assert len(latest(journal)) == before
    conn.close()


def test_failed_native_command_commits_bounded_failure_and_does_not_replay(tmp_path):
    conn, journal, owner, subject = seeded(tmp_path)
    ticket = owner.prepare_cleanup(subject)
    calls = []
    def fail():
        calls.append("attempt")
        raise RuntimeError("sensitive-native-output")
    result = owner.execute_native_cleanup(ticket, fail)
    assert result == NativeCleanupResult((), "NATIVE_FAILED")
    assert "sensitive-native-output" not in str(latest(journal)[-1])
    assert latest(journal)[-1]["payload"]["result"] == "NATIVE_FAILED"
    with pytest.raises(CausalJournalError):
        owner.execute_native_cleanup(ticket, fail)
    assert calls == ["attempt"]
    conn.close()


def test_interrupted_native_operation_keeps_started_and_prevents_replay(tmp_path):
    conn, journal, owner, subject = seeded(tmp_path)
    ticket = owner.prepare_cleanup(subject)
    with pytest.raises(KeyboardInterrupt):
        owner.execute_native_cleanup(ticket, lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert latest(journal)[-1]["kind"] == "RESOURCE_CLEANUP_NATIVE_STARTED"
    with pytest.raises(CausalJournalError):
        owner.execute_native_cleanup(ticket, lambda: None)
    conn.close()


def test_partial_native_failure_keeps_bounded_effect_label_without_stderr(tmp_path):
    from oh_goon.core.ports.local_lace_cleanup import NativeCleanupFailure
    conn, journal, owner, item = seeded(tmp_path)
    ticket = owner.prepare_cleanup(item)
    def partial():
        raise NativeCleanupFailure(("NETWORK",)) from RuntimeError("private host error")
    result = owner.execute_native_cleanup(ticket, partial)
    assert result == NativeCleanupResult(("NETWORK",), "NATIVE_FAILED")
    assert latest(journal)[-1]["payload"]["attempted"] == ["NETWORK"]
    assert "private host error" not in str(latest(journal)[-1])
    assert latest(journal)[-1]["payload"]["teardown_proven"] is False
    conn.close()
