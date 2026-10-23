import json

import pytest
from pydantic import ValidationError

from oh_goon.core.models.agent_providers import (
    AgentProvidersConfig,
    CodexLoginConnection,
    NativeAgentDefinition,
    NativeAgentProfile,
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
