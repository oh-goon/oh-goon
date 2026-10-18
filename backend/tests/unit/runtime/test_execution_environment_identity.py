"""Group A Phase 2 contract: deployment chooses exactly once; Enforcer will admit later."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_environment import (
    EXECUTION_ENVIRONMENT_SCHEMA,
    ResolvedExecutionEnvironment,
    configured_environment_backend,
)


@pytest.mark.parametrize("backend", ["bubblewrap", "opensandbox"])
def test_exact_configuration_backend(backend: str) -> None:
    assert configured_environment_backend({"environment": {"backend": backend}}) == backend


@pytest.mark.parametrize(
    "configuration",
    [
        None, {}, [], "bubblewrap", {"environment": {}},
        {"environment": ["bubblewrap"]},
        {"environment": {"backend": ["bubblewrap", "opensandbox"]}},
        {"environment": {"backend": "auto"}},
        {"environment": {"backend": "native"}},
        {"environment": {"backend": "docker"}},
        {"environment": {"backend": "Bubblewrap"}},
        {"environment": {"backend": "bubblewrap", "fallback": "opensandbox"}},
        {"environment": {"backend": "bubblewrap"}, "fallback": "opensandbox"},
        {"environment": {"backend": True}},
    ],
)
def test_missing_alias_ordered_and_fallback_selection_fail_closed(configuration: object) -> None:
    with pytest.raises(FleetPlanInvalid):
        configured_environment_backend(configuration)


def _resolved(
    *, backend: str = "bubblewrap", generation: int = 7,
    configuration_id: str = "process-isolation-backend-sha256", capability: bool = True,
    cpu: int = 2000,
) -> ResolvedExecutionEnvironment:
    return ResolvedExecutionEnvironment.from_authorities(
        backend=backend,
        executor_id="worker-a",
        executor_generation=generation,
        backend_configuration_identity=configuration_id,
        capability_statement={"backend_id": backend, "private_filesystem": capability},
        deployment_authority={"cpu_millis": cpu, "path": "/pinned/runtime"},
    )


def test_resolved_identity_is_stable_and_includes_exact_authorities() -> None:
    left = _resolved()
    right = ResolvedExecutionEnvironment.from_authorities(
        executor_generation=7,
        executor_id="worker-a",
        backend_configuration_identity="process-isolation-backend-sha256",
        backend="bubblewrap",
        deployment_authority={"path": "/pinned/runtime", "cpu_millis": 2000},
        capability_statement={"private_filesystem": True, "backend_id": "bubblewrap"},
    )
    assert left == right
    assert left.schema == EXECUTION_ENVIRONMENT_SCHEMA
    assert left.capability_manifest_identity.startswith("sha256:")
    assert left.execution_environment_identity.startswith("sha256:")


@pytest.mark.parametrize(
    "changed",
    [
        {"backend": "opensandbox"}, {"generation": 8},
        {"configuration_id": "changed"}, {"capability": False}, {"cpu": 3000},
    ],
)
def test_security_relevant_drift_requires_new_admission(changed: dict[str, object]) -> None:
    pinned = _resolved()
    fresh = _resolved(**changed)
    assert pinned.execution_environment_identity != fresh.execution_environment_identity
    with pytest.raises(FleetPlanInvalid, match="fresh admission"):
        pinned.require_unchanged(fresh)


def test_mismatched_capabilities_do_not_create_identity() -> None:
    with pytest.raises(FleetPlanInvalid, match="mismatch"):
        ResolvedExecutionEnvironment.from_authorities(
            backend="bubblewrap", executor_id="a", executor_generation=1,
            backend_configuration_identity="b", capability_statement={"backend_id": "opensandbox"},
            deployment_authority={},
        )


def test_revalidation_fails_for_modified_identity_fields() -> None:
    original = _resolved()
    with pytest.raises(FleetPlanInvalid, match="drift"):
        original.require_unchanged(replace(original, capability_manifest_identity="forged"))
    original.require_unchanged(_resolved())


def test_remote_backend_credential_handle_is_identity_not_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from oh_goon.integrations.fleet_plan_opensandbox_isolation import (
        OpenSandboxProcessIsolation,
        OpenSandboxSettings,
    )

    # No native effects: test only identity composition for equivalent settings.
    cli, attestation, key = (tmp_path / name for name in ("osb", "a.json", "a.pem"))
    for path in (cli, attestation, key):
        path.write_bytes(b"identity-only fixture")
    common = dict(
        domain="osb.example", image="registry.example/a@sha256:" + "a" * 64,
        cli_executable=str(cli), runtime_attestation_file=str(attestation),
        runtime_attestation_public_key_file=str(key), runtime="kata",
        protocol="https", api_key="secret-a",
    )
    # A runtime instance would demand real attestation; stub that *only* for this
    # pure identity test to keep provider/network/physical claims absent.
    monkeypatch.setattr(OpenSandboxSettings, "validate", lambda self: None)
    a = OpenSandboxProcessIsolation(OpenSandboxSettings(**common, api_key_env="OSB_A"))
    b = OpenSandboxProcessIsolation(OpenSandboxSettings(**common, api_key_env="OSB_B"))
    assert a.configuration_identity() != b.configuration_identity()
    same = OpenSandboxProcessIsolation(
        OpenSandboxSettings(**(common | {"api_key": "secret-b"}), api_key_env="OSB_A")
    )
    assert a.configuration_identity() == same.configuration_identity()
