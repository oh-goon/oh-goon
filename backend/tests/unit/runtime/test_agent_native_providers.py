import hashlib
import json
from pathlib import Path

import pytest

from oh_goon.core.models.agent_providers import (
    AgentProviderError,
    AgentProvidersConfig,
    NativeAgentDefinition,
    NativeAgentProfile,
)
from oh_goon.integrations.agent_native_execution import verify_native_agent_executable
from oh_goon.runtime.agent_providers import (
    load_agent_providers,
    resolve_native_agent,
)


def executable(path: Path) -> Path:
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def test_codex_login_freezes_only_explicit_public_connection_facts(tmp_path: Path) -> None:
    home = tmp_path / "native-home"
    home.mkdir()
    # A special file would hang an attempted credential read. Resolution must
    # only resolve the home; native Codex retains all authentication semantics.
    import os

    os.mkfifo(home / "auth.json")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    executable(tmp_path / "codex")
    catalog = tmp_path / "providers.json"
    catalog.write_text(
        json.dumps(
            {
                "native_agents": {"codex": {"executable": "codex"}},
                "native_profiles": {
                    "signed-in": {
                        "backend": "local",
                        "codex_login": {
                            "home": "native-home",
                            "model": "gpt-6-luna",
                            "reasoning_effort": "low",
                        },
                    }
                },
            }
        )
    )
    config = load_agent_providers(catalog)
    catalog.unlink()
    selected = resolve_native_agent(
        config, "codex", "signed-in", workspace=workspace, environment={}
    )
    assert selected.connection is None
    assert selected.disclosure_recipients == ("codex",)
    assert selected.profile.codex_login is not None
    assert selected.profile.codex_login.home == str(home)
    assert selected.profile.codex_login.model == "gpt-6-luna"
    assert selected.profile.codex_login.reasoning_effort == "low"


@pytest.mark.parametrize("invalid", ["agent", "arguments", "configuration", "workspace"])
def test_codex_login_rejects_conflicting_native_authority(tmp_path: Path, invalid: str) -> None:
    home = tmp_path / "native-home"
    home.mkdir()
    workspace = tmp_path / "repo"
    workspace.mkdir()
    agent = "pi" if invalid == "agent" else "codex"
    program = executable(tmp_path / agent)
    definition: dict[str, object] = {"executable": str(program)}
    if invalid == "arguments":
        definition["arguments"] = ["--model", "different-model"]
    if invalid == "configuration":
        definition["configuration_directory"] = str(home)
    config = AgentProvidersConfig.model_validate(
        {
            "native_agents": {agent: definition},
            "native_profiles": {
                "signed-in": {
                    "codex_login": {
                        "home": str(workspace if invalid == "workspace" else home),
                        "model": "gpt-6-luna",
                        "reasoning_effort": "low",
                    }
                }
            },
        }
    )
    with pytest.raises(AgentProviderError, match="Codex"):
        resolve_native_agent(config, agent, "signed-in", workspace=workspace, environment={})


def test_os_lookup_is_explicit_and_frozen_executable_is_verified(tmp_path: Path) -> None:
    program = executable(tmp_path / "pi")
    selected = resolve_native_agent(
        AgentProvidersConfig(),
        "pi",
        "local",
        workspace=tmp_path,
        environment={"PATH": str(tmp_path)},
    )
    assert selected.definition.executable == str(program)
    assert selected.executable_sha256 == hashlib.sha256(program.read_bytes()).hexdigest()
    assert selected.connection is None and selected.profile.backend == "local"
    verify_native_agent_executable(selected)
    program.write_bytes(b"#!/bin/sh\nexit 1\n")
    with pytest.raises(AgentProviderError, match="resolved identity"):
        verify_native_agent_executable(selected)


def test_catalog_paths_are_resolved_once_without_reading_native_settings(tmp_path: Path) -> None:
    executable(tmp_path / "native")
    configuration = tmp_path / "public-settings"
    configuration.mkdir()
    (configuration / "settings.json").write_text("NOT OH-GOON JSON")
    catalog = tmp_path / "providers.json"
    catalog.write_text(
        json.dumps(
            {
                "native_agents": {
                    "pi": {
                        "executable": "native",
                        "configuration_directory": "public-settings",
                        "runtime_roots": ["public-settings"],
                    }
                },
                "native_profiles": {"work": {"allowed_agents": ["pi"], "workspace_roots": ["."]}},
            }
        )
    )
    config = load_agent_providers(catalog)
    catalog.unlink()
    selected = resolve_native_agent(config, "pi", "work", workspace=tmp_path, environment={})
    assert selected.definition.configuration_directory == str(configuration)
    assert selected.definition.runtime_roots == (str(configuration),)
    assert selected.profile.workspace_roots == (str(tmp_path),)


