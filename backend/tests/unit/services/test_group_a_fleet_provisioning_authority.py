"""M171: exact enrolled Run and native deployment roots, before Fleet work."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.enforcer_lace_issuer_evidence import AUTHORITY_FIELDS
from oh_goon.core.enforcer_lace_trust import AdmittedRunContract, TrustProtocolError
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime._executor_configuration import configured_executor_pool
from oh_goon.runtime.enforcer_lace_issuer_keyring import IssuerKeyReference
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_live_composition import _real_path
from tests.unit.services.test_group_a_precommit_native_issuer_migration import _native_owner_stubs


def provisioned(tmp_path, monkeypatch):
    args, params, conn, _calls, lifecycle, binding, admit = _real_path(tmp_path, monkeypatch)
    host = params["path_authority"]
    bwrap = tmp_path / "bwrap"
    bwrap.write_text("#!/bin/sh\nexit 0\n")
    bwrap.chmod(0o755)
    inventory = tmp_path / "executors.toml"
    inventory.write_text(f'''version = 8
placement_wait_seconds = 0
placement_queue_limit = 64
runtime_inspector = "{host.runtime_inspector}"
trusted_host_roots = ["{host.trusted_roots[0]}"]
tool_search_paths = ["{host.tool_search_paths[0]}"]
[[executors]]
id = "worker-m171"
generation = 7
execution = {{ environment = {{ backend = "bubblewrap" }} }}
bwrap_executable = "{bwrap}"
priority = 1
cpu_millis = 2000
memory_mib = 2048
cgroup_root = "/sys/fs/cgroup/oh-goon"
''')
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(inventory))
    owner = configured_executor_pool()
    observed = owner.executors[0].resolve_environment(owner.path_authority)
    old_contract = binding.contract
    contract = AdmittedRunContract.freeze(
        {
            **old_contract.fields,
            "executor_generation": observed.executor_generation,
            "execution_environment_identity": observed.execution_environment_identity,
            "backend_capability_manifest_identity": observed.capability_manifest_identity,
        }
    )
    binding = replace(binding, contract=contract, configured=observed, current=observed)
    params["contract"] = contract
    params["configured"] = params["current"] = observed
    root = tmp_path / "trusted-public-keys"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    references = {}
    for field in sorted(AUTHORITY_FIELDS):
        filename = f"{field}.pub"
        leaf = root / filename
        leaf.write_bytes(params["trusted_public_roots"][field][1])
        leaf.chmod(0o600)
        references[field] = IssuerKeyReference(
            issuer_id=params["trusted_public_roots"][field][0],
            filename=filename,
        )
    provisioner = FleetPreworkProvisioner(
        enrolled=(binding,),
        executor_inventory_file=inventory,
        trusted_key_directory=root,
        issuer_references=references,
    )
    lifecycle._fleet_prework = None
    lifecycle._fleet_provisioner = provisioner
    calls = _native_owner_stubs(monkeypatch, contract)
    return args, params, conn, calls, lifecycle, binding, provisioner, inventory, root, admit


def test_actual_fleet_lifecycle_uses_provisioned_run_and_original_transaction(
    tmp_path, monkeypatch
):
    args, params, conn, calls, lifecycle, binding, provisioner, _, _, admit = provisioned(
        tmp_path, monkeypatch
    )
    try:
        assert provisioner.resolve("durable-run") is binding
        ticket, state = admit()
        assert ticket._lease.fleet_run_id == "durable-run"
        assert state is not None and facts(args, conn)[1:] == (1, 2)
        assert set(calls) == AUTHORITY_FIELDS
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "unknown-run",
        "changed-inventory",
        "replaced-inventory",
        "wrong-selected-inventory",
        "wrong-public",
        "missing-public",
        "weak-public",
        "revoked-signer",
        "wrong-contract",
        "wrong-backend",
        "wrong-path-authority",
        "wrong-signer-directory",
        "missing-enrollment",
        "duplicate-enrollment",
        "changed-public-during-read",
    ],
)
def test_provisioning_rejects_before_lease_or_run_or_issuer_work(tmp_path, monkeypatch, attack):
    args, params, conn, calls, lifecycle, binding, provisioner, inventory, root, admit = (
        provisioned(tmp_path, monkeypatch)
    )
    from oh_goon.core.enforcer_lace_trust import canonical_bytes

    leaf = root / "source_identity.pub"
    try:
        if attack == "unknown-run":
            lifecycle._fleet_provisioner = provisioner
        elif attack == "changed-inventory":
            inventory.write_text(inventory.read_text().replace("generation = 7", "generation = 8"))
        elif attack == "replaced-inventory":
            payload = inventory.read_bytes()
            inventory.unlink()
            inventory.write_bytes(payload)
            # Native owner still accepts regular replacement *before* the observation;
            # force a durable content mismatch in the new inode.
            inventory.write_text(
                inventory.read_text().replace("cpu_millis = 2000", "cpu_millis = 3000")
            )
        elif attack == "wrong-selected-inventory":
            monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "other.toml"))
        elif attack == "wrong-public":
            leaf.write_bytes(b"x" * 32)
        elif attack == "missing-public":
            leaf.unlink()
        elif attack == "weak-public":
            leaf.chmod(0o644)
        elif attack == "revoked-signer":
            policy_path = binding.key_directory / "signers.json"
            import json

            policy = json.loads(policy_path.read_bytes())
            policy["issuers"]["source_identity"]["state"] = "revoked"
            policy_path.write_bytes(canonical_bytes(policy))
        elif attack == "wrong-contract":
            fields = binding.contract.fields
            fields["execution_environment_identity"] = "sha256:" + "a" * 64
            lifecycle._fleet_provisioner = replace(
                provisioner,
                enrolled=(replace(binding, contract=AdmittedRunContract.freeze(fields)),),
            )
        elif attack == "wrong-backend":
            inventory.write_text(
                inventory.read_text().replace('backend = "bubblewrap"', 'backend = "opensandbox"')
            )
        elif attack == "wrong-path-authority":
            lifecycle._fleet_provisioner = replace(
                provisioner, enrolled=(replace(binding, path_authority=object()),)
            )
        elif attack == "wrong-signer-directory":
            lifecycle._fleet_provisioner = replace(
                provisioner, enrolled=(replace(binding, key_directory=tmp_path / "other-keys"),)
            )
        elif attack == "missing-enrollment":
            with pytest.raises(TrustProtocolError, match="complete explicit"):
                replace(provisioner, enrolled=())
            return
        elif attack == "duplicate-enrollment":
            with pytest.raises(TrustProtocolError, match="duplicate"):
                replace(provisioner, enrolled=(binding, binding))
            return
        elif attack == "changed-public-during-read":
            from oh_goon.services import fleet_prework_provisioner as module

            original = module.load_explicit_issuer_verifier
            runs = 0

            def change(*a, **k):
                nonlocal runs
                result = original(*a, **k)
                runs += 1
                if runs == 1:
                    leaf.write_bytes(b"x" * 32)
                return result

            monkeypatch.setattr(module, "load_explicit_issuer_verifier", change)
        with pytest.raises((TrustProtocolError, FleetPlanInvalid)):
            if attack == "unknown-run":
                lifecycle.require_fleet_prework_available(args["intent"].target, "unknown-run")
            else:
                admit()
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_unenrolled_run_stops_original_control_plane_before_planning(tmp_path, monkeypatch):
    from oh_goon.core.capability_registry import resolve_capability_intent
    from oh_goon.core.vocabulary import OperationAction
    from oh_goon.services.fleet_run_control_plane import FleetRunControlPlane

    args, params, conn, calls, lifecycle, binding, provisioner, _, _, _ = provisioned(
        tmp_path, monkeypatch
    )

    class NoPlanner:
        def plan(self, *args, **kwargs):
            raise AssertionError("Fleet planning may not begin without Run enrollment")

    class NoWorker:
        def submit(self, *args, **kwargs):
            raise AssertionError("execution cannot begin without Run enrollment")

    plane = FleetRunControlPlane(
        NoPlanner(), args["journal"], lifecycle, object(), NoWorker(), object()
    )
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, args["intent"].target)
    try:
        with pytest.raises(TrustProtocolError, match="not explicitly provisioned"):
            plane._start_intent(
                intent,
                repository_scope=args["repository_scope"],
                fleet_run_id="untrusted-run",
                source=args["intent"].source,
                readmitted_from_run_id=None,
                rerun_from_run_id=None,
            )
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT count(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_concurrent_binding_authorities_rejected_before_lifecycle_exists(tmp_path, monkeypatch):
    args, params, conn, _, lifecycle, binding, provisioner, _, _, _ = provisioned(
        tmp_path, monkeypatch
    )
    from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle

    try:
        with pytest.raises(TrustProtocolError, match="one provisioning authority"):
            FleetRunLifecycle(
                args["journal"]._database,
                args["journal"],
                params["execution"],
                object(),
                require_authenticated_fleet=True,
                fleet_prework=binding,
                fleet_provisioner=provisioner,
            )
    finally:
        conn.close()
