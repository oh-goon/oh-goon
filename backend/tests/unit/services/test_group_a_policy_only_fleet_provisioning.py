"""M174: policy-only authorization, original transaction native compiler, no bypass."""
from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.services import fleet_native_run_contract_compiler as native
from oh_goon.services.fleet_policy_enrollment import FleetPolicyEnrollment
from tests.unit.services.test_group_a_native_contract_composition import enrolled
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts


def policy_only(tmp_path, monkeypatch):
    args, conn, calls, lifecycle, binding, admit, observed = enrolled(tmp_path, monkeypatch)
    authority = FleetPolicyEnrollment(
        run_id=binding.contract.fields["run_id"],
        deployment_policy=binding.deployment_policy, authenticator=binding.authenticator,
        configured=binding.configured, current=binding.current,
        intent=binding.intent, repository_scope=binding.repository_scope,
        targets=binding.targets, path_authority=binding.path_authority,
        key_directory=binding.key_directory, trusted_public_roots=binding.trusted_public_roots,
        control_state_root=binding.control_state_root,
    )
    lifecycle._fleet_provisioner = replace(lifecycle._fleet_provisioner, enrolled=(authority,))
    assert lifecycle._fleet_provisioner.resolve("durable-run") is authority
    assert not hasattr(authority, "contract")
    return args, conn, calls, lifecycle, authority, admit, observed


def test_policy_only_compiles_original_transaction_and_signs_without_enrolled_contract(tmp_path, monkeypatch):
    args, conn, calls, lifecycle, authority, admit, observed = policy_only(tmp_path, monkeypatch)
    try:
        ticket, state = admit()
        assert ticket._lease.fleet_run_id == authority.run_id and state is not None
        assert facts(args, conn)[1:] == (1, 2)
        assert conn.execute("select count(*) from execution_lease").fetchone()[0] == 1
        assert observed == [True, False, True, False]
        assert len(calls) == 6
    finally:
        conn.close()


@pytest.mark.parametrize("attack", [
    "wrong-run", "wrong-scope", "wrong-policy-step", "compiler-drift", "compiler-unavailable",
    "native-environment-drift", "missing-policy", "disabled-compiler", "wrong-revision",
    "policy-change-after-compilation", "failed-signature", "strict-replay",
])
def test_policy_only_rejection_rolls_back_lease_both_journals_and_native_work(tmp_path, monkeypatch, attack):
    args, conn, calls, lifecycle, authority, admit, observed = policy_only(tmp_path, monkeypatch)
    try:
        if attack == "wrong-run":
            authority = replace(authority, run_id="wrong")
        elif attack == "wrong-scope":
            authority = replace(authority, targets=tuple(reversed(authority.targets)))
        elif attack == "wrong-policy-step":
            policy = authority.deployment_policy.read()
            policy["step_envelopes"] = {"unselected": {"network": "none"}}
            authority = replace(authority, deployment_policy=authority.deployment_policy.freeze(policy))
        elif attack == "compiler-drift":
            initial = native._owner_observation
            n = 0
            def changed(*a, **kw):
                nonlocal n
                n += 1
                value = initial(*a, **kw)
                if n == 2:
                    return {**value, "lock_identity": "sha256:" + "f" * 64}
                return value
            monkeypatch.setattr(native, "_owner_observation", changed)
        elif attack == "compiler-unavailable":
            monkeypatch.setattr(native, "_owner_observation", lambda *a, **kw: (_ for _ in ()).throw(
                TrustProtocolError("native authority unavailable")))
        elif attack == "native-environment-drift":
            original = native._owner_observation
            monkeypatch.setattr(native, "_owner_observation", lambda *a, **kw: {
                **original(*a, **kw), "execution_environment_identity": "sha256:" + "f" * 64})
        elif attack == "missing-policy":
            with pytest.raises(TrustProtocolError, match="policy-only"):
                replace(authority, deployment_policy=None)
            return
        elif attack == "disabled-compiler":
            lifecycle._require_compiled_fleet_contract = False
        elif attack == "wrong-revision":
            pass
        elif attack == "policy-change-after-compilation":
            initial = native._owner_observation
            n = 0
            def replacing(*a, **kw):
                nonlocal n
                n += 1
                result = initial(*a, **kw)
                if n == 3:
                    return {**result, "native_tool_identity": "sha256:" + "e" * 64}
                return result
            monkeypatch.setattr(native, "_owner_observation", replacing)
        elif attack == "failed-signature":
            authority = replace(authority, key_directory=authority.key_directory.parent / "absent")
        if authority is not lifecycle._fleet_provisioner.enrolled[0]:
            lifecycle._fleet_provisioner = replace(lifecycle._fleet_provisioner, enrolled=(authority,))
        with pytest.raises(Exception):
            if attack == "wrong-revision":
                admit(expected_durable_revision=-1)
            elif attack == "strict-replay":
                admit(rerun_from_run_id="other")
            else:
                admit()
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("select count(*) from execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_policy_only_unenrolled_run_rejected_before_control_plane_planning(tmp_path, monkeypatch):
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane
    from oh_goon.core.capability_registry import resolve_capability_intent
    from oh_goon.core.vocabulary import OperationAction
    args, conn, calls, lifecycle, _, _admit, _ = policy_only(tmp_path, monkeypatch)
    class NoPlanner:
        def plan(self, *a, **kw):
            raise AssertionError("planner invoked")
    class NoWorker:
        def submit(self, *a, **kw):
            raise AssertionError("worker invoked")
    plane = FleetRunControlPlane(NoPlanner(), args["journal"], lifecycle,
                                object(), NoWorker(), object())
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target)
    try:
        with pytest.raises(TrustProtocolError, match="not explicitly provisioned"):
            plane._start_intent(
                intent, repository_scope=args["repository_scope"], fleet_run_id="unknown-run",
                source=args["intent"].source, readmitted_from_run_id=None,
                rerun_from_run_id=None,
            )
        assert facts(args, conn) == (None, 0, 0)
        assert calls == []
    finally:
        conn.close()


def test_policy_and_legacy_enrollments_cannot_share_same_run_identity(tmp_path, monkeypatch):
    args, conn, calls, lifecycle, policy, _admit, _ = policy_only(tmp_path, monkeypatch)
    try:
        assert type(lifecycle._fleet_provisioner.enrolled[0]) is FleetPolicyEnrollment
        with pytest.raises(TrustProtocolError, match="duplicate"):
            replace(lifecycle._fleet_provisioner, enrolled=(policy, policy))
    finally:
        conn.close()
