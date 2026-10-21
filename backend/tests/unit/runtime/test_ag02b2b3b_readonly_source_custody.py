"""AG-02B2B3B no-effect source mount proposal; NOT a native sandbox Gate B test."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.original_mount_intent import (
    OriginalMountIntentDescriptor,
    OriginalSandboxMount,
)
from tests.factories.fleet_plan_executor import agent_staging_scratch as agent_staging_scratch
from tests.unit.runtime.test_ag02b2b3_original_agent_attempt_staging import (
    attempt,
    original_handoff,
)

pytestmark = pytest.mark.usefixtures("agent_staging_scratch")


def prepared(tmp_path: Path):
    handoff = original_handoff(tmp_path)
    owner = attempt(tmp_path, handoff)
    stage = owner._stage_agent_prework_bytes(handoff)
    return handoff, owner, stage


def test_original_attempt_proposes_exact_readonly_mount_without_effects(tmp_path):
    handoff, owner, stage = prepared(tmp_path)
    result = owner._inspect_agent_prework_readonly_mount(handoff, stage)
    assert type(result) is OriginalSandboxMount
    assert result.source == str(owner.paths.root / "agent-source-prework")
    assert result.target == "/opt/oh-goon/agent-source"
    assert len(result.sha256) == 64
    assert result == owner._inspect_agent_prework_readonly_mount(handoff, stage)
    # The canonical original mount-descriptor owner can include this proposed
    # mount, but it has neither signed RESOURCE_INTENT nor native mount authority.
    with_mount = OriginalMountIntentDescriptor((result,), ())
    without_mount = OriginalMountIntentDescriptor((), ())
    assert with_mount.resource_identity != without_mount.resource_identity
    assert with_mount.binding_targets == (result.target,)
    assert stage["effect_permission"] == "NONE"
    assert stage["physical_sandbox_proven"] is False


@pytest.mark.parametrize(
    "tamper",
    [
        "stage-digest",
        "handoff",
        "file-count",
        "extra-file",
        "extra-directory",
        "delete-file",
        "modify-bytes",
        "symlink",
        "hardlink",
        "public-file",
        "stage-root-symlink",
        "stage-root-substitution",
        "attempt-root-symlink",
        "missing-tree",
    ],
)
def test_hostile_stage_never_becomes_mount_proposal(tmp_path, tamper):
    handoff, owner, stage = prepared(tmp_path)
    root = owner.paths.root
    staged_root = root / "agent-source-prework"
    file = next(p for p in (staged_root / "repository").rglob("*") if p.is_file())
    # The staging owner intentionally sealed the tree. An adversary acting
    # as that same owner can chmod it open again; the inspector MUST refuse
    # rather than rely on Unix owner-write bits as immutable custody.
    if tamper in {
        "extra-file",
        "extra-directory",
        "delete-file",
        "modify-bytes",
        "symlink",
        "hardlink",
        "missing-tree",
    }:
        staged_root.chmod(0o700)
        for path in staged_root.rglob("*"):
            if path.is_dir():
                path.chmod(0o700)
            elif path.is_file():
                path.chmod(0o600)
    if tamper == "stage-digest":
        stage = {**stage, "stage_identity": "sha256:" + "f" * 64}
    elif tamper == "handoff":
        stage = {**stage, "handoff_identity": "sha256:" + "f" * 64}
    elif tamper == "file-count":
        stage = {**stage, "file_count": stage["file_count"] + 1}
    elif tamper == "extra-file":
        (staged_root / "skill" / "malicious.txt").write_text("exfiltrate")
    elif tamper == "extra-directory":
        (staged_root / "skill" / "extra").mkdir()
    elif tamper == "delete-file":
        file.unlink()
    elif tamper == "modify-bytes":
        file.write_bytes(b"changed source")
    elif tamper == "symlink":
        file.unlink()
        file.symlink_to("/etc/passwd")
    elif tamper == "hardlink":
        os.link(file, file.with_name(file.name + ".linked"))
    elif tamper == "public-file":
        file.chmod(0o644)
    elif tamper == "stage-root-symlink":
        staged_root.rename(root / "saved-stage")
        staged_root.symlink_to(root / "saved-stage", target_is_directory=True)
    elif tamper == "stage-root-substitution":
        staged_root.rename(root / "old-stage")
        staged_root.mkdir(mode=0o700)
    elif tamper == "attempt-root-symlink":
        root.rename(root.with_name("old-attempt"))
        root.symlink_to(root.with_name("old-attempt"), target_is_directory=True)
    elif tamper == "missing-tree":
        from shutil import rmtree

        rmtree(staged_root / "skill")
    with pytest.raises(FleetPlanInvalid):
        owner._inspect_agent_prework_readonly_mount(handoff, stage)


def test_no_source_reread_and_no_journal_side_effects(tmp_path, monkeypatch):
    handoff, owner, stage = prepared(tmp_path)

    def blocked(*_args, **_kwargs):
        raise AssertionError("original repository/skill source paths must not be reopened")

    monkeypatch.setattr(
        "oh_goon.services.agent_authoring_sources._capture_local_tree", blocked, raising=False
    )
    mount = owner._inspect_agent_prework_readonly_mount(handoff, stage)
    assert mount.source.endswith("/agent-source-prework")
    assert not (owner.paths.root / "agent-source-execution").exists()
