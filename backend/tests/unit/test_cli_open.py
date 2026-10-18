from __future__ import annotations

import argparse
import os
from pathlib import Path

from oh_goon.cli_open import run_open_command
from oh_goon.runtime.local_launch_access import LocalLaunchAccessStore
from oh_goon.runtime.local_launch_owner import BACKEND_GENERATION, BACKEND_SERVICE, LocalLaunchOwner, built_local_intent


def _ready_built_launch(workspace: Path, *, observed_port: int) -> LocalLaunchOwner:
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, os.getpid())
    owner.startup_complete(BACKEND_SERVICE, BACKEND_GENERATION)
    owner.bound(BACKEND_SERVICE, BACKEND_GENERATION, host="127.0.0.1", port=observed_port)
    owner.ready(BACKEND_SERVICE, BACKEND_GENERATION)
    owner.launch_ready()
    return owner


def test_open_uses_exact_observed_endpoint_and_private_capability_without_printing_secret(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    owner = _ready_built_launch(workspace, observed_port=8899)
    LocalLaunchAccessStore.configured().publish(
        launch_id=owner.identity.launch_id,
        owner_generation=owner.identity.owner_generation,
        token="launch-secret",
    )
    opened: list[str] = []

    assert run_open_command(
        argparse.Namespace(command="open"),
        browser_opener=lambda url: opened.append(url) or True,
    ) == 0

    assert opened == ["http://127.0.0.1:8899/#access=launch-secret"]
    output = capsys.readouterr().out
    assert "Endpoint: http://127.0.0.1:8899" in output
    assert "launch-secret" not in output
    assert "8787" not in output


def test_open_refuses_ready_launch_without_exact_published_capability(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    _ready_built_launch(workspace, observed_port=8899)
    opened: list[str] = []

    assert run_open_command(
        argparse.Namespace(command="open"),
        browser_opener=lambda url: opened.append(url) or True,
    ) == 1
    assert opened == []
    output = capsys.readouterr().out
    assert "OPEN UNAVAILABLE" in output
    assert "capability is unavailable" in output
