import json
from io import BytesIO
from types import SimpleNamespace

import pytest

from oh_goon.cli import main
from oh_goon.core.models.agent_providers import AgentProviderError, AgentReply

CONFIG = """version = 1
default_model = "review"
[providers.gateway]
api = "openai-responses"
base_url = "https://gateway.example/v1"
api_key_env = "MY_KEY"
headers_from_env = { "X-Token" = "MY_OTHER_KEY" }
[models.review]
provider = "gateway"
model = "selected-model"
"""


@pytest.fixture
def no_runtime(monkeypatch):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("provider commands must not create Fleet/SQLite"),
    )


@pytest.mark.parametrize("command", ["list", "validate"])
def test_provider_discovery_is_offline_and_does_not_require_credentials(
    tmp_path, monkeypatch, capsys, no_runtime, command
):
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    monkeypatch.setattr(
        AgentProviderClient,
        "complete",
        lambda *args, **kwargs: pytest.fail("configuration must not connect"),
    )
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.delenv("MY_OTHER_KEY", raising=False)
    path = tmp_path / "providers.toml"
    path.write_text(CONFIG)
    assert main(["agent", "providers", command, "--config", str(path), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "configured_not_connected"
    assert report["models"]["review"]["model"] == "selected-model"
    assert report["providers"]["gateway"]["api_key_env"] == "MY_KEY"


def test_path_does_not_read_or_create_configuration(tmp_path, monkeypatch, capsys, no_runtime):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["agent", "providers", "path", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["config"] == str(tmp_path / "oh-goon/providers.toml")
    assert not (tmp_path / "oh-goon").exists()


@pytest.mark.parametrize("source", ["argument", "file", "stdin"])
def test_explicit_request_uses_selected_profile_and_bounded_prompt(
    tmp_path, monkeypatch, capsys, no_runtime, source
):
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    path = tmp_path / "providers.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("MY_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("MY_OTHER_KEY", "OTHER_PRIVATE_KEY")
    observed = []

    def complete(self, selected, messages):
        observed.append((selected, messages))
        return AgentReply(
            selected.profile, selected.provider_name, selected.model, "hello", "completed"
        )

    monkeypatch.setattr(AgentProviderClient, "complete", complete)
    if source == "argument":
        flags = ["--prompt", "explicit text"]
    elif source == "file":
        prompt = tmp_path / "prompt.txt"
        prompt.write_text("explicit text")
        flags = ["--prompt-file", str(prompt)]
    else:
        monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=BytesIO(b"explicit text")))
        flags = ["--prompt-file", "-"]
    assert main(["agent", "providers", "request", "--config", str(path), "--json", *flags]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["text"] == "hello"
    assert observed[0][0].model == "selected-model"
    assert observed[0][1][0].content == "explicit text"
    assert "PRIVATE_KEY" not in output


def test_missing_selected_key_fails_before_request(tmp_path, monkeypatch, capsys, no_runtime):
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    path = tmp_path / "providers.toml"
    path.write_text(CONFIG)
    monkeypatch.delenv("MY_KEY", raising=False)
    monkeypatch.setenv("MY_OTHER_KEY", "other-key")
    monkeypatch.setattr(
        AgentProviderClient,
        "complete",
        lambda *args, **kwargs: pytest.fail("missing credentials must fail before network"),
    )
    assert main(["agent", "providers", "request", "--config", str(path), "--prompt", "hello"]) == 2
    assert "MY_KEY" in capsys.readouterr().err


def test_request_refusal_and_configuration_errors_are_safe_json(
    tmp_path, monkeypatch, capsys, no_runtime
):
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    path = tmp_path / "providers.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("MY_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("MY_OTHER_KEY", "OTHER_PRIVATE_KEY")

    def refuse(*args):
        raise AgentProviderError("Selected provider request timed out")

    monkeypatch.setattr(AgentProviderClient, "complete", refuse)
    assert (
        main(
            ["agent", "providers", "request", "--config", str(path), "--prompt", "hello", "--json"]
        )
        == 2
    )
    report = json.loads(capsys.readouterr().err)
    assert report["status"] == "rejected" and "timed out" in report["error"]
    path.write_text(CONFIG.replace('api_key_env = "MY_KEY"', 'api_key = "PRIVATE_KEY"'))
    assert main(["agent", "providers", "validate", "--config", str(path), "--json"]) == 2
    assert "PRIVATE_KEY" not in capsys.readouterr().err
