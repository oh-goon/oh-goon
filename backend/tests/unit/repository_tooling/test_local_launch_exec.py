from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.repository_tooling import local_launch_exec
from oh_goon.runtime.local_launch_owner import LocalLaunchOwner, built_local_intent
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


class ExecCalled(RuntimeError):
    pass


def test_exec_bridge_records_same_pid_before_native_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "state" / "oh-goon.db"
    monkeypatch.setenv("OH_GOON_DB", str(database))
    store = LocalLaunchEventStore.configured()
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path,
        asserted_by="owner",
        store=store,
    )
    monkeypatch.setattr(local_launch_exec.os, "getpid", lambda: 4242)
    monkeypatch.setattr(
        "oh_goon.runtime.local_launch_owner.observe_process_birth",
        lambda pid: ProcessBirthIdentity(pid=pid, boot_id="boot-test", start_ticks=1),
    )

    def fake_execvp(file: str, argv: list[str]) -> None:
        assert file == "uvicorn"
        assert argv == ["uvicorn", "app:app"]
        raise ExecCalled

    monkeypatch.setattr(local_launch_exec.os, "execvp", fake_execvp)
    with pytest.raises(ExecCalled):
        local_launch_exec.main(
            [
                "--launch-id",
                owner.identity.launch_id,
                "--service",
                "backend",
                "--generation",
                "1",
                "--asserted-by",
                "owner",
                "--",
                "uvicorn",
                "app:app",
            ]
        )

    manifest = store.manifest(owner.identity.launch_id)
    backend = manifest.observed.services[0]
    assert backend.process is not None and backend.process.pid.value == 4242
