"""SKILL-3C2B: deployment policy cannot be copied from author effects."""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.fleet_plan import AgentStepRequestedEffects
from oh_goon.services import fleet_native_run_contract_compiler as native_compiler
from oh_goon.services.fleet_native_run_contract_compiler import (
    DeploymentAgentPolicySource,
    DeploymentRunContractPolicy,
    NativeAgentEffectCeilings,
    compile_native_prework_run_contract,
)
from tests.unit.services.test_group_a_native_run_contract_compiler import setup_native
from tests.unit.services.test_group_a_precommit_native_issuer_migration import _capture


def effects(*, write=False, tools=(), recipients=()):
    return AgentStepRequestedEffects.model_validate(
        {
            "repository_access": "write" if write else "read_only",
            "recipients": list(recipients),
            "capabilities": {"tools": list(tools)},
        }
    ).model_dump(mode="json")


def enrolled_admin_source(tmp_path, monkeypatch, policy):
    # Unit-test simulation of the separate unprivileged signer / admin-root
    # DAC split. Production requires actual Linux custody observations.
    monkeypatch.setattr(native_compiler, "_native_agent_policy_principal", lambda: os.geteuid() + 1)
    monkeypatch.setattr(native_compiler, "_require_admin_policy_ancestry", lambda *a, **k: ())
    path = tmp_path / "admin-agent-effects.json"
    path.write_bytes(policy.encoded)
    return DeploymentAgentPolicySource.enroll(path, owner_uid=os.geteuid())


def agent_profile(profile, *, request=None):
    step = next(iter(profile["step_envelopes"]))
    profile["step_envelopes"][step] = {
        "workload_kind": "agent",
        "agent_handoff_identity": "agent-step-handoff-test",
        "agent_requested_effects": effects(write=True, tools=("git",), recipients=("model_a",))
        if request is None
        else request,
    }
    return step


def compile_with(journal, event, intent, authority, policy):
    with journal._database.transaction(write=True) as connection:
        original_changes = connection.total_changes
        prework = _capture(journal, connection, intent, event)
        contract = compile_native_prework_run_contract(
            prework=prework,
            deployment_policy=policy,
            path_authority=authority,
        )
        assert connection.total_changes == original_changes
    return contract


def test_independent_native_limits_are_injected_into_original_signed_run(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, _ = setup_native(tmp_path, monkeypatch)
    step = agent_profile(profile)
    ceiling = effects(write=True, tools=("git", "pytest"), recipients=("model_a", "model_b"))
    source = {step: deepcopy(ceiling)}
    native = NativeAgentEffectCeilings.freeze(source)
    policy = DeploymentRunContractPolicy.freeze(
        profile,
        agent_effect_policy=native,
        agent_policy_source=enrolled_admin_source(tmp_path, monkeypatch, native),
    )
    source[step]["recipients"] = ["wrong"]
    profile["step_envelopes"][step]["agent_requested_effects"]["recipients"] = ["wrong"]
    assert native.read()[step] == ceiling
    contract = compile_with(journal, event, intent, authority, policy)
    signed = contract.fields["step_envelopes"][step]
    assert signed["agent_effect_limits"] == ceiling
    assert signed["agent_requested_effects"]["recipients"] == ["model_a"]
    assert signed["agent_effect_limits"] != signed["agent_requested_effects"]
    contract.verify()
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_agent_native_policy_absent_fails_before_admission(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, _ = setup_native(tmp_path, monkeypatch)
    agent_profile(profile)
    policy = DeploymentRunContractPolicy.freeze(profile)
    with pytest.raises(TrustProtocolError, match="separately enrolled"):
        compile_with(journal, event, intent, authority, policy)
    assert journal.read_view().run_detail(event.fleet_run_id) is None


@pytest.mark.parametrize(
    "attack",
    [
        "author-supplied-ceiling",
        "policy-not-enrolled",
        "wrong-step",
        "policy-exceeds-demand",
        "policy-extra-command",
        "malformed-policy",
        "tampered-frozen-policy",
        "tampered-deployment-policy",
    ],
)
def test_policy_must_be_separately_enrolled_and_cover_exact_steps(tmp_path, monkeypatch, attack):
    journal, intent, event, authority, profile, _ = setup_native(tmp_path, monkeypatch)
    step = agent_profile(profile)
    ceiling = effects(write=True, tools=("git", "pytest"), recipients=("model_a",))
    if attack == "author-supplied-ceiling":
        profile["step_envelopes"][step]["agent_effect_limits"] = ceiling
        with pytest.raises(TrustProtocolError, match="cannot come from Step envelopes"):
            DeploymentRunContractPolicy.freeze(profile)
        return
    if attack == "malformed-policy":
        with pytest.raises(TrustProtocolError, match="canonical"):
            NativeAgentEffectCeilings.freeze({step: {"repository_access": "write"}})
        return
    native = NativeAgentEffectCeilings.freeze(
        {
            "other-step" if attack == "wrong-step" else step: (
                effects(tools=("git",)) if attack == "policy-exceeds-demand" else ceiling
            )
        }
    )
    if attack == "tampered-frozen-policy":
        native = replace(native, encoded=b'{ "untrusted": true }')
        with pytest.raises(TrustProtocolError):
            DeploymentRunContractPolicy.freeze(profile, agent_effect_policy=native)
        return
    policy = DeploymentRunContractPolicy.freeze(
        profile,
        agent_effect_policy=None if attack == "policy-not-enrolled" else native,
        agent_policy_source=None
        if attack == "policy-not-enrolled"
        else enrolled_admin_source(tmp_path, monkeypatch, native),
    )
    if attack == "policy-extra-command":
        policy = DeploymentRunContractPolicy.freeze(
            {
                **profile,
                "step_envelopes": {
                    **profile["step_envelopes"],
                    "command-other": {"network": "none"},
                },
            },
            agent_effect_policy=native,
            agent_policy_source=enrolled_admin_source(tmp_path, monkeypatch, native),
        )
    if attack == "tampered-deployment-policy":
        bad = deepcopy(profile)
        bad["step_envelopes"][step]["agent_effect_limits"] = ceiling
        from oh_goon.core.enforcer_lace_trust import canonical_bytes

        policy = replace(policy, encoded=canonical_bytes(bad))
    with pytest.raises(TrustProtocolError):
        compile_with(journal, event, intent, authority, policy)
    assert journal.read_view().run_detail(event.fleet_run_id) is None


def test_policy_for_command_only_plan_is_not_accepted(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, _ = setup_native(tmp_path, monkeypatch)
    step = next(iter(profile["step_envelopes"]))
    policy = DeploymentRunContractPolicy.freeze(
        profile,
        agent_effect_policy=NativeAgentEffectCeilings.freeze({step: effects()}),
    )
    with pytest.raises(TrustProtocolError, match="non-Agent Plan"):
        compile_with(journal, event, intent, authority, policy)
