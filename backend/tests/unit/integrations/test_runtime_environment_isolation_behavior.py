from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_values import LiteralEnvironmentPart, ResolvedEnvironmentBinding
from oh_goon.integrations.fleet_plan_environment import (
    direct_plan_environment,
    strict_plan_environment,
)


def _binding(name: str, value: str) -> ResolvedEnvironmentBinding:
    return ResolvedEnvironmentBinding(name=name, parts=(LiteralEnvironmentPart(value=value),))


def test_step_cannot_replace_private_runtime_home_with_plan_environment(tmp_path: Path) -> None:
    with pytest.raises(FleetPlanInvalid, match="runtime-owned environment"):
        strict_plan_environment(
            (_binding("HOME", str(tmp_path / "host-controlled-home")),),
            home=tmp_path / "attempt-home",
            host_environment={},
        )


def test_step_cannot_redirect_runtime_state_or_data_outside_private_home(tmp_path: Path) -> None:
    private_home = tmp_path / "attempt-home"
    outside = tmp_path / "host-controlled-state"
    for name in ("XDG_STATE_HOME", "XDG_DATA_HOME"):
        with pytest.raises(FleetPlanInvalid, match="runtime-owned environment"):
            strict_plan_environment(
                (_binding(name, str(outside)),), home=private_home, host_environment={}
            )

    environment = strict_plan_environment((), home=private_home, host_environment={})
    assert Path(environment["XDG_STATE_HOME"]).is_relative_to(private_home)
    assert Path(environment["XDG_DATA_HOME"]).is_relative_to(private_home)


def test_plan_cannot_redirect_command_lookup_to_unadmitted_tool_supply(tmp_path: Path) -> None:
    rogue = tmp_path / "rogue-tools"
    rogue.mkdir()
    executable = rogue / "true"
    executable.write_text("#!/bin/sh\necho rogue\n", encoding="utf-8")
    executable.chmod(0o755)
    with pytest.raises(FleetPlanInvalid, match="runtime-owned environment"):
        strict_plan_environment(
            (_binding("PATH", str(rogue)),),
            home=tmp_path / "attempt-home",
            host_environment={"PATH": "/usr/bin"},
        )


@pytest.mark.parametrize(
    "name,value",
    (
        ("LD_PRELOAD", "/host/lib/injected.so"),
        ("LD_LIBRARY_PATH", "/host/lib"),
        ("DYLD_INSERT_LIBRARIES", "/host/lib/injected.dylib"),
        ("BASH_ENV", "/host/profile"),
        ("NODE_OPTIONS", "--require=/host/injected.js"),
    ),
)
def test_step_cannot_inject_unadmitted_host_code_through_runtime_environment(
    name: str, value: str, tmp_path: Path
) -> None:
    with pytest.raises(FleetPlanInvalid, match="runtime-owned environment"):
        direct_plan_environment(
            (_binding(name, value),),
            home=tmp_path / "attempt-home",
            host_environment={"PATH": os.environ.get("PATH", "")},
        )
