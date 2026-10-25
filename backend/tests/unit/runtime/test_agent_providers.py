import json
import tomllib
from pathlib import Path

import pytest

from oh_goon.core.models.agent_providers import AgentProviderError
from oh_goon.runtime.agent_providers import agent_providers_path, load_agent_providers

CONFIG = """version = 1
default_model = "review"
[providers.gateway]
api = "openai-chat-completions"
base_url = "https://gateway.example/v1/"
api_key_env = "MY_API_KEY"
headers = { "X-Title" = "Oh-Goon" }
headers_from_env = { "X-Workspace" = "WORKSPACE_TOKEN" }
[providers.local]
api = "ollama"
base_url = "http://localhost:11434"
[models.review]
provider = "gateway"
model = "org/model-name:revision"
max_output_tokens = 700
parameters = { temperature = 0.2 }
[models.offline]
provider = "local"
model = "my-model:latest"
"""


def load(tmp_path, content=CONFIG):
    path = tmp_path / "providers.toml"
    path.write_text(content)
    return load_agent_providers(path)


def test_catalog_is_offline_and_only_selected_credentials_are_required(tmp_path):
    config = load(tmp_path)
    local = config.resolve("offline", environment={})
    assert local.model == "my-model:latest"
    assert local.headers == ()
    with pytest.raises(AgentProviderError, match="MY_API_KEY"):
        config.resolve(None, environment={"WORKSPACE_TOKEN": "workspace"})
    selected = config.resolve(
        None, environment={"MY_API_KEY": "secret", "WORKSPACE_TOKEN": "workspace-secret"}
    )
    assert selected.profile == "review" and selected.provider_name == "gateway"
    assert selected.provider.base_url == "https://gateway.example/v1"
    assert selected.max_output_tokens == 700
    assert b'"temperature": 0.2' in selected.parameters_json
    assert "workspace-secret" not in repr(selected)
    assert "Bearer secret" not in repr(selected)


def test_selection_is_a_snapshot_of_config_and_credentials(tmp_path):
    config = load(tmp_path)
    environment = {"MY_API_KEY": "original", "WORKSPACE_TOKEN": "workspace"}
    selected = config.resolve("review", environment=environment)
    environment["MY_API_KEY"] = "changed"
    config.models["review"].parameters["temperature"] = 1.0
    config.providers["gateway"].headers["X-Title"] = "changed"
    headers = {name: value.get_secret_value() for name, value in selected.headers}
    assert headers["Authorization"] == "Bearer original"
    assert headers["X-Title"] == "Oh-Goon"
    assert b'"temperature": 0.2' in selected.parameters_json


@pytest.mark.parametrize(
    "content",
    [
        CONFIG.replace("version = 1", "version = true"),
        CONFIG.replace("version = 1", "version = 2"),
        CONFIG.replace('default_model = "review"', 'default_model = "missing"'),
        CONFIG.replace('provider = "gateway"', 'provider = "missing"'),
        CONFIG.replace('api = "ollama"', 'api = "made-up-protocol"'),
        CONFIG.replace(
            "https://gateway.example/v1/", "https://user:PRIVATE_SECRET@gateway.example/v1"
        ),
        CONFIG.replace("https://gateway.example/v1/", "file:///etc/passwd"),
        CONFIG.replace(
            "https://gateway.example/v1/", "https://gateway.example/v1?key=PRIVATE_SECRET"
        ),
        CONFIG.replace("https://gateway.example/v1/", "https://gateway.example/v1#PRIVATE_SECRET"),
        CONFIG.replace('api_key_env = "MY_API_KEY"', 'api_key = "PRIVATE_SECRET"'),
        CONFIG.replace('"X-Title" = "Oh-Goon"', '"Authorization" = "PRIVATE_SECRET"'),
        CONFIG.replace('"X-Title" = "Oh-Goon"', '"Content-Length" = "500"'),
        CONFIG.replace('"X-Workspace" = "WORKSPACE_TOKEN"', '"authorization" = "ANOTHER_KEY"'),
        CONFIG.replace("temperature = 0.2", "stream = true"),
        CONFIG.replace("temperature = 0.2", 'model = "other-model"'),
        CONFIG.replace("temperature = 0.2", "temperature = nan"),
        CONFIG.replace("version = 1", "version = 1\nversion = 1"),
    ],
)
def test_invalid_config_fails_without_exposing_accidentally_pasted_secrets(tmp_path, content):
    with pytest.raises(AgentProviderError) as error:
        load(tmp_path, content)
    assert "PRIVATE_SECRET" not in str(error.value)


