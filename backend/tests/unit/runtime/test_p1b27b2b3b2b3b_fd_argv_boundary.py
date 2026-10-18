"""P1B27B2B3B2B3B: Step argv cannot become native mount authority.

The mount builder and the handoff share ONE original bind lease and one bwrap
option/command delimiter. These are hosted Linux regressions, not Gate B.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import _require_original_native_fd_handoff
from tests.factories.x0_architecture import replace_python_syntax
from tests.unit.runtime.test_p1b27b2b2a_pinned_bind_source import _argv, _prepared
from tests.unit.runtime.test_p1b27b2b3b2b2_native_fd_handoff import AtomicStub


def _final(lease: OriginalBindSourceLease) -> list[str]:
    args = _argv(lease)
    lease.consume_validated_argv(args)
    return args


def test_step_literal_mount_switches_cannot_reject_a_valid_original_handoff(tmp_path: Path) -> None:
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        args = _final(lease)
        args += ["--bind-fd", "999", "/not-a-real-mount", "--ro-bind", "/src", "/dest"]
        lease.verify_fd_argv(args)
        runtime = os.memfd_create("original-boundary", os.MFD_CLOEXEC)
        try:
            assert _require_original_native_fd_handoff(
                tuple(args), AtomicStub((runtime,)), lease
            ) == (
                runtime,
                *lease.pass_fds,
            )
        finally:
            os.close(runtime)


def test_step_cannot_replace_a_missing_real_mount_with_command_argv(tmp_path: Path) -> None:
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        args = _final(lease)
        index = args.index("--ro-bind-fd")
        stolen = args[index : index + 3]
        del args[index : index + 3]
        args.extend(stolen)  # malicious child args must not repair missing native mount
        with pytest.raises(FleetPlanInvalid, match="FD bind argv"):
            lease.verify_fd_argv(args)


def test_step_cannot_replace_a_missing_mount_during_source_conversion(tmp_path: Path) -> None:
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        args = _argv(lease)
        index = args.index("--ro-bind")
        stolen = args[index : index + 3]
        del args[index : index + 3]
        args.extend(stolen)  # after '--', not a Bubblewrap option
        with pytest.raises(FleetPlanInvalid, match="Pinned source objects differ"):
            lease.consume_validated_argv(args)


@pytest.mark.parametrize("mutation", ["removed", "no-child", "front"])
def test_final_handoff_requires_a_real_child_command_separator(
    tmp_path: Path, mutation: str
) -> None:
    with OriginalBindSourceLease.pin_prepared(_prepared(tmp_path)) as lease:
        args = _final(lease)
        if mutation == "removed":
            args.remove("--")
        elif mutation == "no-child":
            del args[args.index("--") + 1 :]
        else:
            args.insert(0, "--")
        runtime = os.memfd_create("original-boundary", os.MFD_CLOEXEC)
        try:
            with pytest.raises(FleetPlanInvalid, match="child-command boundary"):
                _require_original_native_fd_handoff(tuple(args), AtomicStub((runtime,)), lease)
        finally:
            os.close(runtime)


def test_x0_original_command_boundary_is_enforced(tmp_path: Path) -> None:
    import shutil

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        _check_p1b27b2b3b2b3b_fd_argv_boundary,
    )

    package = ROOT / "backend/src/oh_goon"
    assert _check_p1b27b2b3b2b3b_fd_argv_boundary(package) == []
    for file_name, fragment in (
        ("fleet_plan_bind_source_custody.py", "args[:args.index('--')] if '--' in args else args"),
        ("fleet_plan_bubblewrap_isolation.py", "if '--' not in argv or argv.index('--') == 0"),
    ):
        sandbox = tmp_path / file_name / "integrations"
        sandbox.mkdir(parents=True)
        for source in ("fleet_plan_bind_source_custody.py", "fleet_plan_bubblewrap_isolation.py"):
            shutil.copy2(package / "integrations" / source, sandbox / source)
        candidate = sandbox / file_name
        candidate.write_text(replace_python_syntax(candidate.read_text(), fragment, "False"))
        assert _check_p1b27b2b3b2b3b_fd_argv_boundary(sandbox.parent)