def test_native_inference_selects_public_snapshot_without_reading_credentials(
    tmp_path: Path,
) -> None:
    program = executable(tmp_path / "pi")
    config = AgentProvidersConfig.model_validate(
        {
            "providers": {
                "host": {
                    "api": "openai-chat-completions",
                    "base_url": "https://example.com/v1",
                    "api_key_env": "NO_SUCH_SECRET",
                }
            },
            "models": {"approved": {"provider": "host", "model": "exact-native-model"}},
            "native_agents": {"pi": {"executable": str(program)}},
            "native_profiles": {"managed": {"inference_profile": "approved"}},
        }
    )
    selected = resolve_native_agent(config, "pi", "managed", workspace=tmp_path, environment={})
    assert selected.connection is not None
    assert selected.connection.selection.model == "exact-native-model"
    config.providers["host"].headers["X-Changed"] = "later"
    assert selected.connection.provider.headers == {}


def test_profile_rejects_escape_through_workspace_symlink(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = approved / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    config = AgentProvidersConfig(
        native_profiles={"local": NativeAgentProfile(workspace_roots=(str(approved),))}
    )
    with pytest.raises(AgentProviderError, match="outside"):
        resolve_native_agent(config, "pi", "local", workspace=alias, environment={})


@pytest.mark.parametrize(
    "agent,profile", [("unknown", "local"), ("pi", "missing"), ("pi", "local")]
)
def test_unknown_or_absent_native_selection_has_no_fallback(
    tmp_path: Path, agent: str, profile: str
) -> None:
    with pytest.raises(AgentProviderError):
        resolve_native_agent(
            AgentProvidersConfig(), agent, profile, workspace=tmp_path, environment={}
        )


def test_disallowed_native_agent_rejects_before_executable_discovery(tmp_path: Path) -> None:
    config = AgentProvidersConfig(
        native_profiles={"local": NativeAgentProfile(allowed_agents=("codex",))}
    )
    with pytest.raises(AgentProviderError, match="not allowed"):
        resolve_native_agent(config, "pi", "local", workspace=tmp_path, environment={})


def test_offline_default_selects_existing_bubblewrap_backend(tmp_path: Path) -> None:
    program = executable(tmp_path / "pi")
    config = AgentProvidersConfig(
        native_agents={"pi": NativeAgentDefinition(executable=str(program))}
    )
    selected = resolve_native_agent(
        config, "pi", "isolated-offline", workspace=tmp_path, environment={}
    )
    assert selected.profile.backend == "bubblewrap"
    assert selected.connection is None


def test_managed_default_uses_explicit_default_model_once(tmp_path: Path) -> None:
    program = executable(tmp_path / "pi")
    config = AgentProvidersConfig.model_validate(
        {
            "default_model": "approved",
            "providers": {
                "host": {"api": "openai-chat-completions", "base_url": "http://127.0.0.1:12345/v1"}
            },
            "models": {"approved": {"provider": "host", "model": "native-model"}},
            "native_agents": {"pi": {"executable": str(program)}},
        }
    )
    selected = resolve_native_agent(
        config, "pi", "isolated-managed", workspace=tmp_path, environment={}
    )
    assert selected.profile.backend == "bubblewrap"
    assert selected.profile.inference_profile == "approved"
    assert selected.connection is not None and selected.connection.selection.model == "native-model"
    config.models["approved"].parameters["later"] = True
    assert selected.connection.selection.parameters == {}


def test_managed_default_without_model_does_not_discover_or_choose_one(tmp_path: Path) -> None:
    program = executable(tmp_path / "pi")
    config = AgentProvidersConfig(
        native_agents={"pi": NativeAgentDefinition(executable=str(program))}
    )
    selected = resolve_native_agent(
        config, "pi", "isolated-managed", workspace=tmp_path, environment={}
    )
    assert selected.profile.backend == "bubblewrap" and selected.connection is None


def test_explicit_named_profile_is_not_replaced_by_managed_builtin(tmp_path: Path) -> None:
    program = executable(tmp_path / "pi")
    profile = NativeAgentProfile(backend="local", allowed_agents=("pi",), timeout_seconds=123)
    config = AgentProvidersConfig(
        native_agents={"pi": NativeAgentDefinition(executable=str(program))},
        native_profiles={"isolated-managed": profile},
    )
    selected = resolve_native_agent(
        config, "pi", "isolated-managed", workspace=tmp_path, environment={}
    )
    assert selected.profile == profile


def test_nonexecutable_native_file_is_not_admitted(tmp_path: Path) -> None:
    program = tmp_path / "pi"
    program.write_text("not executable")
    config = AgentProvidersConfig(
        native_agents={"pi": NativeAgentDefinition(executable=str(program))}
    )
    with pytest.raises(AgentProviderError, match="unavailable"):
        resolve_native_agent(config, "pi", "local", workspace=tmp_path, environment={})


@pytest.mark.parametrize("arguments", [("--api-key", "PRIVATE-KEY"), ("--api-key=PRIVATE-KEY",)])
def test_pi_literal_credentials_reject_before_executable_discovery(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    config = AgentProvidersConfig(native_agents={"pi": NativeAgentDefinition(arguments=arguments)})
    with pytest.raises(AgentProviderError, match="credentials") as captured:
        resolve_native_agent(config, "pi", "local", workspace=tmp_path, environment={})
    assert "PRIVATE-KEY" not in str(captured.value)
