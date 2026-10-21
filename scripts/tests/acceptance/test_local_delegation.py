import os
import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations._fleet_plan_cgroup_resources import (
    NESTED_CGROUP_DELEGATED_ENV,
)
from oh_goon.core.strict_executor_authority import (
    SYSTEMD_DELEGATED_ENV,
    SYSTEMD_SUBGROUP_ENV,
)
from oh_goon.repository_tooling.acceptance import local_delegation
from oh_goon.repository_tooling.acceptance.timing import RESOLVED_TIMING_ENV


def test_delegated_reentry_does_not_recurse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "name", "posix")
    assert (
        local_delegation.needs_systemd_delegation(
            {local_delegation.LOCAL_RUNTIME_DELEGATED_ENV: "1"}
        )
        is False
    )


def test_missing_physical_authority_requests_systemd_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        local_delegation,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(OSError("not delegated")),
    )
    assert local_delegation.needs_systemd_delegation({}) is True


def test_invalid_nested_delegation_is_not_reclassified_as_missing_systemd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        local_delegation,
        "resolve_certification_cgroup_root",
        lambda _environment: (_ for _ in ()).throw(
            FleetPlanInvalid("nested controller delegation is incomplete")
        ),
    )
    with pytest.raises(
        FleetPlanInvalid, match="nested controller delegation is incomplete"
    ):
        local_delegation.needs_systemd_delegation({NESTED_CGROUP_DELEGATED_ENV: "1"})


def test_valid_nested_delegation_never_requests_systemd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        local_delegation,
        "resolve_certification_cgroup_root",
        lambda _environment: tmp_path,
    )
    assert (
        local_delegation.needs_systemd_delegation({NESTED_CGROUP_DELEGATED_ENV: "1"})
        is False
    )


def test_systemd_reentry_uses_canonical_controller_establishment_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/systemd-run")

    class Completed:
        returncode = 23

    def fake_run(command: tuple[str, ...], *, cwd: Path, check: bool) -> Completed:
        observed["command"] = command
        observed["cwd"] = cwd
        observed["check"] = check
        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert (
        local_delegation.run_under_systemd_delegation(
            tmp_path,
            environment={
                "PATH": "/custom/node/bin:/usr/bin",
                "OH_GOON_EXECUTORS_FILE": "/tmp/executors.toml",
                "OH_GOON_RUNTIME_ACCEPTANCE_STEP_START_TIMEOUT_SECONDS": "300",
                "OH_GOON_RUNTIME_ACCEPTANCE_INTERRUPTIBLE_STEP_SECONDS": "480",
            },
        )
        == 23
    )
    command = observed["command"]
    assert isinstance(command, tuple)
    assert "--property=Delegate=yes" in command
    assert f"--setenv={local_delegation.LOCAL_RUNTIME_DELEGATED_ENV}=1" in command
    assert f"--setenv={SYSTEMD_DELEGATED_ENV}=1" in command
    assert (
        f"--setenv={SYSTEMD_SUBGROUP_ENV}="
        f"{local_delegation.LOCAL_RUNTIME_PROCESS_SUBGROUP}"
    ) in command
    assert "--setenv=PATH=/custom/node/bin:/usr/bin" in command
    assert "--setenv=OH_GOON_EXECUTORS_FILE=/tmp/executors.toml" in command
    resolved = next(
        item for item in command if item.startswith(f"--setenv={RESOLVED_TIMING_ENV}=")
    )
    assert '"step_start_timeout_seconds":300.0' in resolved
    assert '"interruptible_step_seconds":480.0' in resolved
    assert not any("STEP_START_TIMEOUT_SECONDS=" in item for item in command)
    assert not any("INTERRUPTIBLE_STEP_SECONDS=" in item for item in command)
    assert observed["cwd"] == tmp_path
    assert observed["check"] is False


def test_systemd_unavailable_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None)
    assert local_delegation.run_under_systemd_delegation(tmp_path) == 1


def test_systemd_reentry_without_path_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/bin/systemd-run")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not start systemd-run")
        ),
    )
    assert local_delegation.run_under_systemd_delegation(tmp_path, environment={}) == 1
