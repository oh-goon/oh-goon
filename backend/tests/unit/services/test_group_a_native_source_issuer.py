"""M155: actual canonical source owner must measure before signing anything."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from oh_goon.core.enforcer_lace_issuer_evidence import TrustedIssuerVerifier
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.repository_tooling import enforcer_lace_source_issuer as source_issuer
from oh_goon.repository_tooling.release.package_slim import canonical_source_identity
from tests.unit.services.test_group_a_verified_ingress_authorities import (
    FIELDS,
    _test_private_key,
    proof,
    roots,
    setup,
)

ROOT = Path(__file__).resolve().parents[4]


def real_source_contract():
    _db, _journal, _env, original, keys, _witnesses = setup()
    # The owner observes the current canonical source, never the mock field.
    values = original.fields
    values["source_identity"] = canonical_source_identity(ROOT)
    return AdmittedRunContract.freeze(values), keys


def issue(contract, **overrides):
    opts = dict(
        root=ROOT,
        contract=contract,
        issuer_id=roots()["source_identity"][0],
        signing_key=_test_private_key("source_identity"),
    )
    opts.update(overrides)
    return source_issuer.issue_observed_source_identity(**opts)


def test_native_source_issuer_matches_actual_owner_and_independent_verifier():
    contract, keys = real_source_contract()
    claim = issue(contract)
    evidence = tuple(claim if f == "source_identity" else proof(f, contract, keys) for f in FIELDS)
    TrustedIssuerVerifier(keys).verify(
        contract,
        evidence=evidence,
        control_epoch=1,
        lease_identity="lease",
        lease_is_current=lambda *_: True,
    )
    assert json.loads(claim.payload)["identity"] == canonical_source_identity(ROOT)
    assert claim.signature_hex != ""


def test_cannot_sign_arbitrary_unobserved_claim():
    _db, _journal, _env, contract, _keys, _evidence = setup()
    with pytest.raises(TrustProtocolError, match="differs"):
        issue(contract)


def test_source_issuer_rejects_missing_key_and_ambiguous_root(tmp_path):
    contract, _ = real_source_contract()
    with pytest.raises(TrustProtocolError):
        issue(contract, signing_key=None)
    with pytest.raises(TrustProtocolError):
        issue(contract, root=Path("."))
    with pytest.raises(TrustProtocolError):
        issue(contract, root=tmp_path / "absent")
    alias = tmp_path / "symlink"
    alias.symlink_to(ROOT, target_is_directory=True)
    with pytest.raises(TrustProtocolError):
        issue(contract, root=alias)
    with pytest.raises(TrustProtocolError):
        issue(contract, issuer_id="wrong/identity")


def test_changed_source_before_issuance_rejects(monkeypatch):
    contract, _ = real_source_contract()
    monkeypatch.setattr(source_issuer, "canonical_source_identity", lambda _: "sha256:" + "0" * 64)
    with pytest.raises(TrustProtocolError, match="differs"):
        issue(contract)


def test_source_mutated_between_owner_observations_rejects(monkeypatch):
    contract, _ = real_source_contract()
    previous = source_issuer.canonical_source_identity
    calls = 0

    def changed_between(root):
        nonlocal calls
        calls += 1
        return previous(root) if calls == 1 else "sha256:" + "0" * 64

    monkeypatch.setattr(source_issuer, "canonical_source_identity", changed_between)
    with pytest.raises(TrustProtocolError, match="changed"):
        issue(contract)
    assert calls == 2


def test_verifier_cannot_sign_using_public_material():
    contract, keys = real_source_contract()
    source_private = _test_private_key("source_identity")
    assert type(source_private) is Ed25519PrivateKey or isinstance(
        source_private, Ed25519PrivateKey
    )
    public = keys["source_identity"][1]
    with pytest.raises(TrustProtocolError):
        issue(contract, signing_key=public)
