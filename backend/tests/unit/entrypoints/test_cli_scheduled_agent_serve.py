from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from oh_goon import cli, cli_ai
from oh_goon.api import dependencies
from oh_goon.api.local_access import LOCAL_ACCESS_HEADER
from oh_goon.core.local_access_protocol import LOCAL_ACCESS_ENV
from oh_goon.core.models.agent_providers import PiSessionIntent
from oh_goon.core.models.schedule import ScheduleCatalog
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation


def _scheduled_options(workspace: Path, catalog: Path) -> list[str]:
    return [
        "--scheduled-agent-workspace",
        str(workspace),
        "--scheduled-agent-profile",
        "managed-review",
        "--scheduled-agent-catalog",
        str(catalog),
    ]


def test_plain_serve_uses_existing_unconfigured_app_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "check_local_state_for_startup", lambda: 0)
    monkeypatch.setattr(
        cli_ai, "resolve_native_cli_selection", lambda **_: pytest.fail("native selection started")
    )
    calls: list[tuple[object, dict[str, object]]] = []

    def serve(app: object, **options: object) -> None:
        calls.append((app, options))

    monkeypatch.setattr(uvicorn, "run", serve)
    monkeypatch.setenv(LOCAL_ACCESS_ENV, "local-test-access")

    assert cli.main(["app", "serve", "--port", "9111"]) == 0
    assert calls == [("oh_goon.api.app:app", {"host": "127.0.0.1", "port": 9111, "reload": False})]


@pytest.mark.parametrize("case", ["partial", "unsupported"])
def test_invalid_scheduled_serve_refuses_before_state_or_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cli, "check_local_state_for_startup", lambda: pytest.fail("state startup began")
    )
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kw: pytest.fail("server started"))
    monkeypatch.setenv(LOCAL_ACCESS_ENV, "local-token-must-not-be-reported")
    if case == "partial":
        monkeypatch.setattr(
            cli_ai,
            "resolve_native_cli_selection",
            lambda **_: pytest.fail("native selection began"),
        )
        options = ["--scheduled-agent-profile", "managed-review"]
    else:
        monkeypatch.setattr(cli_ai, "resolve_native_cli_selection", lambda **_: (None, None, None))
        options = _scheduled_options(workspace, catalog)

    assert cli.main(["app", "serve", *options]) == 2
    output = capsys.readouterr()
    assert "local-token-must-not-be-reported" not in output.out + output.err


def test_scheduled_serve_uses_one_bound_runtime_for_lifespan_and_schedule_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    isolation = BubblewrapProcessIsolation("/usr/bin/bwrap")
    git = GitService()
    session = PiSessionIntent(mode="ephemeral")
    connection = SimpleNamespace(provider=SimpleNamespace(api_key_file=None))
    selected = SimpleNamespace(
        agent="pi",
        interactive=False,
        connection=connection,
        profile=SimpleNamespace(backend="bubblewrap"),
        session=session,
    )
    events: list[str] = []

    def resolve(**arguments: object) -> tuple[object, BubblewrapProcessIsolation, GitService]:
        assert arguments == {
            "agent": "pi",
            "profile": "managed-review",
            "workspace": workspace,
            "config_file": catalog,
            "session": session,
            "role": "review",
        }
        events.append("resolve")
        return selected, isolation, git

    def probe_state() -> int:
        events.append("state")
        return 0

    def read_catalog() -> ScheduleCatalog:
        events.append("schedule")
        return ScheduleCatalog()

    def shutdown(*, wait_for_runs: bool) -> None:
        assert not wait_for_runs
        events.append("shutdown")

    runtime = SimpleNamespace(
        diagnostics=None,
        schedules=SimpleNamespace(catalog=read_catalog),
        start_background=lambda: events.append("start"),
        shutdown=shutdown,
    )

    def build(**arguments: object) -> object:
        assert arguments == {
            "standard_local": True,
            "standard_process_isolation": isolation,
            "native_git_service": git,
        }
        events.append("build")
        return runtime

    def serve(app: FastAPI, **_options: object) -> None:
        assert events == ["resolve", "state"]
        with TestClient(app) as client:
            assert client.get("/healthz").status_code == 204
            assert client.get("/api/schedules").status_code == 401
            response = client.get(
                "/api/schedules", headers={LOCAL_ACCESS_HEADER: "local-test-access"}
            )
            assert response.status_code == 200
            assert response.json() == {"schedules": []}

    dependencies.application.cache_clear()
    monkeypatch.setattr(cli_ai, "resolve_native_cli_selection", resolve)
    monkeypatch.setattr(cli, "check_local_state_for_startup", probe_state)
    monkeypatch.setattr(cli, "_build_application_runtime", build)
    monkeypatch.setattr(uvicorn, "run", serve)
    monkeypatch.setenv(LOCAL_ACCESS_ENV, "local-test-access")

    assert cli.main(["app", "serve", *_scheduled_options(workspace, catalog)]) == 0
    assert events == ["resolve", "state", "build", "start", "schedule", "shutdown"]
    assert dependencies.application.cache_info().currsize == 0
