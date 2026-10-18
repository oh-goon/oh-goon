"""M172: all measured contract identities come from original native Fleet owners."""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import CONTRACT_KEYS, TrustProtocolError
from oh_goon.repository_tooling import (
    enforcer_lace_backend_capability_issuer as capability_owner,
    enforcer_lace_execution_environment_issuer as environment_owner,
    enforcer_lace_native_lock_issuer as lock_owner,
    enforcer_lace_native_tool_issuer as tool_owner,
)
from oh_goon.services.fleet_native_run_contract_compiler import (
    DEPLOYMENT_POLICY_FIELDS, DERIVED_FIELDS, DeploymentRunContractPolicy,
    compile_native_prework_run_contract,
)
from tests.unit.services.test_group_a_precommit_native_issuer_migration import _setup, _capture


def setup_native(tmp_path, monkeypatch):
    journal, old_contract, authority, _, _, intent, event = _setup(tmp_path, monkeypatch)
    fields = old_contract.fields
    steps = {step for item in event.repository_admissions for step in item.active_step_ids}
    assert steps
    profile = {field: fields[field] for field in DEPLOYMENT_POLICY_FIELDS}
    profile["step_envelopes"] = {step: {"network": "none"} for step in steps}
    profile["step_teardown_envelopes"] = {step: {"network": "none"} for step in steps}
    from oh_goon.repository_tooling import enforcer_lace_repository_snapshot_issuer as repository_native
    selected = {target.repository_id: target.path for target in event.targets}
    admitted_by_path = {selected[item.repository_id]: item for item in event.repository_admissions}
    monkeypatch.setattr(repository_native, "repository_subject_instance_id",
                        lambda root: admitted_by_path[root].source_authority.subject_instance_id)
    monkeypatch.setattr(repository_native, "source_snapshot_identity",
                        lambda root: admitted_by_path[root].source_authority.source_snapshot)
    # Only unavailable backend/tool external observation is stubbed. The Run
    # capture, repository source, and identity projections stay native-owned.
    readings = []
    def selected(j, r, *, prework=None):
        admitted, rev = prework.read(journal=j, run_id=r)
        return admitted, rev, {".": ("dev",)}, {item.repository_id: item.path for item in admitted.targets}
    monkeypatch.setattr(lock_owner, "_selected_projects", selected)
    def uv_measure(groups, roots, *, native_check):
        readings.append("uv")
        return ({"repository_id": min(roots), "groups": ["dev"], "uv_lock_sha256": "a" * 64},)
    monkeypatch.setattr(lock_owner, "_measure", uv_measure)
    def selected_tools(j, r, *, prework=None):
        admitted, rev = prework.read(journal=j, run_id=r)
        return admitted, rev, ("uv",)
    monkeypatch.setattr(tool_owner, "_admitted_tools", selected_tools)
    def observe_tools(names, path):
        readings.append("tools")
        return ({"name": "uv", "snapshot_sha256": "b" * 64},)
    monkeypatch.setattr(tool_owner, "_observe", observe_tools)
    env = SimpleNamespace(
        executor_generation=fields["executor_generation"],
        execution_environment_identity=fields["execution_environment_identity"],
    )
    def observe_environment(j, r, *, control_state_root, prework=None):
        admitted, rev = prework.read(journal=j, run_id=r)
        readings.append("environment")
        return admitted, rev, "inventory", "file", env
    monkeypatch.setattr(environment_owner, "_observe", observe_environment)
    def observe_backend(j, r, *, control_state_root, prework=None):
        admitted, rev, p, f, observed = observe_environment(
            j, r, control_state_root=control_state_root, prework=prework)
        readings.append("backend")
        return admitted, rev, p, f, observed, fields["backend_capability_manifest_identity"]
    monkeypatch.setattr(capability_owner, "_observe", observe_backend)
    return journal, intent, event, authority, profile, readings


def compile_from(journal, event, intent, authority, profile):
    with journal._database.transaction(write=True) as connection:
        original_changes = connection.total_changes
        prework = _capture(journal, connection, intent, event)
        contract = compile_native_prework_run_contract(
            prework=prework, path_authority=authority,
            deployment_policy=DeploymentRunContractPolicy.freeze(profile),
        )
        assert connection.total_changes == original_changes
        assert journal.read_view_from_connection(connection).run_detail(event.fleet_run_id) is None
    assert journal.read_view().run_detail(event.fleet_run_id) is None
    return contract


