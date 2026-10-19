"""M173: enrolled native contract matches the original reservation cut before effects."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.services import fleet_native_run_contract_compiler as native
from oh_goon.services.fleet_native_run_contract_compiler import (
    DEPLOYMENT_POLICY_FIELDS,
    DERIVED_FIELDS,
    DeploymentRunContractPolicy,
)
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def enrolled(tmp_path, monkeypatch, *, attach_policy=True, require=True):
    args, params, conn, calls, lifecycle, binding, provisioner, _, _, admit = provisioned(
        tmp_path, monkeypatch
    )
    fields = binding.contract.fields
    active = {
        step for item in args["intent"].repository_admissions for step in item.active_step_ids
    }
    assert active
    deployment = {name: fields[name] for name in DEPLOYMENT_POLICY_FIELDS}
    deployment["step_envelopes"] = {step: {"network": "none"} for step in active}
    deployment["step_teardown_envelopes"] = {step: {"network": "none"} for step in active}
    policy = DeploymentRunContractPolicy.freeze(deployment)
    original = AdmittedRunContract.freeze({**fields, **deployment})
    updated = replace(
        binding, contract=original, deployment_policy=policy if attach_policy else None
    )
    lifecycle._fleet_provisioner = replace(provisioner, enrolled=(updated,))
    lifecycle._require_compiled_fleet_contract = require
    observed = []

    def measured(prework, authority, control_state_root, *, check_uv):
        event, _ = prework.read(journal=args["journal"], run_id=original.fields["run_id"])
        assert event.repository_admissions == args["intent"].repository_admissions
        assert prework.connection.in_transaction
        assert (
            args["journal"]
            .read_view_from_connection(prework.connection)
            .run_detail(original.fields["run_id"])
            is None
        )
        observed.append(check_uv)
        return {field: original.fields[field] for field in DERIVED_FIELDS}

    monkeypatch.setattr(native, "_owner_observation", measured)
    return args, conn, calls, lifecycle, updated, admit, observed


def test_compiler_runs_inside_original_reservation_before_six_native_signatures(
    tmp_path, monkeypatch
):
    args, conn, calls, _lifecycle, _binding, admit, observations = enrolled(tmp_path, monkeypatch)
    try:
        ticket, state = admit()
        assert state and ticket._lease.fleet_run_id == "durable-run"
        assert facts(args, conn)[1:] == (1, 2)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 1
        assert observations == [True, False]
        assert len(calls) == 6
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "missing-policy",
        "wrong-compiler-identity",
        "second-pass-drift",
        "wrong-policy-steps",
        "missing-compiler",
        "wrong-policy-type",
    ],
)
def test_compiled_contract_fail_closed_with_zero_lease_run_and_issuer_work(
    tmp_path, monkeypatch, attack
):
    args, conn, calls, lifecycle, binding, admit, observations = enrolled(
        tmp_path, monkeypatch, attach_policy=attack != "missing-policy"
    )
    try:
        if attack == "wrong-compiler-identity":
            original = native._owner_observation

            def wrong(*args, **kwargs):
                fields = original(*args, **kwargs)
                return {**fields, "native_tool_identity": "sha256:" + "f" * 64}

            monkeypatch.setattr(native, "_owner_observation", wrong)
        elif attack == "second-pass-drift":
            original = native._owner_observation
            count = 0

            def changing(*args, **kwargs):
                nonlocal count
                count += 1
                fields = original(*args, **kwargs)
                if count == 2:
                    return {**fields, "lock_identity": "sha256:" + "a" * 64}
                return fields

            monkeypatch.setattr(native, "_owner_observation", changing)
        elif attack == "wrong-policy-steps":
            payload = binding.deployment_policy.read()
            payload["step_envelopes"] = {"fake-step": {"network": "none"}}
            lifecycle._fleet_provisioner = replace(
                lifecycle._fleet_provisioner,
                enrolled=(
                    replace(binding, deployment_policy=DeploymentRunContractPolicy.freeze(payload)),
                ),
            )
        elif attack == "missing-compiler":

            def unavailable(*_a, **_kw):
                raise TrustProtocolError("native compiler unavailable")

            monkeypatch.setattr(native, "_owner_observation", unavailable)
        elif attack == "wrong-policy-type":
            lifecycle._fleet_provisioner = replace(
                lifecycle._fleet_provisioner,
                enrolled=(replace(binding, deployment_policy=object()),),
            )
        with pytest.raises((TrustProtocolError, ValueError)):
            admit()
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_required_compiler_rejects_unprovisioned_policy_before_planning(tmp_path, monkeypatch):
    args, conn, calls, lifecycle, _, _, observed = enrolled(
        tmp_path, monkeypatch, attach_policy=False
    )
    try:
        with pytest.raises(TrustProtocolError, match="not provisioned"):
            lifecycle.require_fleet_prework_available(args["intent"].target, "durable-run")
        assert facts(args, conn) == (None, 0, 0)
        assert observed == calls == []
    finally:
        conn.close()
