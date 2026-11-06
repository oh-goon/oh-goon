import json
import tomllib
from pathlib import Path

import pytest

from oh_goon.core.models.agent_providers import (
    AgentConnection,
    AgentProviderError,
    AgentProvidersConfig,
)
from oh_goon.core.models.fleet_plan import (
    ResolvedAgentStep,
    ResolvedFleetPlan,
    ResolvedProviderAgentWorkload,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
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


def load(
    tmp_path: Path, content: str = CONFIG, *, filename: str = "providers.toml"
) -> AgentProvidersConfig:
    path = tmp_path / filename
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


def test_provider_history_permission_defaults_to_denial_and_survives_selected_transport(
    tmp_path: Path,
) -> None:
    config = load(tmp_path)
    default = AgentConnection.select(config, "review")
    assert default.selection.allow_provider_history is False
    assert "allow_provider_history" not in default.model_dump(mode="json")["selection"]
    assert (
        default.resolve(
            {"MY_API_KEY": "synthetic", "WORKSPACE_TOKEN": "workspace"}
        ).allow_provider_history
        is False
    )

    explicit = load(
        tmp_path,
        CONFIG.replace(
            "max_output_tokens = 700", "max_output_tokens = 700\nallow_provider_history = true"
        ),
    )
    admitted = AgentConnection.select(explicit, "review")
    transported = AgentConnection.model_validate_json(admitted.model_dump_json())
    explicit.models["review"] = config.models["review"]
    selected = transported.resolve({"MY_API_KEY": "synthetic", "WORKSPACE_TOKEN": "workspace"})
    assert transported.selection.allow_provider_history is True
    assert selected.allow_provider_history is True


@pytest.mark.parametrize(
    "reference",
    [
        {"previous_response_id": "PRIVATE_HISTORY_REFERENCE"},
        {"conversation": "PRIVATE_HISTORY_REFERENCE"},
        {"conversation": {"id": "PRIVATE_HISTORY_REFERENCE"}},
    ],
)
def test_declared_history_parameters_require_explicit_authorization_before_selection(
    tmp_path: Path, reference: dict[str, object]
) -> None:
    payload = tomllib.loads(CONFIG)
    payload["models"]["review"]["parameters"] = reference
    config = load(tmp_path, json.dumps(payload), filename="providers.json")
    # Passive decoding preserves evidence; current execution still requires a grant.
    recorded = AgentConnection(
        profile="review",
        provider_name="gateway",
        provider=config.providers["gateway"],
        selection=config.models["review"],
    )
    decoded = AgentConnection.model_validate_json(recorded.model_dump_json())
    assert decoded.selection.parameters == reference
    with pytest.raises(AgentProviderError) as refused:
        AgentConnection.select(config, "review")
    with pytest.raises(AgentProviderError, match="allow_provider_history"):
        decoded.resolve({})
    assert "PRIVATE_HISTORY_REFERENCE" not in str(refused.value)
    assert "allow_provider_history" in str(refused.value)
    payload["models"]["review"]["allow_provider_history"] = True
    selected = load(tmp_path, json.dumps(payload), filename="providers.json").resolve(
        "review", environment={"MY_API_KEY": "synthetic", "WORKSPACE_TOKEN": "workspace"}
    )
    assert selected.allow_provider_history is True
    assert json.loads(selected.parameters_json) == reference


@pytest.mark.parametrize(
    "reference", [{"conversation": {}}, {"previous_response_id": ""}, {"conversation": False}]
)
def test_nonnull_history_values_cannot_bypass_default_denial_by_being_falsey(
    tmp_path: Path, reference: dict[str, object]
) -> None:
    payload = tomllib.loads(CONFIG)
    payload["models"]["review"]["parameters"] = reference
    config = load(tmp_path, json.dumps(payload), filename="providers.json")
    with pytest.raises(AgentProviderError):
        AgentConnection.select(config, "review")
    with pytest.raises(AgentProviderError):
        config.resolve("review", environment={})


def test_mutated_parameters_cannot_cross_selection_or_credential_resolution(tmp_path: Path) -> None:
    config = load(tmp_path)
    config.models["review"].parameters["previous_response_id"] = "PRIVATE_HISTORY_REFERENCE"
    with pytest.raises(AgentProviderError, match="allow_provider_history"):
        AgentConnection.select(config, "review")
    with pytest.raises(AgentProviderError, match="allow_provider_history"):
        config.resolve("review", environment={})


def test_request_parameters_cannot_authorize_history_even_in_an_authorized_model(
    tmp_path: Path,
) -> None:
    payload = tomllib.loads(CONFIG)
    payload["models"]["review"].update(
        allow_provider_history=True, parameters={"allow_provider_history": True}
    )
    config = load(tmp_path, json.dumps(payload), filename="providers.json")
    with pytest.raises(AgentProviderError):
        AgentConnection.select(config, "review")
    with pytest.raises(AgentProviderError):
        config.resolve("review", environment={})


def test_no_grant_preserves_resolved_plan_identity_and_true_binds_a_new_identity(
    tmp_path: Path,
) -> None:
    connection = AgentConnection.select(load(tmp_path), "review")

    def resolve(selected: AgentConnection) -> ResolvedFleetPlan:
        return ResolvedFleetPlan.create(
            name="recorded-review",
            description="Recorded execution policy",
            execution=MaintenanceExecutionPolicy(),
            steps=(
                ResolvedAgentStep(
                    step_id="review",
                    timeout_seconds=10,
                    workload=ResolvedProviderAgentWorkload(
                        harness="provider-review", connection=selected
                    ),
                    delivery_sha256="a" * 64,
                    handoff_identity="frozen-source",
                    source_step_id="review",
                ),
            ),
        )

    plan = resolve(connection)
    wire = plan.model_dump(mode="json")
    assert '"allow_provider_history"' not in json.dumps(wire)
    assert ResolvedFleetPlan.model_validate_json(json.dumps(wire)).plan_id == plan.plan_id

    explicit_false = connection.model_copy(
        update={
            "selection": connection.selection.model_copy(update={"allow_provider_history": False})
        }
    )
    same = resolve(explicit_false)
    assert same.plan_id == plan.plan_id
    assert same.model_dump(mode="json") == wire

    granted = connection.model_copy(
        update={
            "selection": connection.selection.model_copy(update={"allow_provider_history": True})
        }
    )
    authorized = resolve(granted)
    assert authorized.plan_id != plan.plan_id
    assert '"allow_provider_history": true' in authorized.model_dump_json(indent=2)
    assert (
        ResolvedFleetPlan.model_validate_json(authorized.model_dump_json()).plan_id
        == authorized.plan_id
    )


@pytest.mark.parametrize(
    "content",
    [
        CONFIG.replace("version = 1", "version = true"),
        CONFIG.replace(
            "max_output_tokens = 700", "max_output_tokens = 700\nallow_provider_history = 1"
        ),
        CONFIG.replace(
            "max_output_tokens = 700", 'max_output_tokens = 700\nallow_provider_history = "true"'
        ),
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
