"""SKILL-3C2C: independent deployment file is the one native Agent ceiling source.

Unit fixtures simulate a foreign-owner DAC split; they do NOT attest the
physical OS custody of a production signer or enable Agent effects.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_native_run_contract_compiler as native
from oh_goon.services.fleet_native_run_contract_compiler import (
    DeploymentAgentPolicySource,
    DeploymentRunContractPolicy,
    NativeAgentEffectCeilings,
)
from tests.unit.services.test_group_a_native_run_contract_compiler import setup_native
from tests.unit.services.test_skill03c2b_native_agent_policy_issuer import (
    agent_profile,
    compile_with,
    effects,
)


def _source(tmp_path: Path, monkeypatch, *, content: bytes):
    # Simulate foreign-owner immutable directory and unprivileged process.
    # A separate test checks that real unsafe ancestry fails closed.
    monkeypatch.setattr(native, "_native_agent_policy_principal", lambda: os.geteuid() + 1)
    monkeypatch.setattr(native, "_require_admin_policy_ancestry", lambda *a, **k: ())
    path = tmp_path / "admin-ceilings.json"
    path.write_bytes(content)
    return path, DeploymentAgentPolicySource.enroll(path, owner_uid=os.geteuid())


def _native_agent(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, _ = setup_native(tmp_path, monkeypatch)
    step = agent_profile(profile)
    ceiling = NativeAgentEffectCeilings.freeze(
        {step: effects(write=True, tools=("git",), recipients=("model_a",))}
    )
    path, source = _source(tmp_path, monkeypatch, content=ceiling.encoded)
    policy = DeploymentRunContractPolicy.freeze(
        profile,
        agent_effect_policy=ceiling,
        agent_policy_source=source,
    )
    return journal, intent, event, authority, profile, ceiling, path, source, policy


def test_external_file_ceiling_reaches_original_contract_without_authority_grant(
    tmp_path, monkeypatch
):
    journal, intent, event, authority, _, ceiling, _, source, policy = _native_agent(
        tmp_path, monkeypatch
    )
    result = compile_with(journal, event, intent, authority, policy)
    envelope = result.fields["step_envelopes"][next(iter(ceiling.read()))]
    assert envelope["agent_effect_limits"] == next(iter(ceiling.read().values()))
    assert source.identity.startswith("sha256:")
    assert "effect_grant" not in envelope
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_unbacked_typed_ceiling_cannot_enter_original_native_compiler(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, ceiling, _, _, _ = _native_agent(
        tmp_path, monkeypatch
    )
    unbacked = DeploymentRunContractPolicy.freeze(profile, agent_effect_policy=ceiling)
    with pytest.raises(TrustProtocolError, match="source custody"):
        compile_with(journal, event, intent, authority, unbacked)
    assert journal.read_view().run_detail(event.fleet_run_id) is None


@pytest.mark.parametrize(
    "tamper",
    [
        "modified-bytes",
        "same-bytes-new-inode",
        "mode-writable",
        "hardlink",
        "symlink",
        "deleted",
        "wrong-owner",
        "oversize",
    ],
)
def test_original_enrollment_and_compilation_refuse_source_substitution(
    tmp_path,
    monkeypatch,
    tamper,
):
    journal, intent, event, authority, _, ceiling, path, _, policy = _native_agent(
        tmp_path, monkeypatch
    )
    if tamper == "modified-bytes":
        path.write_bytes(ceiling.encoded.replace(b"git", b"curl"))
    elif tamper == "same-bytes-new-inode":
        replacement = path.with_suffix(".next")
        replacement.write_bytes(ceiling.encoded)
        os.replace(replacement, path)
    elif tamper == "mode-writable":
        path.chmod(0o666)
    elif tamper == "hardlink":
        os.link(path, path.with_suffix(".hardlink"))
    elif tamper == "symlink":
        alternate = path.with_suffix(".other")
        path.rename(alternate)
        path.symlink_to(alternate)
    elif tamper == "deleted":
        path.unlink()
    elif tamper == "wrong-owner":
        policy = replace(
            policy,
            agent_policy_source=replace(policy.agent_policy_source, owner_uid=os.geteuid() + 2),
        )
    elif tamper == "oversize":
        path.write_bytes(b" " * (native._AGENT_POLICY_MAX_BYTES + 1))
    with pytest.raises(TrustProtocolError):
        compile_with(journal, event, intent, authority, policy)
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_enrollment_requires_exact_canonical_admin_document(tmp_path, monkeypatch):
    policy = NativeAgentEffectCeilings.freeze({"agent": effects(write=True)})
    for changed in (b" { " + policy.encoded[1:], b"{}", b"[]", b"invalid"):
        path, _ = _source(tmp_path, monkeypatch, content=policy.encoded)
        path.write_bytes(changed)
        with pytest.raises(TrustProtocolError):
            DeploymentAgentPolicySource.enroll(path, owner_uid=os.geteuid())


def test_external_document_is_not_a_self_asserted_author_grant(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, ceiling, _, source, _ = _native_agent(
        tmp_path, monkeypatch
    )
    step = next(iter(ceiling.read()))
    overbroad = NativeAgentEffectCeilings.freeze({step: effects()})
    with pytest.raises(TrustProtocolError, match="differs from enrolled ceiling"):
        DeploymentRunContractPolicy.freeze(
            profile,
            agent_effect_policy=overbroad,
            agent_policy_source=source,
        )
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_real_unsafe_ancestry_is_refused_even_with_valid_document(tmp_path):
    path = tmp_path / "admin.json"
    path.write_text("{}")
    # A directory under an untrusted writable temp tree cannot be promoted
    # to independent admin custody by choosing a different owner_uid field.
    with pytest.raises(TrustProtocolError, match="signer-mutable"):
        native._require_admin_policy_ancestry(
            path,
            owner_uid=os.geteuid() + 1,
            signer_uid=os.geteuid(),
        )


def test_root_signer_cannot_claim_independent_policy_custody(monkeypatch):
    from oh_goon.services import fleet_prework_provisioner as provisioner

    monkeypatch.setattr(provisioner, "_native_unprivileged_signer_identity", lambda: (1,))
    monkeypatch.setattr(native.os, "geteuid", lambda: 0)
    with pytest.raises(TrustProtocolError, match="privileged signer"):
        native._native_agent_policy_principal()


def test_deployment_document_drift_during_native_observation_aborts_without_admission(
    tmp_path,
    monkeypatch,
):
    journal, intent, event, authority, _, _, path, _, policy = _native_agent(tmp_path, monkeypatch)
    original = native._owner_observation
    calls = 0

    def while_observing(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            # The enrollment was valid before the native issuers observed
            # physical facts, but the admin source changed while they worked.
            replacement = path.with_suffix(".swapped")
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
        return result

    monkeypatch.setattr(native, "_owner_observation", while_observing)
    with pytest.raises(TrustProtocolError, match="changed or differs"):
        compile_with(journal, event, intent, authority, policy)
    assert calls >= 1
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_admin_policy_path_cannot_contain_parent_traversal(tmp_path):
    # A lexical absolute path containing parent components may point to a
    # different source at the admission cut; it must not become enrolled.
    candidate = tmp_path / "child" / ".." / "agent.json"
    with pytest.raises(TrustProtocolError, match="absolute deployment"):
        native._require_admin_policy_ancestry(
            candidate,
            owner_uid=os.geteuid() + 1,
            signer_uid=os.geteuid(),
        )
