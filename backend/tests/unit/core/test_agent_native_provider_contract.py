import json

import pytest
from pydantic import ValidationError

from oh_goon.core.models.agent_providers import (
    AgentProvidersConfig,
    CodexLoginConnection,
    CodexServiceConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
)
from oh_goon.core.models.fleet_plan import AgentPlanStep, ResolvedAgentStep


@pytest.mark.parametrize(
    "workload",
    [
        {"harness": "local-search", "model": "foreign"},
        {"harness": "local-search", "native_agent": {}},
        {"harness": "local-model-search"},
        {"harness": "local-model-review", "model": "selected", "connection": {}},
        {"harness": "provider-review", "model": "selected"},
        {"harness": "native", "model": "foreign"},
        {"harness": "local-model-skill-loop", "model": "selected"},
        {"harness": "unhandled-new-workload"},
    ],
)
def test_resolved_agent_rejects_contradictory_or_unhandled_workloads(
    workload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        ResolvedAgentStep.model_validate(
            {
                "step_id": "agent",
                "timeout_seconds": 30,
                "delivery_sha256": "a" * 64,
                "handoff_identity": "handoff",
                "source_step_id": "agent",
                "workload": workload,
            }
        )


def test_retired_skill_loop_is_refused_at_authoring_before_source_capture() -> None:
    payload = {
        "step_id": "agent",
        "harness": "local-model-skill-resources",
        "task": "Review the source",
        "timeout_seconds": 30,
    }
    assert AgentPlanStep.model_validate(payload).harness == "local-model-skill-resources"
    with pytest.raises(ValidationError, match="harness"):
        AgentPlanStep.model_validate({**payload, "harness": "local-model-skill-loop"})


def test_legacy_flat_agent_payload_cannot_be_admitted_as_current_intent() -> None:
    with pytest.raises(ValidationError, match="workload"):
        ResolvedAgentStep.model_validate(
            {
                "step_id": "agent",
                "timeout_seconds": 30,
                "delivery_sha256": "a" * 64,
                "handoff_identity": "handoff",
                "source_step_id": "agent",
                "argv": ["python"],
                "harness": "local-search",
            }
        )


def test_existing_catalog_wire_stays_unchanged_when_native_selection_is_absent() -> None:
    payload = AgentProvidersConfig().model_dump(mode="json")
    assert "native_agents" not in payload
    assert "native_profiles" not in payload


def test_native_catalog_reuses_resource_policy_and_keeps_native_settings_opaque() -> None:
    config = AgentProvidersConfig.model_validate_json(
        json.dumps(
            {
                "native_agents": {
                    "pi": {"runtime_roots": ["tools"], "arguments": ["--model", "native"]}
                },
                "native_profiles": {
                    "isolated": {
                        "backend": "bubblewrap",
                        "allowed_agents": ["pi"],
                        "workspace_roots": ["work"],
                        "timeout_seconds": 120,
                        "resources": {"memory_mib": 1024},
                    }
                },
            }
        )
    )
    assert config.native_agents["pi"].arguments == ("--model", "native")
    assert config.native_profiles["isolated"].resources.memory_bytes == 1024 * 1024 * 1024


@pytest.mark.parametrize(
    "payload",
    [
        {"native_agents": {"unknown": {}}},
        {"native_profiles": {"bad name": {}}},
        {"native_profiles": {"local": {"inference_profile": "missing"}}},
        {"native_profiles": {"local": {"backend": "docker"}}},
        {"native_profiles": {"local": {"allowed_agents": ["pi", "pi"]}}},
        {"native_profiles": {"local": {"timeout_seconds": True}}},
        {"native_profiles": {"local": {"timeout_seconds": 21601}}},
        {"native_agents": {"pi": {"arguments": ["bad\u0000argument"]}}},
        {"native_agents": {"pi": {"model": "not-owned-here"}}},
    ],
)
def test_invalid_native_policy_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AgentProvidersConfig.model_validate_json(json.dumps(payload))


def test_native_defaults_do_not_request_inference_or_installation() -> None:
    assert NativeAgentDefinition().executable is None
    assert NativeAgentProfile().inference_profile is None
    assert NativeAgentProfile().timeout_seconds == 7200


@pytest.mark.parametrize("conflict", ["bubblewrap", "broker"])
def test_codex_login_never_implicitly_downgrades_broker_policy(conflict: str) -> None:
    profile: dict[str, object] = {
        "codex_login": {
            "home": "/native/auth",
            "model": "gpt-6-luna",
            "reasoning_effort": "low",
        }
    }
    if conflict == "bubblewrap":
        profile["backend"] = "bubblewrap"
    else:
        profile["inference_profile"] = "api"
    with pytest.raises(ValidationError):
        NativeAgentProfile.model_validate(profile)


@pytest.mark.parametrize(
    "effort",
    ["", " \t\r\n", "ultra\x00", "x" * 257],
    ids=["empty", "whitespace", "nul", "oversized"],
)
def test_codex_login_rejects_malformed_native_effort(effort: str) -> None:
    with pytest.raises(ValidationError):
        CodexLoginConnection.model_validate_json(
            json.dumps(
                {
                    "home": "/native/auth",
                    "model": "gpt-6.1-sol",
                    "reasoning_effort": effort,
                }
            )
        )


def test_explicit_backend_sandbox_does_not_authorize_native_file_access() -> None:
    assert "sandbox_owner" not in NativeAgentProfile().model_dump(mode="json")
    profile = NativeAgentProfile(backend="bubblewrap", sandbox_owner="execution_backend")
    assert profile.model_dump(mode="json")["sandbox_owner"] == "execution_backend"


@pytest.mark.parametrize("credential_store", ["file", "native"])
def test_native_login_rejects_credential_file_selection(credential_store: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        CodexLoginConnection.model_validate(
            {
                "home": "/native/auth",
                "model": "native",
                "reasoning_effort": "ultra",
                "credential_store": credential_store,
            }
        )


@pytest.mark.parametrize("owner", ["harness", "execution_backend"])
def test_protected_sign_in_requires_service_owned_authentication(owner: str) -> None:
    with pytest.raises(ValidationError, match="qualified service connection"):
        NativeAgentProfile.model_validate(
            {
                "backend": "bubblewrap",
                "sandbox_owner": owner,
                "codex_login": {
                    "home": "/native/auth",
                    "model": "native",
                    "reasoning_effort": "ultra",
                },
            }
        )


def test_codex_service_selects_public_native_settings_without_changing_existing_wire() -> None:
    assert "codex_service" not in NativeAgentProfile().model_dump(mode="json")
    service = CodexServiceConnection(
        home="/private/native", model="native-model", reasoning_effort=" native-effort "
    )
    profile = NativeAgentProfile(codex_service=service)
    assert profile.backend == "local" and profile.sandbox_owner == "harness"
    assert profile.model_dump(mode="json")["codex_service"] == {
        "home": "/private/native",
        "model": "native-model",
        "reasoning_effort": " native-effort ",
    }


@pytest.mark.parametrize("field", ["model", "reasoning_effort"])
@pytest.mark.parametrize("value", ["", " \t\r\n", "bad\x00value", "x" * 257, float("inf")])
def test_codex_service_rejects_unbounded_or_empty_native_settings(
    field: str, value: object
) -> None:
    payload = {
        "home": "/private/native",
        "model": "native",
        "reasoning_effort": "ultra",
        field: value,
    }
    with pytest.raises(ValidationError):
        CodexServiceConnection.model_validate(payload)


@pytest.mark.parametrize("private_input", ["configuration_directory", "socket", "token"])
def test_codex_service_cannot_select_native_stores_or_an_unowned_endpoint(
    private_input: str,
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs"):
        CodexServiceConnection.model_validate(
            {
                "home": "/private/native",
                "model": "native",
                "reasoning_effort": "ultra",
                private_input: "/private/native",
            }
        )


@pytest.mark.parametrize("conflict", ["login", "broker", "bubblewrap", "backend-owner"])
def test_codex_service_requires_one_explicit_native_connection_and_sandbox(conflict: str) -> None:
    payload: dict[str, object] = {
        "codex_service": {"home": "/private/native", "model": "native", "reasoning_effort": "ultra"}
    }
    if conflict == "login":
        payload["codex_login"] = {
            "home": "/private/native",
            "model": "native",
            "reasoning_effort": "ultra",
        }
    elif conflict == "broker":
        payload["inference_profile"] = "managed"
    elif conflict == "bubblewrap":
        payload["backend"] = "bubblewrap"
    else:
        payload.update(backend="bubblewrap", sandbox_owner="execution_backend")
    with pytest.raises(ValidationError, match="Codex service"):
        NativeAgentProfile.model_validate(payload)


def test_codex_service_requires_the_native_owned_directory_scope() -> None:
    with pytest.raises(ValidationError, match="home"):
        CodexServiceConnection.model_validate({"model": "native", "reasoning_effort": "ultra"})
