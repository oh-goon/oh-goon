"""M163 deployment-owned native issuer custody: every defect gets a regression."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import (
    AUTHORITY_FIELDS, ISSUER_PROTOCOL, SIGNATURE_DOMAIN, IssuerEvidence, TrustedIssuerVerifier,
)
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes
from oh_goon.repository_tooling.enforcer_lace_signer_custody import (
    SIGNER_POLICY_SCHEMA, issue_with_deployment_signer,
)
from tests.unit.services.test_group_a_verified_ingress_authorities import setup

FIELD = "source_identity"


def _fixture(tmp_path):
    directory = tmp_path / "signers"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    entries, roots, keys = {}, {}, {}
    for i, field in enumerate(sorted(AUTHORITY_FIELDS)):
        key = Ed25519PrivateKey.generate()
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        name = f"key-{i}.seed"
        path = directory / name
        path.write_bytes(key.private_bytes(serialization.Encoding.Raw,
                                           serialization.PrivateFormat.Raw,
                                           serialization.NoEncryption()))
        path.chmod(0o600)
        issuer_id = f"native-{i}-generation-1"
        entries[field] = {"issuer_id": issuer_id, "key_id": f"generation-{i}-1",
                          "private_key_file": name, "public_key_hex": public.hex(),
                          "state": "active"}
        roots[field] = issuer_id, public
        keys[field] = key
    policy = {"schema": SIGNER_POLICY_SCHEMA, "issuers": entries}
    _write_policy(directory, policy)
    _db, _journal, _env, contract, _, _ = setup()
    return directory, roots, policy, keys, contract


def _write_policy(directory, policy):
    path = directory / "signers.json"
    path.write_bytes(canonical_bytes(policy))
    path.chmod(0o600)


def _owner(contract, *, action=None):
    def issue(issuer_id, key):
        payload = canonical_bytes({"protocol": ISSUER_PROTOCOL, "field": FIELD,
            "run_id": contract.fields["run_id"], "contract_identity": contract.identity,
            "identity": contract.fields[FIELD], "executor_generation": contract.fields["executor_generation"]})
        if action:
            action()
        return IssuerEvidence(FIELD, issuer_id, payload,
            key.sign(SIGNATURE_DOMAIN + FIELD.encode() + b"\x00" + payload).hex())
    return issue


def _issue(directory, roots, contract, native_issuer=None, field=FIELD):
    return issue_with_deployment_signer(key_directory=directory,
        trusted_public_roots=roots, field=field, contract=contract,
        native_issuer=native_issuer or _owner(contract))


def test_deployment_key_signs_exact_native_claim_for_public_only_verifier(tmp_path):
    directory, roots, _policy_data, _, contract = _fixture(tmp_path)
    evidence = _issue(directory, roots, contract)
    assert evidence.field == FIELD
    assert evidence.issuer_id == roots[FIELD][0]
    # Enforcer knows only public material; it cannot issue its own evidence.
    verifier = TrustedIssuerVerifier(roots)
    assert not hasattr(verifier, "sign")
    public = verifier._roots[FIELD][1]
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(public).verify(
        bytes.fromhex(evidence.signature_hex),
        SIGNATURE_DOMAIN + FIELD.encode() + b"\x00" + evidence.payload)


@pytest.mark.parametrize("attack", [
    "revoked", "untrusted_root", "wrong_seed", "missing_key", "weak_private_mode",
    "weak_dir_mode", "symlink_key", "hardlink_key", "fifo_key", "extra_field",
    "missing_field", "duplicate_key_file", "duplicate_issuer", "duplicate_public_root",
    "traversal", "symlink_directory", "relative_directory", "malformed_policy", "weak_policy_mode",
    "wrong_owner_claim", "wrong_field_claim", "wrong_payload_claim", "wrong_signature_claim",
    "policy_revoked_during_sign", "private_replaced_during_sign", "policy_replaced_during_sign",
    "directory_replaced_during_sign", "noncanonical_policy", "oversized_private_key",
])
def test_custody_rejects_unsafe_issuance(tmp_path, attack):
    directory, roots, policy, keys, contract = _fixture(tmp_path)
    leaf = directory / policy["issuers"][FIELD]["private_key_file"]
    owner = _owner(contract)
    if attack == "revoked":
        policy["issuers"][FIELD]["state"] = "revoked"
    elif attack == "untrusted_root":
        roots[FIELD] = ("a-different-issuer", roots[FIELD][1])
    elif attack == "wrong_seed":
        leaf.write_bytes(b"z" * 32)
    elif attack == "missing_key":
        leaf.unlink()
    elif attack == "weak_private_mode":
        leaf.chmod(0o644)
    elif attack == "weak_dir_mode":
        directory.chmod(0o755)
    elif attack == "symlink_key":
        leaf.unlink()
        leaf.symlink_to(directory / policy["issuers"][sorted(AUTHORITY_FIELDS)[0]]["private_key_file"])
    elif attack == "hardlink_key":
        (directory / "other-link").hardlink_to(leaf)
    elif attack == "fifo_key":
        leaf.unlink()
        os.mkfifo(leaf, 0o600)
    elif attack == "extra_field":
        policy["issuers"]["new_authority"] = policy["issuers"][FIELD].copy()
    elif attack == "missing_field":
        policy["issuers"].pop(FIELD)
    elif attack == "duplicate_key_file":
        other = next(f for f in sorted(AUTHORITY_FIELDS) if f != FIELD)
        policy["issuers"][other]["private_key_file"] = policy["issuers"][FIELD]["private_key_file"]
    elif attack == "duplicate_issuer":
        other = next(f for f in sorted(AUTHORITY_FIELDS) if f != FIELD)
        policy["issuers"][other]["issuer_id"] = policy["issuers"][FIELD]["issuer_id"]
        roots[other] = (roots[FIELD][0], roots[other][1])
    elif attack == "duplicate_public_root":
        other = next(f for f in sorted(AUTHORITY_FIELDS) if f != FIELD)
        policy["issuers"][other]["public_key_hex"] = policy["issuers"][FIELD]["public_key_hex"]
        roots[other] = (roots[other][0], roots[FIELD][1])
    elif attack == "traversal":
        policy["issuers"][FIELD]["private_key_file"] = "../outside.key"
    elif attack == "symlink_directory":
        link = tmp_path / "alias"
        link.symlink_to(directory, target_is_directory=True)
        directory = link
    elif attack == "relative_directory":
        directory = Path("relative/signers")
    elif attack == "malformed_policy":
        (directory / "signers.json").write_bytes(b"not JSON")
    elif attack == "weak_policy_mode":
        (directory / "signers.json").chmod(0o644)
    elif attack == "noncanonical_policy":
        (directory / "signers.json").write_text(json.dumps(policy, indent=4))
    elif attack == "oversized_private_key":
        leaf.write_bytes(b"q" * 33)
    elif attack == "wrong_owner_claim":
        def owner(issuer, key):
            claim = _owner(contract)(issuer, key)
            return IssuerEvidence(claim.field, "forged-owner", claim.payload, claim.signature_hex)
    elif attack == "wrong_field_claim":
        def owner(issuer, key):
            claim = _owner(contract)(issuer, key)
            return IssuerEvidence("native_tool_identity", claim.issuer_id, claim.payload, claim.signature_hex)
    elif attack == "wrong_payload_claim":
        def owner(issuer, key):
            claim = _owner(contract)(issuer, key)
            return IssuerEvidence(claim.field, claim.issuer_id, b"{}", claim.signature_hex)
    elif attack == "wrong_signature_claim":
        def owner(issuer, key):
            claim = _owner(contract)(issuer, key)
            return IssuerEvidence(claim.field, claim.issuer_id, claim.payload, "00" * 64)
    elif attack == "policy_revoked_during_sign":
        def action():
            policy["issuers"][FIELD]["state"] = "revoked"
            _write_policy(directory, policy)
        owner = _owner(contract, action=action)
    elif attack == "private_replaced_during_sign":
        def action():
            new = directory / "replaced"
            new.write_bytes(b"q" * 32)
            new.chmod(0o600)
            os.replace(new, leaf)
        owner = _owner(contract, action=action)
    elif attack == "policy_replaced_during_sign":
        def action():
            path = directory / "signers.json"
            replacement = directory / "replacement"
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            os.replace(replacement, path)
        owner = _owner(contract, action=action)
    elif attack == "directory_replaced_during_sign":
        def action():
            os.rename(directory, directory.with_name("old-signers"))
            directory.mkdir(mode=0o700)
            directory.chmod(0o700)
        owner = _owner(contract, action=action)
    if (attack in {"revoked", "extra_field", "missing_field", "duplicate_key_file",
                    "duplicate_issuer", "duplicate_public_root", "traversal"}):
        _write_policy(directory, policy)
    with pytest.raises(TrustProtocolError):
        _issue(directory, roots, contract, native_issuer=owner)


def test_explicit_rotation_requires_new_matching_public_trust_root(tmp_path):
    directory, roots, policy, keys, contract = _fixture(tmp_path)
    old = _issue(directory, roots, contract)
    new_private = Ed25519PrivateKey.generate()
    entry = policy["issuers"][FIELD]
    new_path = directory / "rotated.seed"
    new_path.write_bytes(new_private.private_bytes(serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw, serialization.NoEncryption()))
    new_path.chmod(0o600)
    entry.update(issuer_id="rotated-issuer-gen-2", key_id="rotated-generation-2",
                 private_key_file="rotated.seed",
                 public_key_hex=new_private.public_key().public_bytes(
                     serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
    _write_policy(directory, policy)
    with pytest.raises(TrustProtocolError):
        _issue(directory, roots, contract)
    roots[FIELD] = (entry["issuer_id"], bytes.fromhex(entry["public_key_hex"]))
    rotated = _issue(directory, roots, contract)
    assert rotated.issuer_id != old.issuer_id
    # A revoked generation cannot sign even if old bytes remain in custody.
    entry["state"] = "revoked"
    _write_policy(directory, policy)
    with pytest.raises(TrustProtocolError, match="revoked"):
        _issue(directory, roots, contract)
