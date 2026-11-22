"""Invalid runtime capacity selections refuse before creating product state."""

from pathlib import Path
from typing import cast

import pytest

from oh_goon.application import ApplicationRuntime
from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.models.run_resources import RunConcurrencyPolicy
from oh_goon.engine.command import CommandTerminal
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation


@pytest.mark.parametrize("invalid", ["deployment", "terminal", "observation", "git"])
def test_incompatible_two_run_runtime_refuses_before_state_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    database = tmp_path / "control/state.sqlite"
    monkeypatch.setenv("OH_GOON_DB", str(database))
    for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{kind}_HOME", str(tmp_path / kind.lower()))
    policy = RunConcurrencyPolicy(
        aggregate_slice="unselected-native.slice",
        aggregate_resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=2048, max_processes=512),
        worker_resources=CicdResourcePolicy(cpu_millis=800, memory_mib=512, max_processes=128),
        controller_reserve=CicdResourcePolicy(cpu_millis=400, memory_mib=512, max_processes=128),
    )
    with pytest.raises(TrustProtocolError):
        ApplicationRuntime.build(
            standard_local=invalid != "deployment",
            standard_process_isolation=BubblewrapProcessIsolation("/usr/bin/bwrap"),
            run_concurrency=policy,
            terminal=cast(CommandTerminal, object()) if invalid == "terminal" else None,
            startup_purpose="observation" if invalid == "observation" else "application",
            git_service=GitService() if invalid == "git" else None,
        )
    assert not database.exists()
    assert not database.parent.exists()
