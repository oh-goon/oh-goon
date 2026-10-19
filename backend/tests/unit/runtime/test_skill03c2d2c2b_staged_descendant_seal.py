"""SKILL-3C2D2C2B: original stage seal and FD descendant drift tests.

A read-only mode seal and a rechecked FD tree do NOT make a directory
immutable against the stage owner or prove a physically contained Agent run.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.integrations.fleet_plan_local_lace_intent import prepared_original_native_mounts
from tests.unit.runtime.test_ag02b2b3_original_agent_attempt_staging import (
    attempt,
    original_handoff,
)
from tests.unit.runtime.test_skill03c2d2c1_agent_native_mount_projection import _source


def _request_with_native_sources(tmp_path):
    request = _source(tmp_path)
    for mount in prepared_original_native_mounts(request):
        source = Path(mount.source)
        if not source.exists():
            if source.suffix or source.name in {"hosts", "passwd", "group", "nsswitch.conf"}:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.touch()
            else:
                source.mkdir(parents=True, exist_ok=True)
    return request


def _agent_file(request):
    return Path(request.agent_source_mount.source) / "repository" / "fixture.txt"


def test_original_stage_seals_all_descendants_and_preserves_signed_proposal(tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    stage = owner._stage_agent_prework_bytes(handoff)
    root = owner.paths.root / "agent-source-prework"
    assert stat.S_IMODE(root.stat().st_mode) == 0o500
    for item in root.rglob("*"):
        expected = 0o500 if item.is_dir() else 0o400
        assert stat.S_IMODE(item.stat().st_mode) == expected
    assert owner._inspect_agent_prework_readonly_mount(handoff, stage).target == (
        "/opt/oh-goon/agent-source"
    )
    # It is still not an executable grant or qualified native filesystem.
    assert stage["effect_permission"] == "NONE"
    assert stage["physical_sandbox_proven"] is False


def test_original_proposal_rejects_owner_write_bit_restored_after_seal(tmp_path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    stage = owner._stage_agent_prework_bytes(handoff)
    member = next(p for p in (owner.paths.root / "agent-source-prework").rglob("*") if p.is_file())
    member.chmod(0o600)
    with pytest.raises(FleetPlanInvalid, match="unsafe Agent staged file"):
        owner._inspect_agent_prework_readonly_mount(handoff, stage)


@pytest.mark.parametrize("tamper", ["bytes", "same-bytes", "extra", "symlink", "hardlink", "mode"])
def test_pinned_descendants_reject_mutation_before_native_argv_consumption(tmp_path, tamper):
    request = _request_with_native_sources(tmp_path)
    member = _agent_file(request)
    source_root = Path(request.agent_source_mount.source)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        lease.verify_kernel_custody()
        parent = member.parent
        if tamper in {"bytes", "same-bytes"}:
            original = member.read_bytes()
            member.chmod(0o600)
            member.write_bytes(b"altered bytes" if tamper == "bytes" else original)
            member.chmod(0o400)
        elif tamper == "mode":
            member.chmod(0o600)
        else:
            parent.chmod(0o700)
            try:
                if tamper == "extra":
                    extra = parent / "extra.txt"
                    extra.write_bytes(b"new")
                    extra.chmod(0o400)
                elif tamper == "symlink":
                    member.unlink()
                    member.symlink_to("/etc/passwd")
                elif tamper == "hardlink":
                    os.link(member, parent / "another.txt")
            finally:
                parent.chmod(0o500)
        with pytest.raises(FleetPlanInvalid):
            lease.verify_kernel_custody()
        assert source_root.is_dir()


@pytest.mark.parametrize("tamper", ["writable", "symlink", "hardlink", "unexpected"])
def test_original_fd_owner_rejects_unsealed_tree_before_mount(tmp_path, tamper):
    request = _request_with_native_sources(tmp_path)
    member = _agent_file(request)
    parent = member.parent
    if tamper == "writable":
        member.chmod(0o600)
    else:
        parent.chmod(0o700)
        try:
            if tamper == "symlink":
                member.unlink()
                member.symlink_to("/etc/passwd")
            elif tamper == "hardlink":
                os.link(member, parent / "another.txt")
            else:
                (parent / "bad.sock").symlink_to("/tmp/nonexistent")
        finally:
            parent.chmod(0o500)
    with pytest.raises(FleetPlanInvalid):
        OriginalBindSourceLease.pin_prepared(request)
