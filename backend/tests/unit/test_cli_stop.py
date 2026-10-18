from __future__ import annotations

import argparse
from pathlib import Path

import oh_goon.cli_stop as cli_stop
from oh_goon.runtime.local_launch_owner import LocalLaunchOwner, built_local_intent


def _args() -> argparse.Namespace:
    return argparse.Namespace(command="stop")


def test_stop_requests_exact_owner_without_writing_lifecycle_itself(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
    )
    before = owner.store.manifest(owner.identity.launch_id).lifecycle.state
    targets = []
    monkeypatch.setattr(cli_stop, "signal_launch_stop", lambda target: targets.append(target))

    assert cli_stop.run_stop_command(_args()) == 0
    assert [target.launch_id for target in targets] == [owner.identity.launch_id]
    assert owner.store.manifest(owner.identity.launch_id).lifecycle.state is before
    output = capsys.readouterr().out
    assert "STOP REQUESTED" in output
    assert "Lifecycle: unchanged until the launch owner publishes terminal evidence" in output


def test_stop_without_owner_birth_proof_refuses_instead_of_killing_pid(tmp_path: Path, monkeypatch, capsys) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "state" / "oh-goon.db"))
    LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=workspace,
        asserted_by="cli-start",
        owner_pid=99999999,
    )
    called = []
    monkeypatch.setattr(cli_stop, "signal_launch_stop", lambda target: called.append(target))

    assert cli_stop.run_stop_command(_args()) == 1
    assert called == []
    assert "STOP REFUSED" in capsys.readouterr().out
