"""Signed Agent stage -> original LACE native mount roster, still non-executable.

No simulated mount, prework, or journal event in this suite is physical custody.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.original_mount_intent import (
    OriginalMountIntentDescriptor,
    OriginalNativeMount,
    OriginalSandboxMount,
)
from oh_goon.integrations.fleet_plan_bind_source_custody import OriginalBindSourceLease
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import _prepare_mounts
from oh_goon.integrations.fleet_plan_local_lace_intent import (
    local_resource_identities,
    prepared_original_native_mounts,
)
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import _request
from tests.unit.runtime.test_skill03c2d2b_original_tool_grant import _grant, _staged


def _source(tmp_path):
    request = _request(tmp_path)
    source = request.attempt.paths.root / "agent-source-prework"
    source.mkdir(parents=True)
    for kind in ("repository", "skill"):
        tree = source / kind
        tree.mkdir()
        (tree / "fixture.txt").write_bytes(b"fixed fixture bytes")
        (tree / "fixture.txt").chmod(0o400)
        tree.chmod(0o500)
    source.chmod(0o500)
    request.agent_source_mount = OriginalSandboxMount(
        str(source),
        "/opt/oh-goon/agent-source",
        "a" * 64,
    )
    return request


def test_original_journal_projects_exact_grant_bound_stage(tmp_path):
    conn, journal, args = _staged(tmp_path)
    seq = _grant(conn, journal, args)
    conn.execute("BEGIN IMMEDIATE")
    try:
        source = journal.original_agent_source_mount_for_grant_in(
            "run-one",
            auth=args["auth"],
            grant_seq=seq,
            step_id=args["step_id"],
            operational_attempt_identity=args["attempt_identity"],
            operational_attempt_root=args["operational_attempt_root"],
            command_ordinal=1,
        )
        assert source.source == str(args["operational_attempt_root"] / "agent-source-prework")
        assert source.target == "/opt/oh-goon/agent-source"
        assert len(source.sha256) == 64
        for changes in (
            {"grant_seq": seq + 9},
            {"command_ordinal": 2},
            {"operational_attempt_identity": "sha256:" + "f" * 64},
            {"operational_attempt_root": tmp_path / "swapped"},
        ):
            kwargs = dict(
                grant_seq=seq,
                step_id=args["step_id"],
                operational_attempt_identity=args["attempt_identity"],
                operational_attempt_root=args["operational_attempt_root"],
                command_ordinal=1,
            )
            kwargs.update(changes)
            with pytest.raises(CausalJournalError):
                journal.original_agent_source_mount_for_grant_in(
                    "run-one", auth=args["auth"], **kwargs
                )
    finally:
        conn.execute("ROLLBACK")
        conn.close()


def test_original_native_roster_contains_exact_read_only_agent_mount(tmp_path):
    request = _source(tmp_path)
    source = request.agent_source_mount
    mounts = prepared_original_native_mounts(request)
    assert [m for m in mounts if m.target == source.target] == [
        OriginalNativeMount(source.source, source.target, "--ro-bind")
    ]
    descriptor = OriginalMountIntentDescriptor(
        (),
        (),
        native_mounts=mounts,
        agent_source_mount=source,
    )
    assert descriptor.resource_identity.startswith("sha256:")
    assert (
        descriptor.resource_identity
        != OriginalMountIntentDescriptor((), (), native_mounts=mounts[:-1]).resource_identity
    )


@pytest.mark.parametrize("change", ["source", "target", "mode", "missing", "shadow"])
def test_native_descriptor_refuses_agent_source_swaps(tmp_path, change):
    request = _source(tmp_path)
    source = request.agent_source_mount
    mounts = list(prepared_original_native_mounts(request))
    if change == "source":
        mounts[-1] = OriginalNativeMount(str(tmp_path / "other"), source.target, "--ro-bind")
    elif change == "target":
        mounts[-1] = OriginalNativeMount(source.source, "/opt/another", "--ro-bind")
    elif change == "mode":
        mounts[-1] = OriginalNativeMount(source.source, source.target, "--bind")
    elif change == "missing":
        mounts.pop()
    else:
        source = replace(source, target="/workspace")
    with pytest.raises(ValueError):
        OriginalMountIntentDescriptor(
            (), (), native_mounts=tuple(mounts), agent_source_mount=source
        )


def test_agent_stage_fd_roster_is_pinned_with_existing_owner(tmp_path):
    request = _source(tmp_path)
    # Prepare each referenced source for the real O_PATH native source lease.
    for mount in prepared_original_native_mounts(request):
        source = Path(mount.source)
        if not source.exists():
            if source.suffix or source.name in {"hosts", "passwd", "group", "nsswitch.conf"}:
                source.parent.mkdir(parents=True, exist_ok=True)
                source.touch()
            else:
                source.mkdir(parents=True, exist_ok=True)
    with OriginalBindSourceLease.pin_prepared(request) as lease:
        lease.verify_kernel_custody()
        pinned = [p for p in lease.pins if p.target == "/opt/oh-goon/agent-source"]
        assert len(pinned) == 1
        assert pinned[0].option == "--ro-bind"
        assert pinned[0].source == request.agent_source_mount.source


def test_agent_mount_still_refused_before_physical_effects(tmp_path):
    request = _source(tmp_path)
    with pytest.raises(FleetPlanInvalid, match="lacks qualified LACE effect custody"):
        local_resource_identities(request)
    with pytest.raises(FleetPlanInvalid, match="lacks qualified LACE effect custody"):
        _prepare_mounts("/bin/true", request, atomic_authority=SimpleNamespace(), network_prefix=())


def test_original_bwrap_builder_consumes_same_prepared_read_only_source(tmp_path, monkeypatch):
    from oh_goon.integrations import fleet_plan_bubblewrap_isolation as bwrap

    request = _source(tmp_path)
    # Exercise the pure mount-argv builder without relaxing the production
    # pre-effect Agent refusal (tested separately above).
    monkeypatch.setattr(bwrap, "require_original_agent_source_mount_authority", lambda _: None)
    monkeypatch.setattr(bwrap, "verify_runtime_root", lambda _: None)
    request.attempt.egress.mode = "none"
    atomic = SimpleNamespace(bubblewrap_bindings=())
    argv = bwrap._prepare_mounts("/bin/true", request, atomic_authority=atomic, network_prefix=())
    triplet = ["--ro-bind", request.agent_source_mount.source, request.agent_source_mount.target]
    assert sum(argv[i : i + 3] == triplet for i in range(len(argv) - 2)) == 1
    bwrap._require_original_native_mount_argv(argv, request, atomic, None)
    tampered = list(argv)
    tampered[tampered.index(request.agent_source_mount.source)] = str(tmp_path / "substituted")
    with pytest.raises(FleetPlanInvalid, match="native mount argv"):
        bwrap._require_original_native_mount_argv(tampered, request, atomic, None)
