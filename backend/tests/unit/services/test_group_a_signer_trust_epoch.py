"""M165 regressions: signer revocation belongs to the SAME admission transaction."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization

from oh_goon.core.enforcer_lace_trust import TrustProtocolError, admission_receipt, canonical_bytes
from oh_goon.repository_tooling.enforcer_lace_signer_custody import (
    SIGNER_POLICY_SCHEMA,
    deployment_signer_policy_snapshot,
)
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.fixtures.db1b2_legacy_sqlite import (
    SignerTrustEpochAuthority,
    SignerTrustGrant,
)
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    assert_no_effect,
    setup,
)


def fixture(tmp_path):
    db, journal, env, contract, roots, witnesses = setup()
    directory = tmp_path / "deployment-signers"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    policy = {"schema": SIGNER_POLICY_SCHEMA, "issuers": {}}
    for i, field in enumerate(FIELDS):
        key = _test_private_key(field)
        public = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        assert roots[field][1] == public
        key_file = directory / f"issuer-{i}.seed"
        key_file.write_bytes(
            key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        )
        key_file.chmod(0o600)
        policy["issuers"][field] = {
            "issuer_id": roots[field][0],
            "key_id": f"key-generation-{i}",
            "private_key_file": key_file.name,
            "public_key_hex": public.hex(),
            "state": "active",
        }
    write_policy(directory, policy)
    authority = SignerTrustEpochAuthority(db)
    authority.initialize_schema()
    snapshot = deployment_signer_policy_snapshot(
        key_directory=directory, trusted_public_roots=roots
    )
    grant = authority.enroll(policy_snapshot=snapshot, roots=roots)
    return db, journal, env, contract, roots, witnesses, directory, policy, authority, grant


def write_policy(directory, policy):
    path = directory / "signers.json"
    path.write_bytes(canonical_bytes(policy))
    path.chmod(0o600)


def publish(fx, *, operational_admission=None, override=None):
    db, journal, env, contract, roots, witnesses, directory, _, authority, grant = fx
    auth = authenticator()
    args = dict(
        journal=journal,
        contract=contract,
        receipt=admission_receipt(auth, contract),
        authenticator=auth,
        configured=env,
        current=env,
        control_epoch=7,
        lease_identity="lease-exact",
        signer_authority=authority,
        signer_grant=grant,
        key_directory=directory,
        trusted_public_roots=roots,
        issuer_evidence=witnesses,
        lease_is_current=lambda run, epoch, lease: (run, epoch, lease)
        == ("run-one", 7, "lease-exact"),
        commit_lease_is_current=lambda connection, run, epoch, lease: (
            connection.in_transaction
            and (run, epoch, lease) == ("run-one", 7, "lease-exact")
            and connection.execute(
                "SELECT run_id FROM operational_lease WHERE run_id=?", (run,)
            ).fetchone()
            == (run,)
        ),
        operational_admission=operational_admission
        or (lambda c: c.execute("INSERT INTO operational_lease VALUES ('run-one')")),
    )
    if override:
        args.update(override)
    return FleetAdmissionPlanner.publish_with_fenced_signer_trust(**args)


def test_initial_epoch_commits_both_journals_once(tmp_path):
    fx = fixture(tmp_path)
    db = fx[0]
    assert publish(fx).rowcount == 1
    assert db.execute("SELECT COUNT(*) FROM lace_run_events").fetchone()[0] == 1
    assert db.execute("SELECT trust_epoch, state FROM lace_signer_trust_epoch").fetchone() == (
        1,
        "ACTIVE",
    )
    db.close()


@pytest.mark.parametrize(
    "attack",
    [
        "missing-grant",
        "fake-grant",
        "revoked",
        "unprovisioned",
        "rotated",
        "different-connection",
        "missing-policy",
        "different-roots",
    ],
)
def test_invalid_epoch_cannot_publish_any_work(tmp_path, attack):
    fx = fixture(tmp_path)
    db, journal, env, contract, roots, witnesses, directory, policy, authority, grant = fx
    overrides = {}
    if attack == "missing-grant":
        overrides["signer_grant"] = None
    elif attack == "fake-grant":
        overrides["signer_grant"] = replace(grant, epoch=grant.epoch + 1)
    elif attack == "revoked":
        authority.revoke(expected=grant)
    elif attack == "unprovisioned":
        db.execute("DELETE FROM lace_signer_trust_epoch")
    elif attack == "rotated":
        changed = dict(policy)
        changed["issuers"] = {f: dict(config) for f, config in policy["issuers"].items()}
        changed["issuers"][FIELDS[0]]["key_id"] = "key-generation-next"
        write_policy(directory, changed)
        # A rotated trust epoch pins both a changed policy AND a changed public root.
        changed_roots = dict(roots)
        changed_roots[FIELDS[0]] = ("new-owner", bytes.fromhex("1a" * 32))
        authority.rotate(
            expected=grant,
            policy_snapshot=deployment_signer_policy_snapshot(
                key_directory=directory, trusted_public_roots=roots
            ),
            roots=changed_roots,
        )
    elif attack == "different-connection":
        other = sqlite3.connect(":memory:", isolation_level=None)
        overrides["signer_authority"] = SignerTrustEpochAuthority(other)
    elif attack == "missing-policy":
        (directory / "signers.json").unlink()
    elif attack == "different-roots":
        overrides["trusted_public_roots"] = {**roots, FIELDS[0]: ("wrong", roots[FIELDS[0]][1])}
    try:
        with pytest.raises((TrustProtocolError, OSError)):
            publish(fx, override=overrides)
        assert_no_effect(db)
    finally:
        db.close()


@pytest.mark.parametrize(
    "attack",
    ["revoke-after-writes", "rotate-after-writes", "policy-replace-after-writes", "lease-revoke"],
)
def test_commit_fence_rolls_back_provisional_operational_and_causal_records(tmp_path, attack):
    fx = fixture(tmp_path)
    db, _, _, _, roots, _, directory, policy, authority, grant = fx

    def operation(connection):
        result = connection.execute("INSERT INTO operational_lease VALUES ('run-one')")
        if attack == "revoke-after-writes":
            connection.execute(
                "UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1,state='REVOKED'"
            )
        elif attack == "rotate-after-writes":
            connection.execute("UPDATE lace_signer_trust_epoch SET trust_epoch=trust_epoch+1")
        elif attack == "policy-replace-after-writes":
            changed = {**policy, "issuers": {f: dict(c) for f, c in policy["issuers"].items()}}
            changed["issuers"][FIELDS[0]]["state"] = "revoked"
            write_policy(directory, changed)
        else:
            connection.execute("DELETE FROM operational_lease")
        return result

    with pytest.raises(TrustProtocolError):
        publish(fx, operational_admission=operation)
    assert_no_effect(db)
    assert db.execute("SELECT trust_epoch, state FROM lace_signer_trust_epoch").fetchone() == (
        1,
        "ACTIVE",
    )
    db.close()


def test_rotation_rejects_old_grant_and_requires_new_roots_and_policy(tmp_path):
    fx = fixture(tmp_path)
    db, _, _, _, roots, _, directory, policy, authority, old = fx
    with pytest.raises(TrustProtocolError, match="new explicit"):
        authority.rotate(
            expected=old,
            policy_snapshot=deployment_signer_policy_snapshot(
                key_directory=directory, trusted_public_roots=roots
            ),
            roots=roots,
        )
    new_policy = {**policy, "issuers": {f: dict(c) for f, c in policy["issuers"].items()}}
    new_policy["issuers"][FIELDS[0]]["key_id"] = "rotated-key"
    write_policy(directory, new_policy)
    new_roots = {**roots, FIELDS[0]: ("rotated-owner", bytes.fromhex("2b" * 32))}
    new_snapshot = deployment_signer_policy_snapshot(
        key_directory=directory, trusted_public_roots=roots
    )
    new = authority.rotate(expected=old, policy_snapshot=new_snapshot, roots=new_roots)
    assert new.epoch == 2
    assert authority.active() == new
    with pytest.raises(TrustProtocolError, match="stale"):
        authority.revoke(expected=old)
    authority.revoke(expected=new)
    with pytest.raises(TrustProtocolError, match="no active"):
        authority.active()
    db.close()


def test_revoked_epoch_cannot_be_reactivated_by_constructed_current_grant(tmp_path):
    """A1j: a freshly constructed grant is not permission to undo revocation.

    This previously succeeded because rotate() matched identity and epoch but
    forgot to check the durable row's REVOKED state. A non-revoked positive
    rotation is covered by test_rotation_rejects_old_grant above.
    """
    fx = fixture(tmp_path)
    db, _, _, _, roots, _, directory, policy, authority, original = fx
    authority.revoke(expected=original)
    revoked_row = db.execute(
        "SELECT trust_epoch, policy_identity, root_identity, state FROM lace_signer_trust_epoch"
    ).fetchone()
    assert revoked_row == (2, original.policy_identity, original.root_identity, "REVOKED")
    # Even if an administrator erroneously constructs the new epoch number,
    # the durable state transition remains terminal and cannot grant Run work.
    forged_current = replace(original, epoch=revoked_row[0])
    new_policy = {**policy, "issuers": {f: dict(c) for f, c in policy["issuers"].items()}}
    new_policy["issuers"][FIELDS[0]]["key_id"] = "attempted-resurrection"
    write_policy(directory, new_policy)
    snapshot = deployment_signer_policy_snapshot(
        key_directory=directory, trusted_public_roots=roots
    )
    new_roots = {**roots, FIELDS[0]: ("new-owner", bytes.fromhex("c3" * 32))}
    with pytest.raises(TrustProtocolError, match="revoked"):
        authority.rotate(expected=forged_current, policy_snapshot=snapshot, roots=new_roots)
    assert (
        db.execute(
            "SELECT trust_epoch, policy_identity, root_identity, state FROM lace_signer_trust_epoch"
        ).fetchone()
        == revoked_row
    )
    with pytest.raises(TrustProtocolError, match="no active"):
        authority.active()
    with pytest.raises(TrustProtocolError):
        publish(fx, override={"signer_grant": forged_current})
    assert_no_effect(db)
    db.close()


@pytest.mark.parametrize("bad_grant", [None, object(), "1", (1, "policy", "root")])
def test_epoch_admin_mutations_require_captured_grant_before_sql(tmp_path, bad_grant):
    fx = fixture(tmp_path)
    db, _, _, _, roots, _, directory, _, authority, original = fx
    snapshot = deployment_signer_policy_snapshot(
        key_directory=directory, trusted_public_roots=roots
    )
    with pytest.raises(TrustProtocolError, match="captured signer trust epoch"):
        authority.revoke(expected=bad_grant)
    with pytest.raises(TrustProtocolError, match="captured signer trust epoch"):
        authority.rotate(expected=bad_grant, policy_snapshot=snapshot, roots=roots)
    assert authority.active() == original
    db.close()


def test_restart_and_concurrent_revocation_use_original_database(tmp_path):
    path = tmp_path / "same-authority.sqlite"
    first = sqlite3.connect(path, isolation_level=None, timeout=0.1)
    authority = SignerTrustEpochAuthority(first)
    authority.initialize_schema()
    # Exercise persistence without inventing keys for a second issuer protocol.
    first.execute("INSERT INTO lace_signer_trust_epoch VALUES (1, 4, 'p', 'r', 'ACTIVE')")
    other = sqlite3.connect(path, isolation_level=None, timeout=0.1)
    assert SignerTrustEpochAuthority(other).active() == SignerTrustGrant(4, "p", "r")
    first.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        SignerTrustEpochAuthority(other).revoke(expected=SignerTrustGrant(4, "p", "r"))
    first.execute("ROLLBACK")
    SignerTrustEpochAuthority(other).revoke(expected=SignerTrustGrant(4, "p", "r"))
    assert first.execute("SELECT trust_epoch, state FROM lace_signer_trust_epoch").fetchone() == (
        5,
        "REVOKED",
    )
    other.close()
    first.close()


def test_revoked_epoch_remains_terminal_after_reopening_sqlite(tmp_path):
    """A1j: an alternate SQLite handle cannot resurrect persistent revocation."""
    path = tmp_path / "admission.sqlite"
    first = sqlite3.connect(path, isolation_level=None)
    initial = SignerTrustGrant(9, "old-policy", "old-roots")
    authority = SignerTrustEpochAuthority(first)
    authority.initialize_schema()
    first.execute(
        "INSERT INTO lace_signer_trust_epoch VALUES (1, 9, 'old-policy', 'old-roots', 'ACTIVE')"
    )
    authority.revoke(expected=initial)
    first.close()

    second = sqlite3.connect(path, isolation_level=None)
    reopened = SignerTrustEpochAuthority(second)
    # This fixture tests the transactional state transition, not authenticity
    # of a native policy or independent public keys. That has separate owners.
    candidate_snapshot = ((1, 2), (3, 4), b"new-policy-observation")
    import unittest.mock

    with (
        unittest.mock.patch(
            "oh_goon.runtime.enforcer_lace_signer_trust_epoch.identity_for_policy",
            return_value="new-policy",
        ),
        unittest.mock.patch(
            "oh_goon.runtime.enforcer_lace_signer_trust_epoch.identity_for_roots",
            return_value="new-roots",
        ),
    ):
        with pytest.raises(TrustProtocolError, match="revoked"):
            reopened.rotate(
                expected=replace(initial, epoch=10), policy_snapshot=candidate_snapshot, roots={}
            )
    assert second.execute(
        "SELECT trust_epoch, policy_identity, root_identity, state FROM lace_signer_trust_epoch"
    ).fetchone() == (10, "old-policy", "old-roots", "REVOKED")
    second.close()


def test_untrusted_schema_or_nested_mutation_fails_early(tmp_path):
    db = sqlite3.connect(":memory:", isolation_level=None)
    authority = SignerTrustEpochAuthority(db)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        authority.active()
    authority.initialize_schema()
    db.execute("BEGIN IMMEDIATE")
    with pytest.raises(TrustProtocolError, match="cannot nest"):
        authority.revoke(expected=SignerTrustGrant(1, "a", "b"))
    db.execute("ROLLBACK")
    db.close()
