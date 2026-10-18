"""One SQLite DB owner: the live original Fleet cut cannot be spoofed by BEGIN."""
from __future__ import annotations

import pytest

from tests.fixtures.db1b2_legacy_sqlite import CausalRunJournal
from tests.fixtures.db1b2_legacy_sqlite import SignerTrustEpochAuthority
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import (
    facts, prepared, publish,
)


def test_raw_begin_is_not_an_original_fleet_reservation(tmp_path, monkeypatch):
    args, connection, _ = prepared(tmp_path, monkeypatch)
    try:
        connection.execute("BEGIN IMMEDIATE")
        args["original_reservation_connection"] = connection
        with pytest.raises(RuntimeError, match="LocalDatabase atomic write cut required"):
            publish(args)
        connection.rollback()
        assert facts(args, connection) == (None, 0, 0)
    finally:
        connection.close()


def test_original_localdatabase_cut_commits_both_journals_once(tmp_path, monkeypatch):
    args, outside, _ = prepared(tmp_path, monkeypatch)
    owner = args["journal"]._database
    try:
        with owner.transaction(write=True) as connection:
            args["causal_journal"] = CausalRunJournal(connection)
            args["signer_authority"] = SignerTrustEpochAuthority(connection)
            args["original_reservation_connection"] = connection
            assert publish(args) is not None
        assert facts(args, outside)[1:] == (1, 2)
        assert owner.durable_revision() > 0
    finally:
        outside.close()


def test_original_database_owner_rolls_back_both_journals_on_post_admission_fault(tmp_path, monkeypatch):
    args, outside, _ = prepared(tmp_path, monkeypatch)
    owner = args["journal"]._database
    from oh_goon.runtime.enforcer_lace_journal import CausalRunJournal as RuntimeCausalRunJournal
    original = RuntimeCausalRunJournal._append_locked

    def append_then_crash(self, run_id, kind, payload):
        result = original(self, run_id, kind, payload)
        if kind == "RUN_ADMITTED":
            raise RuntimeError("post causal admission crash")
        return result

    try:
        with monkeypatch.context() as context:
            context.setattr(RuntimeCausalRunJournal, "_append_locked", append_then_crash)
            with pytest.raises(RuntimeError, match="post causal admission crash"):
                with owner.transaction(write=True) as connection:
                    args["causal_journal"] = CausalRunJournal(connection)
                    args["signer_authority"] = SignerTrustEpochAuthority(connection)
                    args["original_reservation_connection"] = connection
                    publish(args)
        assert facts(args, outside) == (None, 0, 0)
    finally:
        outside.close()
