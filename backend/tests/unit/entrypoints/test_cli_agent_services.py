from __future__ import annotations

import json
import os
import socket
import subprocess
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import pytest

from oh_goon.cli import main


@pytest.fixture
def no_service_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], AbstractContextManager[None]]:
    import oh_goon.cli as cli
    import oh_goon.runtime.agent_providers as providers

    @contextmanager
    def forbidden_effects() -> Iterator[None]:
        with monkeypatch.context() as boundary:

            def forbidden(*args: Any, **kwargs: Any) -> Any:
                pytest.fail(
                    "offline service preparation attempted an external effect or native-store access"
                )

            boundary.setattr(cli, "_build_application_runtime", forbidden)
            boundary.setattr(providers, "load_agent_providers", forbidden)
            boundary.setattr(subprocess, "Popen", forbidden)
            boundary.setattr(socket, "socket", forbidden)
            boundary.setattr(socket, "create_connection", forbidden)
            for name in ("open", "stat", "lstat", "resolve", "expanduser"):
                boundary.setattr(Path, name, forbidden)
            environment_getitem = type(os.environ).__getitem__

            def environment_value(environment: Any, name: str) -> str:
                if name in {
                    "OPENCODE_PASSWORD",
                    "OPENCODE_SERVER_PASSWORD",
                    "OPENAI_API_KEY",
                    "CODEX_API_KEY",
                    "CODEX_HOME",
                }:
                    pytest.fail("offline preparation inspected native state or a credential")
                return environment_getitem(environment, name)

            boundary.setattr(type(os.environ), "__getitem__", environment_value)
            yield

    return forbidden_effects


@pytest.mark.parametrize("agent", ["codex", "opencode"])
def test_service_prepare_has_no_native_state_network_or_runtime_effects(
    agent: str,
    no_service_effects: Callable[[], AbstractContextManager[None]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    command = [
        "agent",
        "service",
        "prepare",
        "--agent",
        agent,
        "--executable",
        f"/public/{agent}",
        "--json",
    ]
    if agent == "codex":
        command.extend(["--socket", "/private-service/codex.sock"])
    with no_service_effects():
        result_code = main(command)
    assert result_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "configured_not_connected"
    assert result["started"] is False and result["execution_admitted"] is False
    assert result["native_store_access"] == "not_requested"
    assert result["launch_argv"][0] == f"/public/{agent}"
    assert result["unverified"]


@pytest.mark.parametrize(
    ("agent", "program", "extra"),
    [
        ("codex", "/public/codex", []),
        ("opencode", "/public/opencode", ["--socket", "/private-service/unused.sock"]),
        ("codex", "relative/codex", ["--socket", "/private-service/codex.sock"]),
        ("codex", "/public/codex", ["--socket", "/private-service/../native.sock"]),
        ("codex", "/public/codex", ["--socket", "/public/codex"]),
        ("codex", "/public/codex", ["--socket", "/public/codex/control.sock"]),
        ("codex", "/public/codex/program", ["--socket", "/public/codex"]),
    ],
)
def test_service_prepare_refuses_invalid_setup_before_effects(
    agent: str,
    program: str,
    extra: list[str],
    no_service_effects: Callable[[], AbstractContextManager[None]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with no_service_effects():
        result_code = main(
            [
                "agent",
                "service",
                "prepare",
                "--agent",
                agent,
                "--executable",
                program,
                "--json",
                *extra,
            ]
        )
    assert result_code == 2
    result = json.loads(capsys.readouterr().err)
    assert result["status"] == "rejected" and result["error"]


def test_service_prepare_plain_recipe_remains_non_authorizing(
    no_service_effects: Callable[[], AbstractContextManager[None]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with no_service_effects():
        result_code = main(
            [
                "agent",
                "service",
                "prepare",
                "--agent",
                "codex",
                "--executable",
                "/public/Codex installation/codex",
                "--socket",
                "/private-service/control.sock",
            ]
        )
    assert result_code == 0
    output = capsys.readouterr().out
    assert "Nothing started, connected or admitted" in output
    assert (
        "'/public/Codex installation/codex' app-server --listen unix:///private-service/control.sock"
        in output
    )


def test_service_preparation_cannot_accept_a_task_or_execution_profile(
    no_service_effects: Callable[[], AbstractContextManager[None]],
) -> None:
    with pytest.raises(SystemExit) as error, no_service_effects():
        main(
            [
                "agent",
                "service",
                "prepare",
                "--agent",
                "opencode",
                "--executable",
                "/public/opencode",
                "--prompt",
                "do work",
                "--profile",
                "trusted",
            ]
        )
    assert error.value.code == 2
