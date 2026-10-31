from __future__ import annotations

import json
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never

import pytest

from oh_goon.cli import main
from oh_goon.core.models.agent_providers import (
    AgentMessage,
    AgentProviderError,
    AgentReply,
    ResolvedAgentModel,
)

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
def no_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("provider commands must not create Fleet/SQLite"),
    )


@pytest.mark.parametrize("command", ["list", "validate"])
def test_provider_discovery_is_offline_and_does_not_require_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
    command: str,
) -> None:
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


def test_path_does_not_read_or_create_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert main(["agent", "providers", "path", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["config"] == str(tmp_path / "oh-goon/providers.json")
    assert not (tmp_path / "oh-goon").exists()


@pytest.mark.parametrize("command", ["list", "validate"])
@pytest.mark.parametrize("as_json", [False, True])
def test_saved_native_default_is_visible_without_api_setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
    command: str,
    as_json: bool,
) -> None:
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    monkeypatch.setattr(
        AgentProviderClient,
        "complete",
        lambda *args, **kwargs: pytest.fail("native catalog inspection must stay offline"),
    )
    path = tmp_path / "providers.json"
    path.write_text(
        json.dumps(
            {
                "default_native_profile": "signed-in",
                "native_profiles": {
                    "signed-in": {
                        "allowed_agents": ["codex"],
                        "codex_login": {
                            "home": "/unprobed/native-home",
                            "model": "gpt-6.1-sol",
                            "reasoning_effort": "low",
                        },
                    }
                },
            }
        )
    )
    argv = ["agent", "providers", command, "--config", str(path)]
    assert main([*argv, *(["--json"] if as_json else [])]) == 0
    output = capsys.readouterr().out
    if as_json:
        report = json.loads(output)
        assert report["default_native_profile"] == "signed-in"
        assert report["status"] == "configured_not_connected"
        assert report["providers"] == report["models"] == {}
    else:
        assert "Default native profile: signed-in" in output
    assert "/unprobed/native-home" not in output


@pytest.mark.parametrize("source", ["argument", "file", "stdin"])
def test_explicit_request_uses_selected_profile_and_bounded_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
    source: str,
) -> None:
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    path = tmp_path / "providers.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("MY_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("MY_OTHER_KEY", "OTHER_PRIVATE_KEY")
    observed: list[tuple[ResolvedAgentModel, Sequence[AgentMessage]]] = []

    def complete(
        self: AgentProviderClient, selected: ResolvedAgentModel, messages: Sequence[AgentMessage]
    ) -> AgentReply:
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


def test_missing_selected_key_fails_before_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
) -> None:
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
    assert "AGENT_PROVIDER_ERROR" in capsys.readouterr().err


def test_request_refusal_and_configuration_errors_are_safe_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
) -> None:
    from oh_goon.integrations.agent_provider_client import AgentProviderClient

    path = tmp_path / "providers.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("MY_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("MY_OTHER_KEY", "OTHER_PRIVATE_KEY")

    def refuse(*args: Any) -> Never:
        raise AgentProviderError("Selected provider request timed out")

    monkeypatch.setattr(AgentProviderClient, "complete", refuse)
    assert (
        main(
            ["agent", "providers", "request", "--config", str(path), "--prompt", "hello", "--json"]
        )
        == 2
    )
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert output.err == ""
    assert report["status"] == "rejected" and report["code"] == "AGENT_PROVIDER_ERROR"
    path.write_text(CONFIG.replace('api_key_env = "MY_KEY"', 'api_key = "PRIVATE_KEY"'))
    assert main(["agent", "providers", "validate", "--config", str(path), "--json"]) == 2
    output = capsys.readouterr()
    assert "PRIVATE_KEY" not in output.out + output.err
    assert json.loads(output.out)["status"] == "rejected"


@pytest.mark.parametrize("provider", ["ollama", "litellm"])
def test_setup_creates_an_offline_schema_backed_catalog_and_keeps_existing_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
    provider: str,
) -> None:
    from oh_goon.integrations.agent_provider_client import AgentProviderClient
    from oh_goon.runtime.agent_providers import load_agent_providers

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(
        AgentProviderClient, "complete", lambda *args: pytest.fail("setup must not connect")
    )
    command = ["agent", "providers", "setup", "--provider", provider, "--model", "team/model:tag"]
    assert main([*command, "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "configured_not_connected"
    path = tmp_path / "oh-goon/providers.json"
    raw = path.read_bytes()
    payload = json.loads(raw)
    schema = path.parent / payload["$schema"]
    assert "providers" in json.loads(schema.read_bytes())["properties"]
    config = load_agent_providers(path)
    assert config.resolve(None, environment={}).model == "team/model:tag"
    assert main(command) == 2
    assert "AGENT_PROVIDER_ERROR" in capsys.readouterr().err
    assert path.read_bytes() == raw


def test_invalid_setup_cannot_create_files_or_print_accidental_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    no_runtime: None,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert (
        main(
            [
                "agent",
                "providers",
                "setup",
                "--provider",
                "litellm",
                "--model",
                "model",
                "--base-url",
                "https://user:PRIVATE_SECRET@example.com/v1",
            ]
        )
        == 2
    )
    assert "PRIVATE_SECRET" not in capsys.readouterr().err
    assert not list(tmp_path.iterdir())
