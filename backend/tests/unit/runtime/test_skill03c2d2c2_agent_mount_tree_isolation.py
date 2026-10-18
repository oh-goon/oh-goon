"""Signed Agent ro-bind cannot be undermined by another native mount.

This is a pre-effect namespace safety gate, not kernel mount observation.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.original_mount_intent import (
    OriginalMountIntentDescriptor,
    OriginalNativeMount,
)
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.integrations.fleet_plan_local_lace_intent import prepared_original_native_mounts
from tests.unit.runtime.test_skill03c2d2c1_agent_native_mount_projection import _source


@pytest.mark.parametrize(
    "shadow",
    [
        "/opt",
        "/opt/oh-goon",
        "/opt/oh-goon/agent-source/scripts",
        "/opt/oh-goon/agent-source/scripts/runner.py",
    ],
)
@pytest.mark.parametrize("mode", ["--ro-bind", "--bind"])
def test_original_descriptor_rejects_all_native_agent_mount_shadowing(tmp_path, shadow, mode):
    request = _source(tmp_path)
    original = prepared_original_native_mounts(request)
    malicious = OriginalNativeMount(str(tmp_path / "shadow-source"), shadow, mode)
    with pytest.raises(ValueError, match="must not be shadowed"):
        OriginalMountIntentDescriptor(
            (),
            (),
            native_mounts=(*original, malicious),
            agent_source_mount=request.agent_source_mount,
        )


def test_root_base_and_exact_agent_bind_remain_valid(tmp_path):
    request = _source(tmp_path)
    original = prepared_original_native_mounts(request)
    assert OriginalMountIntentDescriptor(
        (),
        (),
        native_mounts=original,
        agent_source_mount=request.agent_source_mount,
    ).resource_identity.startswith("sha256:")


def test_fd_pinning_refuses_managed_input_inside_agent_source_before_open(tmp_path):
    request = _source(tmp_path)
    request.attempt.sandbox_bindings = (
        SimpleNamespace(
            snapshot_path=tmp_path / "input",
            sandbox_path=Path("/opt/oh-goon/agent-source/scripts"),
        ),
    )
    with pytest.raises(FleetPlanInvalid, match="must not be shadowed"):
        OriginalBindSourceLease.pin_prepared(request)


def test_fd_pinning_refuses_managed_ancestor_before_open(tmp_path):
    request = _source(tmp_path)
    request.attempt.sandbox_bindings = (
        SimpleNamespace(
            snapshot_path=tmp_path / "input",
            sandbox_path=Path("/opt/oh-goon"),
        ),
    )
    with pytest.raises(FleetPlanInvalid, match="must not be shadowed"):
        OriginalBindSourceLease.pin_prepared(request)


@pytest.mark.parametrize(
    "target",
    [
        "/opt/oh-goon/agent-source/data",
        "/opt/oh-goon",
        "/opt/oh-goon/agent-source/scripts/runner.py",
    ],
)
def test_sealed_atomic_runtime_mount_cannot_shadow_agent_source(tmp_path, monkeypatch, target):
    from oh_goon.integrations import fleet_plan_bubblewrap_isolation as bwrap

    request = _source(tmp_path)
    request.attempt.egress.mode = "none"
    monkeypatch.setattr(bwrap, "require_original_agent_source_mount_authority", lambda _: None)
    monkeypatch.setattr(bwrap, "verify_runtime_root", lambda _: None)
    forged_runtime_file = SimpleNamespace(descriptor=43, sandbox_path=Path(target), mode=0o400)
    with pytest.raises(FleetPlanInvalid, match="must not be shadowed"):
        bwrap._prepare_mounts(
            "/bin/true",
            request,
            atomic_authority=SimpleNamespace(bubblewrap_bindings=(forged_runtime_file,)),
            network_prefix=(),
        )


@pytest.mark.parametrize("mode", ["--bind", "--ro-bind"])
def test_unapproved_agent_child_bind_cannot_enter_legacy_descriptor(tmp_path, mode):
    with pytest.raises(ValueError, match="unsigned Agent source"):
        OriginalMountIntentDescriptor(
            (),
            (),
            native_mounts=(
                OriginalNativeMount(
                    str(tmp_path / "input"),
                    "/opt/oh-goon/agent-source/unapproved",
                    mode,
                ),
            ),
        )
