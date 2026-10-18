"""M164: fixed native issuer wiring and whole-bundle signer generation fences."""

from __future__ import annotations

import os

import pytest
from cryptography.hazmat.primitives import serialization

from oh_goon.core.enforcer_lace_issuer_evidence import (
    AUTHORITY_FIELDS, ISSUER_PROTOCOL, SIGNATURE_DOMAIN, IssuerEvidence,
    TrustedIssuerVerifier,
)
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes
from oh_goon.repository_tooling import (
    enforcer_lace_backend_capability_issuer as capability_owner,
    enforcer_lace_captured_run_issuer as run_owner,
    enforcer_lace_execution_environment_issuer as environment_owner,
    enforcer_lace_native_lock_issuer as lock_owner,
    enforcer_lace_native_tool_issuer as tool_owner,
    enforcer_lace_native_issuer_suite as suite,
)
from oh_goon.repository_tooling.enforcer_lace_signer_custody import SIGNER_POLICY_SCHEMA
from oh_goon.runtime._executor_configuration import configured_executor_pool
from tests.factories.journal import start_run
from tests.unit.services.test_group_a_execution_environment_issuer import fixture as environment_fixture
from tests.unit.services.test_group_a_verified_ingress_authorities import _test_private_key


def fixture(tmp_path, monkeypatch):
    journal, contract, inventory, _ = environment_fixture(tmp_path, monkeypatch)
    key_dir = tmp_path / "private-issuers"
    key_dir.mkdir(mode=0o700)
    key_dir.chmod(0o700)
    roots = {}
    policy = {"schema": SIGNER_POLICY_SCHEMA, "issuers": {}}
    for i, field in enumerate(sorted(AUTHORITY_FIELDS)):
        key = _test_private_key(field)
        public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        issuer = f"test-owner-{i}"
        filename = f"owner-{i}.seed"
        private = key_dir / filename
        private.write_bytes(key.private_bytes(serialization.Encoding.Raw,
                                             serialization.PrivateFormat.Raw,
                                             serialization.NoEncryption()))
        private.chmod(0o600)
        roots[field] = issuer, public
        policy["issuers"][field] = {"issuer_id": issuer, "key_id": f"test-generation-{i}",
                                    "private_key_file": filename,
                                    "public_key_hex": public.hex(), "state": "active"}
    write_policy(key_dir, policy)
    return journal, contract, configured_executor_pool().path_authority, key_dir, roots, policy


def write_policy(key_dir, policy):
    path = key_dir / "signers.json"
    path.write_bytes(canonical_bytes(policy))
    path.chmod(0o600)


def stub_native_owners(monkeypatch, contract):
    """Replace only external native observers unavailable offline; keep fixed dispatch."""
    calls = []
    names = [
        (run_owner, "issue_observed_captured_run_repository_identity", "repository_identity"),
        (run_owner, "issue_observed_captured_run_source", "source_identity"),
        (lock_owner, "issue_observed_captured_run_lock_identity", "lock_identity"),
        (tool_owner, "issue_observed_captured_run_native_tool_identity", "native_tool_identity"),
        (environment_owner, "issue_observed_captured_run_execution_environment_identity", "execution_environment_identity"),
        (capability_owner, "issue_observed_captured_run_backend_capability_manifest_identity", "backend_capability_manifest_identity"),
    ]
    for module, name, field in names:
        def owner(*, journal, contract, issuer_id, signing_key, _field=field, **kwargs):
            calls.append((_field, issuer_id, frozenset(kwargs)))
            payload = canonical_bytes({"protocol": ISSUER_PROTOCOL, "field": _field,
                "run_id": contract.fields["run_id"], "contract_identity": contract.identity,
                "identity": contract.fields[_field],
                "executor_generation": contract.fields["executor_generation"]})
            return IssuerEvidence(_field, issuer_id, payload,
                signing_key.sign(SIGNATURE_DOMAIN + _field.encode() + b"\x00" + payload).hex())
        monkeypatch.setattr(module, name, owner)
    return calls


def issue(journal, contract, authority, key_dir, roots):
    return suite.issue_fixed_native_admission_evidence(
        journal=journal, contract=contract, path_authority=authority,
        key_directory=key_dir, trusted_public_roots=roots)


