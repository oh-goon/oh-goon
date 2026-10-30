"""M204: read-only, original-owner deployment probe; never an admission artifact."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.enforcer_lace_trust import TrustProtocolError, canonical_bytes
from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from tests.unit.services.test_group_a_atomic_fleet_prework_ingress import facts
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned


def test_native_deployment_readiness_public_and_non_authorizing(tmp_path, monkeypatch):
    args, _params, conn, calls, _lifecycle, binding, provisioner, _inventory, _roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    try:
        observation = provisioner.inspect_deployment_readiness("durable-run")
        assert type(observation) is MappingProxyType
        assert observation == {
            "schema": "oh-goon.fleet-deployment-readiness/v1",
            "status": "OBSERVED_NOT_ADMITTED",
            "run_id": "durable-run",
            "enrollment_kind": "FleetPreworkComposition",
            "executor_id": binding.configured.executor_id,
            "executor_generation": binding.configured.executor_generation,
            "execution_environment_identity": binding.configured.execution_environment_identity,
            "backend_capability_manifest_identity": binding.configured.capability_manifest_identity,
            "independently_verified_issuer_roots": 6,
            "run_admission": "NOT_PERFORMED",
            "app_certification": "NOT_ESTABLISHED",
            "authenticated_replay": "NOT_AUTHORIZED",
            "physical_containment": "NOT_ESTABLISHED",
        }
        with pytest.raises(TypeError):
            observation["run_admission"] = "ADMITTED"
        output = str(dict(observation))
        assert str(provisioner.trusted_key_directory) not in output
        assert str(binding.key_directory) not in output
        assert "authenticator" not in output and "private" not in output
        assert "issuer_references" not in output
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "unknown-run",
        "missing-inventory-selection",
        "inventory-generation-drift",
        "changed-backend",
        "missing-key",
        "weak-key",
        "substituted-key",
        "revoked-signer",
        "different-path-authority",
        "different-signer-custody",
    ],
)
def test_untrusted_deployment_probe_fails_closed_before_any_work(tmp_path, monkeypatch, attack):
    args, _params, conn, calls, _lifecycle, binding, provisioner, inventory, roots, _admit = (
        provisioned(tmp_path, monkeypatch)
    )
    try:
        run_id = "durable-run"
        if attack == "unknown-run":
            run_id = "not-enrolled"
        elif attack == "missing-inventory-selection":
            monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "missing.toml"))
        elif attack == "inventory-generation-drift":
            inventory.write_text(inventory.read_text().replace("generation = 7", "generation = 8"))
        elif attack == "changed-backend":
            inventory.write_text(
                inventory.read_text().replace('backend = "bubblewrap"', 'backend = "opensandbox"')
            )
        elif attack == "missing-key":
            (roots / "source_identity.pub").unlink()
        elif attack == "weak-key":
            (roots / "source_identity.pub").chmod(0o644)
        elif attack == "substituted-key":
            (roots / "source_identity.pub").write_bytes(b"x" * 32)
        elif attack == "revoked-signer":
            import json

            path = binding.key_directory / "signers.json"
            payload = json.loads(path.read_bytes())
            payload["issuers"]["source_identity"]["state"] = "revoked"
            path.write_bytes(canonical_bytes(payload))
        elif attack == "different-path-authority":
            provisioner = replace(
                provisioner, enrolled=(replace(binding, path_authority=object()),)
            )
        elif attack == "different-signer-custody":
            provisioner = replace(
                provisioner, enrolled=(replace(binding, key_directory=tmp_path / "unknown"),)
            )
        with pytest.raises((TrustProtocolError, FleetPlanInvalid)):
            provisioner.inspect_deployment_readiness(run_id)
        assert facts(args, conn) == (None, 0, 0)
        assert conn.execute("SELECT COUNT(*) FROM execution_lease").fetchone()[0] == 0
        assert calls == []
    finally:
        conn.close()


def test_readiness_does_not_enter_cli_or_create_ambient_deployment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon import cli

    monkeypatch.setenv("OH_GOON_FLEET_PROVISIONER", "fake")
    monkeypatch.setenv("OH_GOON_FLEET_ISSUER_KEYS", "fake")
    observed: list[str] = []
    monkeypatch.setattr(cli, "_run_pre_runtime_command", lambda _args: None)

    def build(
        _cls: type[object],
        *,
        standard_local: bool = False,
        startup_purpose: str = "application",
        standard_process_isolation: object | None = None,
        git_service: object | None = None,
    ) -> object:
        assert startup_purpose == "workflow"
        assert (standard_process_isolation, git_service) == (None, None)
        observed.append("standard-local" if standard_local else "ordinary-no-provisioner")
        return object()

    monkeypatch.setattr(
        ApplicationRuntime,
        "build",
        classmethod(build),
    )
    monkeypatch.setattr(cli, "_execute_stateful_command", lambda _runtime, _args: 2)
    from tests.factories.run_configuration import write_run_configuration

    configuration = write_run_configuration(tmp_path, monkeypatch)
    assert cli.main(["workflow", "prepare", str(configuration)]) == 0
    assert observed == []
    assert cli.main(["run", str(configuration), "--fleet-run-id", "durable-run"]) == 2
    assert observed == ["standard-local"]


def test_deployment_probe_rejects_no_or_duplicate_enrollments(tmp_path, monkeypatch):
    *_unused, provisioner, _inventory, _root, _admit = provisioned(tmp_path, monkeypatch)
    assert type(provisioner) is FleetPreworkProvisioner
    with pytest.raises(TrustProtocolError, match="complete explicit"):
        replace(provisioner, enrolled=())
    with pytest.raises(TrustProtocolError, match="duplicate"):
        replace(provisioner, enrolled=(provisioner.enrolled[0], provisioner.enrolled[0]))
