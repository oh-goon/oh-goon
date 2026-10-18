"""M165 explicit pre-issuance epoch capture and transport across the native owner suite."""
from __future__ import annotations

import sqlite3

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.repository_tooling import enforcer_lace_native_issuer_suite as suite
from oh_goon.repository_tooling.enforcer_lace_signer_custody import deployment_signer_policy_snapshot
from tests.fixtures.db1b2_legacy_sqlite import SignerTrustEpochAuthority
from tests.unit.services.test_group_a_fixed_native_issuer_suite import fixture, stub_native_owners


def prep(tmp_path, monkeypatch):
    journal, contract, path_authority, directory, roots, policy = fixture(tmp_path, monkeypatch)
    stub_native_owners(monkeypatch, contract)
    db = sqlite3.connect(":memory:", isolation_level=None)
    epoch_owner = SignerTrustEpochAuthority(db)
    epoch_owner.initialize_schema()
    grant = epoch_owner.enroll(policy_snapshot=deployment_signer_policy_snapshot(
        key_directory=directory, trusted_public_roots=roots), roots=roots)
    return journal, contract, path_authority, directory, roots, policy, db, epoch_owner, grant


def issue(fx):
    journal, contract, path_authority, directory, roots, _, _, epoch_owner, _ = fx
    return suite.issue_fixed_native_evidence_with_signer_grant(
        journal=journal, contract=contract, path_authority=path_authority,
        key_directory=directory, trusted_public_roots=roots, signer_authority=epoch_owner)


def test_epoch_captured_before_native_issuance(tmp_path, monkeypatch):
    fx = prep(tmp_path, monkeypatch)
    grant, evidence = issue(fx)
    assert grant == fx[-1]
    assert len(evidence) == 6
    fx[6].close()


@pytest.mark.parametrize("stage", ["before", "during", "after"])
def test_revocation_before_during_after_issuance_never_substitutes_a_fresh_epoch(tmp_path, monkeypatch, stage):
    fx = prep(tmp_path, monkeypatch)
    epoch_owner, original_grant = fx[-2:]
    if stage == "before":
        epoch_owner.revoke(expected=original_grant)
        with pytest.raises(TrustProtocolError):
            issue(fx)
    elif stage == "during":
        original = suite.issue_fixed_native_admission_evidence
        def revoke_midway(**kwargs):
            result = original(**kwargs)
            epoch_owner.revoke(expected=original_grant)
            return result
        monkeypatch.setattr(suite, "issue_fixed_native_admission_evidence", revoke_midway)
        with pytest.raises(TrustProtocolError):
            issue(fx)
    else:
        grant, _ = issue(fx)
        epoch_owner.revoke(expected=original_grant)
        assert grant == original_grant
        with pytest.raises(TrustProtocolError):
            epoch_owner.active()
    fx[6].close()


def test_different_signer_root_and_unprovisioned_epoch_fail_before_native_issuer(tmp_path, monkeypatch):
    fx = prep(tmp_path, monkeypatch)
    journal, contract, path_authority, directory, roots, _, db, epoch_owner, grant = fx
    db.execute("DELETE FROM lace_signer_trust_epoch")
    with pytest.raises(TrustProtocolError, match="no active"):
        issue(fx)
    db.close()