def test_all_six_fixed_native_owner_bindings_and_independent_signatures(tmp_path, monkeypatch):
    journal, contract, authority, key_dir, roots, _ = fixture(tmp_path, monkeypatch)
    calls = stub_native_owners(monkeypatch, contract)
    evidence = issue(journal, contract, authority, key_dir, roots)
    assert [x.field for x in evidence] == sorted(AUTHORITY_FIELDS)
    assert {call[0] for call in calls} == AUTHORITY_FIELDS
    assert {call[1] for call in calls} == {value[0] for value in roots.values()}
    assert {key for _, _, keys in calls for key in keys} <= {"path_authority", "control_state_root"}
    TrustedIssuerVerifier(roots).verify(contract, evidence=evidence, control_epoch=1,
        lease_identity="test-lease", lease_is_current=lambda *_: True)
    assert not hasattr(suite, "sign")


@pytest.mark.parametrize("attack", [
    "revoked", "root-replaced", "policy-replaced-between-claims", "revoked-between-claims",
    "run-starts-at-end", "wrong-root", "unknown-owner", "wrong-journal", "missing-policy",
])
def test_bundle_rejects_untrusted_or_stale_authority(tmp_path, monkeypatch, attack):
    journal, contract, authority, key_dir, roots, policy = fixture(tmp_path, monkeypatch)
    stub_native_owners(monkeypatch, contract)
    if attack == "revoked":
        policy["issuers"][sorted(AUTHORITY_FIELDS)[0]]["state"] = "revoked"
        write_policy(key_dir, policy)
    elif attack == "root-replaced":
        roots[sorted(AUTHORITY_FIELDS)[0]] = ("another", roots[sorted(AUTHORITY_FIELDS)[0]][1])
    elif attack == "wrong-root":
        roots.pop(sorted(AUTHORITY_FIELDS)[0])
    elif attack == "missing-policy":
        (key_dir / "signers.json").unlink()
    elif attack == "unknown-owner":
        monkeypatch.setattr(tool_owner, "issue_observed_captured_run_native_tool_identity",
                            lambda **kwargs: None)
    elif attack == "wrong-journal":
        with pytest.raises(TrustProtocolError, match="trusted native Journal"):
            issue(object(), contract, authority, key_dir, roots)
        return
    else:
        original = suite.issue_with_deployment_signer
        calls = 0
        def changed(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == (6 if attack == "run-starts-at-end" else 2):
                if attack == "policy-replaced-between-claims":
                    replacement = key_dir / "replacement"
                    replacement.write_bytes((key_dir / "signers.json").read_bytes())
                    replacement.chmod(0o600)
                    os.replace(replacement, key_dir / "signers.json")
                elif attack == "revoked-between-claims":
                    policy["issuers"][sorted(AUTHORITY_FIELDS)[0]]["state"] = "revoked"
                    write_policy(key_dir, policy)
                elif attack == "run-starts-at-end":
                    # The journal sequence is append-only; final re-read must reject.
                    start_run(journal, "durable-run")
            return result
        monkeypatch.setattr(suite, "issue_with_deployment_signer", changed)
    with pytest.raises(TrustProtocolError):
        issue(journal, contract, authority, key_dir, roots)


def test_user_selected_issuer_or_signing_callback_is_not_an_entrypoint(tmp_path, monkeypatch):
    journal, contract, authority, key_dir, roots, _ = fixture(tmp_path, monkeypatch)
    stub_native_owners(monkeypatch, contract)
    with pytest.raises(TypeError):
        suite.issue_fixed_native_admission_evidence(
            journal=journal, contract=contract, path_authority=authority,
            key_directory=key_dir, trusted_public_roots=roots,
            native_issuer=lambda *_: None)
    with pytest.raises(TypeError):
        suite.issue_fixed_native_admission_evidence(
            journal=journal, contract=contract, path_authority=authority,
            key_directory=key_dir, trusted_public_roots=roots,
            selected_repository_ids=("untrusted",))


def test_mutating_external_root_mapping_during_bundle_cannot_change_trust(tmp_path, monkeypatch):
    journal, contract, authority, key_dir, roots, _ = fixture(tmp_path, monkeypatch)
    stub_native_owners(monkeypatch, contract)
    pinned = dict(roots)
    original = suite.issue_with_deployment_signer
    calls = 0
    def mutate_external_roots(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            roots[sorted(AUTHORITY_FIELDS)[0]] = ("untrusted-replacement", b"x" * 32)
        return result
    monkeypatch.setattr(suite, "issue_with_deployment_signer", mutate_external_roots)
    evidence = issue(journal, contract, authority, key_dir, roots)
    TrustedIssuerVerifier(pinned).verify(contract, evidence=evidence, control_epoch=1,
        lease_identity="test-lease", lease_is_current=lambda *_: True)
