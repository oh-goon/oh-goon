"""M154: deployment key custody may not silently become admission evidence."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import ISSUER_PROTOCOL, SIGNATURE_DOMAIN
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, admission_receipt
from oh_goon.runtime.enforcer_lace_issuer_keyring import (
    IssuerKeyReference,
    load_explicit_issuer_verifier,
)
from oh_goon.services.fleet_admission import FleetAdmissionPlanner
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    proof,
    roots,
    setup,
)


def secure_roots(tmp_path):
    directory = tmp_path / "trusted-issuers"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    keys = roots()
    refs = {}
    for i, field in enumerate(FIELDS):
        filename = f"owner-{i}.key"
        path = directory / filename
        path.write_bytes(keys[field][1])
        path.chmod(0o600)
        refs[field] = IssuerKeyReference(keys[field][0], filename)
    return directory, refs, keys


def test_explicit_file_keys_validate_independent_existing_proofs(tmp_path):
    directory, refs, keys = secure_roots(tmp_path)
    verifier = load_explicit_issuer_verifier(directory, refs)
    db, journal, env, contract, _, witnesses = setup()
    auth = authenticator()
    db.execute("INSERT INTO operational_lease VALUES ('run-one')")
    # This remains the non-live, opt-in tested boundary; key loading cannot issue proof.
    result = FleetAdmissionPlanner.publish_verified_ingress_admission(
        journal=journal,
        contract=contract,
        receipt=admission_receipt(auth, contract),
        authenticator=auth,
        configured=env,
        current=env,
        control_epoch=1,
        lease_identity="lease-verified",
        issuer_verifier=verifier,
        issuer_evidence=witnesses,
        lease_is_current=lambda run, epoch, lease: (run, epoch, lease)
        == ("run-one", 1, "lease-verified"),
        commit_lease_is_current=lambda conn, run, epoch, lease: conn.in_transaction
        and (run, epoch, lease) == ("run-one", 1, "lease-verified"),
        operational_admission=lambda conn: conn.execute(
            "UPDATE operational_lease SET run_id=run_id WHERE run_id='run-one'"
        ),
    )
    assert result.rowcount == 1
    assert journal.verify("run-one", auth=auth)[0]["kind"] == "RUN_ADMITTED"
    db.close()


@pytest.mark.parametrize(
    "attack",
    [
        "no_root",
        "relative_root",
        "world_directory",
        "wrong_owner_directory",
        "symlink_directory",
        "missing_file",
        "symlink_file",
        "world_file",
        "group_file",
        "directory_instead_of_file",
        "short_key",
        "oversized_key",
        "duplicate_file",
        "path_traversal",
        "absolute_leaf",
        "unknown_field",
        "missing_field",
        "duplicated_key",
        "duplicated_issuer",
        "fifo",
        "hardlink",
        "read_race",
    ],
)
def test_unsafe_issuer_roots_reject_before_proofs_or_admission(tmp_path, monkeypatch, attack):
    directory, refs, keys = secure_roots(tmp_path)
    field = FIELDS[0]
    path = directory / refs[field].filename
    target = directory
    if attack == "no_root":
        target = directory / "absent"
    elif attack == "relative_root":
        target = type(directory)("relative/keys")
    elif attack == "world_directory":
        directory.chmod(0o777)
    elif attack == "wrong_owner_directory":
        previous = os.fstat

        def fake_fstat(fd):
            info = previous(fd)
            if os.path.samefile(f"/proc/self/fd/{fd}", directory):
                values = list(info)
                values[4] = os.geteuid() + 1
                return os.stat_result(values)
            return info

        monkeypatch.setattr(os, "fstat", fake_fstat)
    elif attack == "symlink_directory":
        alias = tmp_path / "keys-link"
        alias.symlink_to(directory, target_is_directory=True)
        target = alias
    elif attack == "missing_file":
        path.unlink()
    elif attack == "symlink_file":
        path.unlink()
        path.symlink_to(directory / refs[FIELDS[1]].filename)
    elif attack == "world_file":
        path.chmod(0o644)
    elif attack == "group_file":
        path.chmod(0o640)
    elif attack == "directory_instead_of_file":
        path.unlink()
        path.mkdir(mode=0o700)
    elif attack == "short_key":
        path.write_bytes(b"short")
    elif attack == "oversized_key":
        path.write_bytes(b"x" * 33)
    elif attack == "duplicate_file":
        refs[FIELDS[1]] = replace(refs[FIELDS[1]], filename=refs[field].filename)
    elif attack == "path_traversal":
        refs[field] = replace(refs[field], filename="../secret")
    elif attack == "absolute_leaf":
        refs[field] = replace(refs[field], filename="/tmp/secret")
    elif attack == "unknown_field":
        refs["extra"] = IssuerKeyReference("extra", "extra.key")
    elif attack == "missing_field":
        refs.pop(field)
    elif attack == "duplicated_key":
        (directory / refs[FIELDS[1]].filename).write_bytes(keys[field][1])
    elif attack == "duplicated_issuer":
        refs[FIELDS[1]] = replace(refs[FIELDS[1]], issuer_id=refs[field].issuer_id)
    elif attack == "hardlink":
        (directory / "another-link").hardlink_to(path)
    elif attack == "read_race":
        original_read = os.read

        def racing_read(fd, n):
            result = original_read(fd, n)
            path.write_bytes(b"changed-within-native-read-time" * 2)
            return result

        monkeypatch.setattr(os, "read", racing_read)
    elif attack == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    with pytest.raises(TrustProtocolError):
        load_explicit_issuer_verifier(target, refs)


def test_root_file_swap_does_not_fallback_to_old_key(tmp_path):
    directory, refs, _ = secure_roots(tmp_path)
    old = load_explicit_issuer_verifier(directory, refs)
    _, _, _, contract, keys, _ = setup()
    evidence = tuple(proof(field, contract, keys) for field in FIELDS)
    old.verify(
        contract,
        evidence=evidence,
        control_epoch=1,
        lease_identity="lease",
        lease_is_current=lambda *_: True,
    )
    swapped = directory / refs[FIELDS[0]].filename
    swapped.write_bytes(b"r" * 32)
    new = load_explicit_issuer_verifier(directory, refs)
    with pytest.raises(TrustProtocolError, match="authentication failed"):
        new.verify(
            contract,
            evidence=evidence,
            control_epoch=1,
            lease_identity="lease",
            lease_is_current=lambda *_: True,
        )


@pytest.mark.parametrize("replacement", ["leaf-rename", "directory-rename"])
def test_root_names_must_remain_bound_through_entire_verifier_load(
    tmp_path,
    monkeypatch,
    replacement,
):
    directory, refs, keys = secure_roots(tmp_path)
    original_read = os.read
    raced = False
    reads = 0

    def swap_after_descriptor_read(fd, size):
        nonlocal raced, reads
        data = original_read(fd, size)
        reads += 1
        # First root descriptor has closed: an old descriptor-only check can
        # no longer notice replacement of its filesystem name.
        if reads == 2:
            raced = True
            if replacement == "leaf-rename":
                leaf = directory / refs[sorted(FIELDS)[0]].filename
                leaf.rename(directory / "retired-root")
                leaf.write_bytes(keys[sorted(FIELDS)[0]][1])  # same bytes, different inode
                leaf.chmod(0o600)
            else:
                directory.rename(tmp_path / "old-roots")
                directory.mkdir(mode=0o700)  # old opened descriptor still valid
                directory.chmod(0o700)
        return data

    monkeypatch.setattr(os, "read", swap_after_descriptor_read)
    with pytest.raises(TrustProtocolError, match="changed during observation"):
        load_explicit_issuer_verifier(directory, refs)
    assert raced


def test_public_verification_root_does_not_grant_signature_authority(tmp_path):
    directory, references, public_roots = secure_roots(tmp_path)
    verifier = load_explicit_issuer_verifier(directory, references)
    assert not hasattr(verifier, "issue")
    assert not hasattr(verifier, "sign")
    _, _, _, contract, _, evidence = setup()
    # Treating the public byte string as a private seed produces the wrong key;
    # the verifier must reject it. The actual owner-side private keys are absent.
    field = FIELDS[0]
    public_key = public_roots[field][1]
    fake = Ed25519PrivateKey.from_private_bytes(public_key)
    message = SIGNATURE_DOMAIN + field.encode("ascii") + b"\x00" + evidence[0].payload
    forged = replace(evidence[0], signature_hex=fake.sign(message).hex())
    assert ISSUER_PROTOCOL.endswith("/v2")
    with pytest.raises(TrustProtocolError, match="authentication failed"):
        verifier.verify(
            contract,
            evidence=(forged,) + evidence[1:],
            control_epoch=1,
            lease_identity="lease",
            lease_is_current=lambda *_: True,
        )