def test_missing_and_oversized_configuration_are_explicit_errors(tmp_path):
    with pytest.raises(AgentProviderError, match="Cannot read"):
        load_agent_providers(tmp_path / "missing.toml")
    with pytest.raises(AgentProviderError, match="exceeds"):
        load(tmp_path, " " * 262_145)


def test_unknown_model_and_header_injection_are_rejected(tmp_path):
    config = load(tmp_path)
    with pytest.raises(AgentProviderError, match="configured model profile"):
        config.resolve("missing", environment={})
    with pytest.raises(AgentProviderError, match="header value") as error:
        config.resolve(
            None,
            environment={"MY_API_KEY": "PRIVATE_SECRET\r\nX-Evil: yes", "WORKSPACE_TOKEN": "w"},
        )
    assert "PRIVATE_SECRET" not in str(error.value)


def test_provider_path_precedence_is_independent_of_database_and_checkout(tmp_path):
    environment = {
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "OH_GOON_DB": str(tmp_path / "database.sqlite"),
    }
    assert agent_providers_path(environment) == tmp_path / "config/oh-goon/providers.json"
    environment["OH_GOON_PROVIDERS_FILE"] = str(tmp_path / "team.toml")
    assert agent_providers_path(environment) == tmp_path / "team.toml"
    assert (
        agent_providers_path(environment, tmp_path / "explicit.toml") == tmp_path / "explicit.toml"
    )
    assert not (tmp_path / "config").exists()


def test_empty_xdg_setting_keeps_user_config_outside_the_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert (
        agent_providers_path({"XDG_CONFIG_HOME": ""}) == tmp_path / ".config/oh-goon/providers.json"
    )


def test_relative_ca_bundle_is_resolved_at_configuration_load(tmp_path, monkeypatch):
    config = load(
        tmp_path,
        CONFIG.replace(
            'api_key_env = "MY_API_KEY"', 'api_key_env = "MY_API_KEY"\nca_file = "certs/team.pem"'
        ),
    )
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    assert config.providers["gateway"].ca_file == str(tmp_path / "certs/team.pem")


def test_shipped_example_loads_without_provider_packages_or_credentials():
    root = Path(__file__).resolve().parents[4]
    config = load_agent_providers(root / "examples/agent-providers/providers.toml")
    assert set(config.providers) == {"ollama", "litellm", "openrouter", "openai", "anthropic"}
    assert config.resolve("local", environment={}).provider.api == "ollama"
    with pytest.raises(AgentProviderError, match="configured model profile"):
        config.resolve(None, environment={})


def test_json_and_toml_share_one_provider_contract(tmp_path):
    toml = load(tmp_path)
    payload = tomllib.loads(CONFIG)
    payload["$schema"] = "./providers.schema.json"
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(payload))
    native = load_agent_providers(path)
    assert native.providers == toml.providers
    assert native.models == toml.models
    assert native.resolve("offline", environment={}) == toml.resolve("offline", environment={})
    path.write_text('{"version":1,"version":1,"PRIVATE_SECRET":"do not disclose"}')
    with pytest.raises(AgentProviderError, match="unique keys") as error:
        load_agent_providers(path)
    assert "PRIVATE_SECRET" not in str(error.value)
