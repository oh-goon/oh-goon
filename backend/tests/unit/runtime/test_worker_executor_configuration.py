from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
    OpenSandboxProcessIsolation,
    OpenSandboxSettings,
    OpenSandboxWorkerConfiguration,
)
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
    WorkerExecutorPoolConfiguration,
    capture_worker_executor_configuration,
    capture_worker_isolation_configuration,
    restore_worker_executor_configuration,
    restore_worker_isolation_configuration,
)
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
from tests.factories.path_authority import TEST_PATH_AUTHORITY


def _backend(tmp_path: Path) -> BubblewrapProcessIsolation:
    backend = BubblewrapProcessIsolation(
        executable=sys.executable,
        control_state_root=tmp_path / "control",
        require_local_resource_intent=True,
        standard_resource_executable=sys.executable,
        standard_resource_query_executable=sys.executable,
        standard_resource_environment={"XDG_RUNTIME_DIR": "/original/runtime"},
    )
    backend.bind_application_state(
        private_paths=(tmp_path / "app.sqlite",),
        read_only_paths=(tmp_path / "extensions", tmp_path / "tools"),
    )
    return backend


def test_worker_rebinds_exact_resolved_inventory_without_ambient_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path)
    original = ExecutorPoolConfiguration(
        placement_wait_seconds=17,
        placement_queue_limit=23,
        path_authority=TEST_PATH_AUTHORITY,
        executors=(
            ConfiguredFleetExecutor(
                priority=7,
                backend=backend,
                capacity=ExecutorCapacitySettings(
                    executor=FleetExecutorIdentity(executor_id="selected-local", generation=3),
                    cpu_millis=2000,
                    memory_mib=512,
                    max_processes=64,
                ),
            ),
        ),
    )
    captured = capture_worker_executor_configuration(original)
    assert captured is not None
    transported = WorkerExecutorPoolConfiguration.model_validate_json(captured.model_dump_json())
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/unrelated/runtime")
    restored = restore_worker_executor_configuration(transported)
    assert restored is not None
    assert restored.path_authority == original.path_authority
    assert restored.placement_wait_seconds == 17 and restored.placement_queue_limit == 23
    assert len(restored.executors) == 1
    actual = restored.executors[0]
    assert actual.priority == 7 and actual.capacity == original.executors[0].capacity
    assert type(actual.backend) is BubblewrapProcessIsolation
    assert actual.backend is not backend
    assert actual.backend.capture_worker_configuration() == backend.capture_worker_configuration()


def test_worker_rejects_backend_executable_replacement(tmp_path: Path) -> None:
    executable = tmp_path / "bwrap"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    backend = BubblewrapProcessIsolation(
        executable=str(executable), control_state_root=tmp_path / "control"
    )
    captured = capture_worker_isolation_configuration(backend)
    executable.write_text("#!/bin/sh\nexit 1\n")
    with pytest.raises(FleetPlanInvalid, match="changed after capture"):
        restore_worker_isolation_configuration(captured)


def test_worker_rejects_retargeted_private_application_path(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    captured = capture_worker_isolation_configuration(backend)
    foreign = tmp_path / "another.sqlite"
    foreign.write_bytes(b"unrelated private source")
    (tmp_path / "app.sqlite").symlink_to(foreign)
    with pytest.raises(FleetPlanInvalid, match="changed after capture"):
        restore_worker_isolation_configuration(captured)


def test_worker_refuses_injected_backend_without_replacing_its_semantics(tmp_path: Path) -> None:
    class CustomBackend(BubblewrapProcessIsolation):
        pass

    custom = CustomBackend(executable=sys.executable, control_state_root=tmp_path / "control")
    with pytest.raises(FleetPlanInvalid, match="injected process-isolation owner"):
        capture_worker_isolation_configuration(custom)


def test_unconfigured_executor_pool_stays_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OH_GOON_EXECUTORS_FILE", str(tmp_path / "missing.toml"))
    assert capture_worker_executor_configuration(None) is None
    assert restore_worker_executor_configuration(None) is None


@pytest.fixture
def remote_backend(tmp_path: Path) -> OpenSandboxProcessIsolation:
    executable = tmp_path / "osb"
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    key = Ed25519PrivateKey.generate()
    public_key = tmp_path / "attestation.pem"
    public_key.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    claims = {
        "schema": "oh-goon.opensandbox-runtime-attestation.v1",
        "domain": "worker.example",
        "protocol": "https",
        "secure_runtime": "kata",
        "server_configuration_sha256": "a" * 64,
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    canonical = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    attestation = tmp_path / "attestation.json"
    attestation.write_text(
        json.dumps(
            {
                "claims": claims,
                "signature": base64.b64encode(key.sign(canonical)).decode("ascii"),
            }
        )
    )
    return OpenSandboxProcessIsolation(
        OpenSandboxSettings(
            domain="worker.example",
            api_key="worker-secret-canary",
            api_key_env="OH_GOON_WORKER_TEST_KEY",
            image="runner@sha256:" + "a" * 64,
            cli_executable=str(executable),
            runtime_attestation_file=str(attestation),
            runtime_attestation_public_key_file=str(public_key),
            runtime="kata",
            protocol="https",
        )
    )


def test_remote_worker_retains_exact_credential_and_verified_backend(
    remote_backend: OpenSandboxProcessIsolation, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = remote_backend.capture_worker_configuration()
    transported = OpenSandboxWorkerConfiguration.model_validate_json(captured.model_dump_json())
    monkeypatch.setenv("OH_GOON_WORKER_TEST_KEY", "unrelated-ambient-value")
    restored = OpenSandboxProcessIsolation.from_worker_configuration(transported)
    assert restored.settings == remote_backend.settings
    assert restored.settings.api_key == "worker-secret-canary"
    assert restored.configuration_identity() == captured.configuration_identity
    assert "worker-secret-canary" not in repr(captured)
    invalid = captured.model_dump(mode="json") | {"kind": "unavailable"}
    with pytest.raises(ValidationError) as rejected:
        OpenSandboxWorkerConfiguration.model_validate(invalid)
    assert "worker-secret-canary" not in str(rejected.value)


def test_remote_worker_refuses_replaced_native_cli(
    remote_backend: OpenSandboxProcessIsolation,
) -> None:
    captured = remote_backend.capture_worker_configuration()
    Path(remote_backend.settings.cli_executable).write_text("#!/bin/sh\nexit 1\n")
    with pytest.raises(FleetPlanInvalid, match="changed after capture"):
        OpenSandboxProcessIsolation.from_worker_configuration(captured)
