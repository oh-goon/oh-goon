from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path

from fastapi.testclient import TestClient

from oh_goon import cli, cli_start, container_entrypoint
from oh_goon.api.app import create_app
from oh_goon.api.local_access import LOCAL_ACCESS_HEADER
from oh_goon.core.local_access_protocol import LOCAL_ACCESS_ENV
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore
from oh_goon.runtime.local_launch_owner import LocalLaunchOwner, built_local_intent


class _Folders:
    path = Path("/tmp/league.yml")

    def list(self):
        return ()


def _fake_uvicorn(calls: list[tuple[tuple[object, ...], dict[str, object]]]):
    module = types.SimpleNamespace()

    class Config:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.args = args
            self.kwargs = kwargs

    class Server:
        def __init__(self, config: Config) -> None:
            self.config = config
            self.should_exit = False

        def run(self) -> None:
            calls.append((self.config.args, self.config.kwargs))

    def run(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    module.Config = Config
    module.Server = Server
    module.run = run
    return module


def test_raw_asgi_creation_without_launch_token_is_fail_closed(monkeypatch) -> None:
    monkeypatch.delenv(LOCAL_ACCESS_ENV, raising=False)
    app = create_app()
    client = TestClient(app)
    try:
        assert LOCAL_ACCESS_ENV not in os.environ
        assert client.get("/healthz").status_code == 204
        assert client.get("/api/health").status_code == 401
        assert (
            client.get("/api/health", headers={LOCAL_ACCESS_HEADER: "guessed"}).status_code == 401
        )
    finally:
        client.close()


def test_start_stops_before_reuse_or_launch_when_local_state_preflight_fails(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        cli_start, "check_local_state_for_startup", lambda: (calls.append("preflight"), 2)[1]
    )
    monkeypatch.setattr(
        cli_start,
        "assess_local_launch_reuse",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("reuse must not run")),
    )
    monkeypatch.setattr(
        cli_start,
        "built_ui_available",
        lambda: (_ for _ in ()).throw(AssertionError("startup work must not run")),
    )

    assert cli_start.run_start_command(argparse.Namespace(command="start")) == 2
    assert calls == ["preflight"]


def test_start_generates_launch_capability_before_uvicorn(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.delenv(LOCAL_ACCESS_ENV, raising=False)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_start, "check_local_state_for_startup", lambda: 0)
    monkeypatch.setattr(cli_start, "built_ui_available", lambda: True)
    monkeypatch.setattr(cli_start, "LeagueFolderStore", _Folders)
    monkeypatch.setattr(
        cli_start,
        "start_inprocess_readiness_monitor",
        lambda **kwargs: (kwargs["on_ready"](), types.SimpleNamespace(cancel=lambda: None))[1],
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))

    assert cli_start.run_start_command(argparse.Namespace(command="start")) == 0

    token = os.environ[LOCAL_ACCESS_ENV]
    assert token
    assert f"#access={token}" in capsys.readouterr().out
    binding = LocalLaunchBindingStore.configured().resolve(tmp_path)
    assert (
        LocalLaunchAccessStore.configured().token(
            launch_id=binding.launch_id, owner_generation=binding.owner_generation
        )
        == token
    )
    assert calls == [
        (("oh_goon.api.app:app",), {"host": "127.0.0.1", "port": 8787, "reload": False})
    ]


def test_container_entrypoint_generates_capability_and_never_disables_api_auth(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.delenv(LOCAL_ACCESS_ENV, raising=False)
    monkeypatch.setenv("OH_GOON_CONTAINER", "1")
    monkeypatch.setenv("OH_GOON_DB", str(state / "state.db"))
    monkeypatch.setattr(container_entrypoint, "built_ui_available", lambda: True)
    monkeypatch.setattr(container_entrypoint, "check_local_state_for_startup", lambda: 0)
    monkeypatch.setattr(container_entrypoint, "LeagueFolderStore", _Folders)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))

    assert container_entrypoint.main() == 0

    token = os.environ[LOCAL_ACCESS_ENV]
    assert token
    assert f"Local access token: {token}" in capsys.readouterr().out
    assert calls == [(("oh_goon.api.app:app",), {"host": "0.0.0.0", "port": 8787, "reload": False})]


def test_serve_generates_api_capability_before_uvicorn(monkeypatch, capsys) -> None:
    monkeypatch.delenv(LOCAL_ACCESS_ENV, raising=False)
    monkeypatch.setattr(cli, "check_local_state_for_startup", lambda: 0)
    monkeypatch.setattr(cli, "run_start_command", lambda _args: None)
    monkeypatch.setattr(cli, "run_local_state_command", lambda _args: None)
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))

    assert cli.main(["serve", "--host", "127.0.0.1", "--port", "9898"]) == 0

    token = os.environ[LOCAL_ACCESS_ENV]
    assert token
    output = capsys.readouterr().out
    assert f"Local access token: {token}" in output
    assert "X-Oh-Goon-Local-Access" in output
    assert calls == [
        (("oh_goon.api.app:app",), {"host": "127.0.0.1", "port": 9898, "reload": False})
    ]


def test_start_reuses_compatible_live_owner_without_starting_second_uvicorn(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.delenv(LOCAL_ACCESS_ENV, raising=False)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_start, "check_local_state_for_startup", lambda: 0)
    monkeypatch.setattr(cli_start, "built_ui_available", lambda: True)
    monkeypatch.setattr(cli_start, "LeagueFolderStore", _Folders)
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path,
        asserted_by="cli-start",
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setitem(sys.modules, "uvicorn", _fake_uvicorn(calls))

    assert cli_start.run_start_command(argparse.Namespace(command="start")) == 0
    output = capsys.readouterr().out
    assert f"Launch:         {owner.identity.launch_id}" in output
    assert "Reuse:          existing compatible live owner" in output
    assert calls == []
