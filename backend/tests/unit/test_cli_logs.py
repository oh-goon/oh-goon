from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from oh_goon.cli_logs import run_logs_command
from oh_goon.runtime.local_launch_owner import (
    BACKEND_GENERATION,
    BACKEND_SERVICE,
    LocalLaunchOwner,
    built_local_intent,
)


def _args(*, as_json: bool) -> argparse.Namespace:
    return argparse.Namespace(command="logs", as_json=as_json)


def test_logs_json_uses_exact_workspace_binding_and_safe_event_projection(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, os.getpid())
    owner.bound(BACKEND_SERVICE, BACKEND_GENERATION, host="127.0.0.1", port=8899)

    assert run_logs_command(_args(as_json=True)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["schema"] == "oh-goon.launch-log/v1"
    assert payload["launch_id"] == owner.identity.launch_id
    rendered = lines[0]
    assert '"port":8899' in rendered
    assert '"pid"' not in rendered
    assert "start_ticks" not in rendered
    assert "uvicorn" not in rendered


def test_logs_without_binding_never_discovers_another_launch(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    assert run_logs_command(_args(as_json=False)) == 1
    assert "LAUNCH LOG UNAVAILABLE" in capsys.readouterr().out