def test_compiles_original_plan_and_six_native_claims_without_admission(tmp_path, monkeypatch):
    journal, intent, event, authority, profile, readings = setup_native(tmp_path, monkeypatch)
    original = deepcopy(profile)
    policy = DeploymentRunContractPolicy.freeze(profile)
    profile["step_envelopes"].clear()
    assert policy.read() == original  # nested caller mutation cannot change policy
    with journal._database.transaction(write=True) as connection:
        prework = _capture(journal, connection, intent, event)
        contract = compile_native_prework_run_contract(
            prework=prework, deployment_policy=policy, path_authority=authority)
        assert contract.fields["run_id"] == event.fleet_run_id
        assert contract.fields["resolved_plan_identity"] == event.target.execution_digest
        assert contract.fields["executor_generation"] > 0
        assert contract.fields["repository_identity"].startswith("sha256:")
        assert contract.fields["source_identity"].startswith("sha256:")
        assert contract.fields["lock_identity"].startswith("sha256:")
        assert contract.fields["native_tool_identity"].startswith("sha256:")
        assert len(contract.fields) == len(CONTRACT_KEYS)
        contract.verify()
        assert connection.total_changes == prework.total_changes
    assert journal.read_view().run_detail(event.fleet_run_id) is None
    assert readings.count("uv") == 2 and readings.count("tools") == 2


@pytest.mark.parametrize("attack", [
    "injected-identity", "missing-policy", "wrong-steps", "tampered-policy",
    "missing-owner", "source-drift", "uv-drift", "tool-drift", "executor-drift",
    "early-run", "wrong-prework", "noncanonical-policy",
])
def test_fails_closed_before_run_or_lease_when_authority_incomplete_or_drifts(tmp_path, monkeypatch, attack):
    journal, intent, event, authority, profile, readings = setup_native(tmp_path, monkeypatch)
    policy = DeploymentRunContractPolicy.freeze(profile)
    if attack == "injected-identity":
        profile["lock_identity"] = "sha256:" + "1" * 64
    elif attack == "missing-policy":
        profile.pop("run_authority")
    elif attack == "wrong-steps":
        profile["step_envelopes"] = {"wrong-step": {"network": "none"}}
    elif attack == "tampered-policy":
        policy = replace(policy, encoded=b"{ \"noncanonical\": true }")
    elif attack == "missing-owner":
        def missing(*args, **kwargs):
            raise TrustProtocolError("native owner unavailable")
        monkeypatch.setattr(tool_owner, "_observe", missing)
    elif attack == "source-drift":
        from oh_goon.repository_tooling import enforcer_lace_repository_snapshot_issuer as native
        original = native.source_snapshot_identity
        state = 0
        def changed(path):
            nonlocal state
            state += 1
            value = original(path)
            return replace(value, sha256="0" * 64) if state >= 4 else value
        monkeypatch.setattr(native, "source_snapshot_identity", changed)
    elif attack in {"uv-drift", "tool-drift", "executor-drift"}:
        if attack == "uv-drift":
            original = lock_owner._measure
            call = 0
            def altered(*a, **kw):
                nonlocal call
                call += 1
                result = original(*a, **kw)
                return ({**result[0], "uv_lock_sha256": "0" * 64},) if call > 1 else result
            monkeypatch.setattr(lock_owner, "_measure", altered)
        if attack == "tool-drift":
            original = tool_owner._observe
            call = 0
            def altered(*a, **kw):
                nonlocal call
                call += 1
                result = original(*a, **kw)
                return ({**result[0], "snapshot_sha256": "0" * 64},) if call > 1 else result
            monkeypatch.setattr(tool_owner, "_observe", altered)
        if attack == "executor-drift":
            original = environment_owner._observe
            call = 0
            def altered(*a, **kw):
                nonlocal call
                call += 1
                result = original(*a, **kw)
                return (*result[:4], SimpleNamespace(
                    executor_generation=999,
                    execution_environment_identity=result[4].execution_environment_identity,
                )) if call > 1 else result
            monkeypatch.setattr(environment_owner, "_observe", altered)
    elif attack == "noncanonical-policy":
        policy = replace(policy, encoded=b'{ "oops": 1 }')
    if attack in {"injected-identity", "missing-policy"}:
        with pytest.raises(TrustProtocolError):
            DeploymentRunContractPolicy.freeze(profile)
        assert journal.read_view().run_detail(event.fleet_run_id) is None
        return
    if attack == "wrong-steps":
        policy = DeploymentRunContractPolicy.freeze(profile)
    with journal._database.transaction(write=True) as conn:
        before = conn.total_changes
        cut = _capture(journal, conn, intent, event)
        if attack == "early-run":
            journal.admit_run_in(conn, event.fleet_run_id, intent, event.repository_scope, list(event.targets))
        with pytest.raises((TrustProtocolError, ValueError, TypeError)):
            compile_native_prework_run_contract(
                prework=object() if attack == "wrong-prework" else cut,
                deployment_policy=policy, path_authority=authority)
        if attack != "early-run":
            assert conn.total_changes == before
            assert journal.read_view_from_connection(conn).run_detail(event.fleet_run_id) is None
        # Roll back the deliberately premature Run if present.
        if attack == "early-run":
            conn.rollback()
    assert journal.read_view().run_detail(event.fleet_run_id) is None
