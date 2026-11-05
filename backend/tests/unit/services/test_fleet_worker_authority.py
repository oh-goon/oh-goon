"""A fresh worker retains one original enrollment without ambient configuration."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from oh_goon.core.enforcer_lace_trust import (
    ProtectedReceiptKey,
    ReceiptAuthenticator,
    TrustProtocolError,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.native_issuer.enforcer_lace_execution_environment_issuer import _inventory_snapshot
from oh_goon.runtime._executor_configuration import (
    ExecutorPoolConfiguration,
    configured_executor_pool,
)
from oh_goon.services.fleet_prework_composition import FleetPreworkComposition
from oh_goon.services.fleet_prework_provisioner import FleetPreworkProvisioner
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_worker_authority import (
    FleetWorkerAuthority,
    capture_fleet_worker_authority,
    restore_fleet_worker_authority,
)
from tests.unit.services.test_group_a_fleet_provisioning_authority import provisioned
from tests.unit.services.test_group_a_policy_only_fleet_provisioning import policy_only
from tests.unit.services.test_group_a_strict_cicd_ingress import strict_fixture


@pytest.fixture
def enrolled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration]]:
    # Reuse the native inventory/keyring fixture, rather than inventing a second
    # deployment owner for worker transport tests.
    setup = cast(Callable[[Path, pytest.MonkeyPatch], tuple[object, ...]], provisioned)
    _, _, connection, _, _, binding, provisioner, _, _, _ = setup(tmp_path, monkeypatch)
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(binding, FleetPreworkComposition)
    assert isinstance(provisioner, FleetPreworkProvisioner)
    configuration = configured_executor_pool()
    assert configuration is not None
    try:
        yield binding, provisioner, configuration
    finally:
        connection.close()


def capture(
    provisioner: FleetPreworkProvisioner, configuration: ExecutorPoolConfiguration
) -> FleetWorkerAuthority:
    payload = capture_fleet_worker_authority(
        provisioner,
        "durable-run",
        configuration,
        expected_inventory_identity=_inventory_snapshot(provisioner.executor_inventory_file)[1],
    )
    assert payload is not None
    return payload


def test_worker_roundtrip_retains_original_receipt_and_native_owners(
    enrolled: tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding, provisioner, configuration = enrolled
    payload = capture(provisioner, configuration)
    transport = payload.model_dump_json()
    restored_payload = FleetWorkerAuthority.model_validate_json(transport)

    # A changed ambient selection cannot replace already resolved authority.
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/ambient/other-executors.toml")
    restored = restore_fleet_worker_authority(restored_payload, configuration)
    returned = restored.resolve("durable-run")
    assert isinstance(returned, FleetPreworkComposition)
    assert returned.contract == binding.contract
    assert returned.intent == binding.intent
    assert returned.path_authority == binding.path_authority
    assert returned.trusted_public_roots == binding.trusted_public_roots
    receipt = binding.authenticator.issue("admission", {"run_id": "durable-run"})
    assert returned.authenticator.verify(receipt, domain="admission") == {"run_id": "durable-run"}
    reverse = returned.authenticator.issue("admission", {"run_id": "durable-run", "worker": True})
    assert binding.authenticator.verify(reverse, domain="admission")["worker"] is True
    with pytest.raises(TrustProtocolError, match="not explicitly provisioned"):
        restored.resolve("another-run")


def test_worker_payload_omits_private_project_archive_cache(
    enrolled: tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration],
) -> None:
    binding, provisioner, configuration = enrolled
    canary = b"worker-must-not-copy-pre-admission-private-project-archive"
    target = binding.intent.target
    assert isinstance(target, FleetPlanTarget)
    target.plan._project_archives["private-only"] = canary
    try:
        payload = capture(provisioner, configuration)
        transport = payload.model_dump_json()
        assert canary.decode() not in transport
        assert canary.hex() not in transport
        restored = restore_fleet_worker_authority(
            FleetWorkerAuthority.model_validate_json(transport), configuration
        )
        returned = restored.resolve("durable-run")
        assert isinstance(returned, FleetPreworkComposition)
        restored_target = returned.intent.target
        assert isinstance(restored_target, FleetPlanTarget)
        assert restored_target.plan._project_archives == {}
        assert returned.intent.model_dump() == binding.intent.model_dump()
    finally:
        target.plan._project_archives.pop("private-only", None)


@pytest.mark.parametrize("kind", ["policy", "strict"])
def test_worker_roundtrip_retains_policy_and_preplacement_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    setup = cast(
        Callable[[Path, pytest.MonkeyPatch], tuple[object, ...]],
        strict_fixture if kind == "strict" else policy_only,
    )
    facts = setup(tmp_path, monkeypatch)
    connection = facts[1]
    lifecycle = facts[2] if kind == "strict" else facts[3]
    assert isinstance(connection, sqlite3.Connection)
    assert isinstance(lifecycle, FleetRunLifecycle)
    provisioner = lifecycle._fleet_provisioner
    assert provisioner is not None
    configuration = configured_executor_pool()
    assert configuration is not None
    try:
        expected = provisioner.resolve("durable-run")
        payload = capture(provisioner, configuration)
        assert payload.kind == kind
        restored = restore_fleet_worker_authority(
            FleetWorkerAuthority.model_validate_json(payload.model_dump_json()), configuration
        )
        actual = restored.resolve("durable-run")
        assert type(actual) is type(expected)
        assert actual.intent == expected.intent
        assert actual.deployment_policy == expected.deployment_policy
        assert actual.configured == expected.configured
        assert actual.trusted_public_roots == expected.trusted_public_roots
    finally:
        connection.close()


@pytest.mark.parametrize("change", ["content", "replacement", "public-root", "revocation"])
def test_worker_restore_rejects_changed_native_authority(
    enrolled: tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration],
    change: str,
) -> None:
    binding, provisioner, configuration = enrolled
    payload = capture(provisioner, configuration)
    inventory = provisioner.executor_inventory_file
    if change == "content":
        inventory.write_text(inventory.read_text().replace("generation = 7", "generation = 8"))
    elif change == "replacement":
        data = inventory.read_bytes()
        inventory.unlink()
        inventory.write_bytes(data)
    elif change == "public-root":
        leaf = provisioner.trusted_key_directory / "source_identity.pub"
        leaf.write_bytes(b"x" * 32)
    else:
        policy = binding.key_directory / "signers.json"
        policy.write_text(policy.read_text().replace('"state":"active"', '"state":"revoked"'))
    with pytest.raises(TrustProtocolError):
        restore_fleet_worker_authority(payload, configuration)


def test_capture_rejects_inventory_outside_resolved_snapshot(
    enrolled: tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration],
) -> None:
    _, provisioner, configuration = enrolled
    expected = _inventory_snapshot(provisioner.executor_inventory_file)[1]
    provisioner.executor_inventory_file.write_text(
        provisioner.executor_inventory_file.read_text() + "\n# changed after parent resolution\n"
    )
    with pytest.raises(TrustProtocolError, match="captured configuration"):
        capture_fleet_worker_authority(
            provisioner, "durable-run", configuration, expected_inventory_identity=expected
        )


def test_worker_key_never_appears_in_repr_or_validation_error() -> None:
    secret = b"a-secret-long-enough-for-native-hmac-key"
    owner = ReceiptAuthenticator(key_id="original-key", secret=secret)
    payload = owner.capture_worker_key()
    assert secret.hex() not in repr(payload)
    assert secret.decode() not in repr(payload)
    with pytest.raises(ValidationError) as failure:
        ProtectedReceiptKey(key_id="original-key", secret_hex=secret.hex().upper())
    assert secret.hex().upper() not in str(failure.value)
    restored = ReceiptAuthenticator.restore_worker_key(payload)
    receipt = owner.issue("admission", {"run_id": "one-run"})
    assert restored.verify(receipt, domain="admission") == {"run_id": "one-run"}


def test_original_provisioner_still_checks_ambient_selection(
    enrolled: tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, provisioner, _ = enrolled
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", "/ambient/other-executors.toml")
    with pytest.raises(TrustProtocolError, match="authoritative selection"):
        provisioner.resolve("durable-run")


def test_captured_provisioner_requires_paired_inventory_authority(
    enrolled: tuple[FleetPreworkComposition, FleetPreworkProvisioner, ExecutorPoolConfiguration],
) -> None:
    _, provisioner, configuration = enrolled
    with pytest.raises(TrustProtocolError, match="must be paired"):
        replace(provisioner, executor_configuration=configuration)
