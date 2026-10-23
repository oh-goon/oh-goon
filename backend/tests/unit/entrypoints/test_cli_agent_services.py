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


def test_codex_connect_reports_only_readiness_and_closes_its_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys
    from types import SimpleNamespace

    import oh_goon.cli as cli

    events: list[object] = []

    class Client:
        def __init__(self, path: Path, *, timeout_seconds: float) -> None:
            events.append((str(path), timeout_seconds))
            self.closed = False

        async def __aenter__(self) -> Client:
            events.append("connected")
            return self

        async def __aexit__(self, *error: object) -> None:
            events.append("closed")
            self.closed = True

        @property
        def metadata(self) -> dict[str, object]:
            return {
                "status": "connected_not_qualified",
                "agent": "codex",
                "execution_admitted": False,
                "service_version": "0.163.0",
                "transport_cleanup": "complete" if self.closed else "open",
            }

    monkeypatch.setitem(
        sys.modules,
        "oh_goon.integrations.agent_codex_service",
        SimpleNamespace(CodexServiceClient=Client),
    )
    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("connection must not create a Run/database"),
    )
    with monkeypatch.context() as boundary:
        boundary.setattr(
            Path, "open", lambda *args, **kwargs: pytest.fail("native stores must not be opened")
        )
        boundary.setattr(
            subprocess,
            "Popen",
            lambda *args, **kwargs: pytest.fail("connect must not start a service"),
        )
        code = main(
            [
                "agent",
                "service",
                "connect",
                "--agent",
                "codex",
                "--socket",
                "/explicit/advertised.sock",
                "--timeout-seconds",
                "3",
                "--json",
            ]
        )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["execution_admitted"] is False and report["connection_closed"] is True
    assert report["transport_cleanup"] == "complete"
    assert events == [("/explicit/advertised.sock", 3.0), "connected", "closed"]


def test_opencode_connect_validates_before_resolving_one_transport_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys
    from types import SimpleNamespace

    import oh_goon.cli as cli

    seen: list[object] = []
    monkeypatch.setenv("OH_GOON_SERVICE_TEST_PASSWORD", "synthetic-private-service-password")
    original_get = type(os.environ).__getitem__

    def get(environment: Any, name: str) -> str:
        if name == "OH_GOON_SERVICE_TEST_PASSWORD":
            seen.append("password")
        if name in {"CODEX_HOME", "OPENAI_API_KEY", "OPENCODE_PASSWORD"}:
            pytest.fail("native defaults or credentials must not be discovered")
        return original_get(environment, name)

    def validate(endpoint: str) -> str:
        seen.append(("endpoint", endpoint))
        return endpoint

    class Client:
        def __init__(self, endpoint: str, *, password: Any, timeout_seconds: float) -> None:
            assert password.get_secret_value() == "synthetic-private-service-password"
            assert timeout_seconds == 4.0
            seen.append("frozen")
            self.metadata = SimpleNamespace(version="2.1.2", pid=123)

        async def __aenter__(self) -> Client:
            seen.append("connected")
            return self

        async def __aexit__(self, *error: object) -> None:
            seen.append("closed")

    monkeypatch.setitem(
        sys.modules,
        "oh_goon.integrations.agent_opencode_service",
        SimpleNamespace(OpenCodeServiceClient=Client, validate_opencode_service_endpoint=validate),
    )
    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("connection must not create a Run/database"),
    )
    with monkeypatch.context() as boundary:
        boundary.setattr(type(os.environ), "__getitem__", get)
        boundary.setattr(
            Path, "open", lambda *args, **kwargs: pytest.fail("native stores must not be opened")
        )
        boundary.setattr(
            subprocess,
            "Popen",
            lambda *args, **kwargs: pytest.fail("connect must not start a service"),
        )
        code = main(
            [
                "agent",
                "service",
                "connect",
                "--agent",
                "opencode",
                "--endpoint",
                "http://127.0.0.1:4096",
                "--password-env",
                "OH_GOON_SERVICE_TEST_PASSWORD",
                "--timeout-seconds",
                "4",
                "--json",
            ]
        )
    assert code == 0
    output = capsys.readouterr().out
    assert "synthetic-private-service-password" not in output
    report = json.loads(output)
    assert report["execution_admitted"] is False and report["connection_closed"] is True
    assert report["native_authentication"] == "unverified"
    assert seen == [
        ("endpoint", "http://127.0.0.1:4096"),
        "password",
        "frozen",
        "connected",
        "closed",
    ]


@pytest.mark.parametrize(
    "extra",
    [
        ["--agent", "codex"],
        ["--agent", "codex", "--socket", "/explicit.sock", "--endpoint", "http://127.0.0.1:1"],
        ["--agent", "codex", "--socket", "/explicit.sock", "--password-env", "PRIVATE"],
        ["--agent", "opencode", "--endpoint", "http://127.0.0.1:1"],
        [
            "--agent",
            "opencode",
            "--endpoint",
            "http://127.0.0.1:1",
            "--password-env",
            "BAD\nPRIVATE",
        ],
        ["--agent", "codex", "--socket", "/explicit.sock", "--timeout-seconds", "nan"],
        ["--agent", "codex", "--socket", "/explicit.sock", "--timeout-seconds", "0"],
        ["--agent", "codex", "--socket", "/explicit.sock", "--timeout-seconds", "inf"],
    ],
)
def test_connect_rejects_incompatible_flags_before_native_effects(
    extra: list[str],
    no_service_effects: Callable[[], AbstractContextManager[None]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with no_service_effects():
        code = main(["agent", "service", "connect", "--json", *extra])
    assert code == 2
    assert json.loads(capsys.readouterr().err)["status"] == "rejected"


def test_connect_refusal_never_claims_success_or_closed_transport(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import oh_goon.cli_agent_services as services
    from oh_goon.core.models.agent_providers import AgentProviderError

    async def refused(*args: Any, **kwargs: Any) -> dict[str, object]:
        raise AgentProviderError("Native Codex service refused the connection")

    monkeypatch.setattr(services, "_codex_metadata", refused)
    assert (
        main(
            [
                "agent",
                "service",
                "connect",
                "--agent",
                "codex",
                "--socket",
                "/explicit.sock",
                "--json",
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["status"] == "rejected"
