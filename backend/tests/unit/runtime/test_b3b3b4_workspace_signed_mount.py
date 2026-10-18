"""B3B3B4 hostile workspace source-digest correspondence (no Agent execution)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.ports.original_mount_intent import (
    OriginalMountIntentDescriptor,
    OriginalSandboxMount,
)
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import (
    _prepare_mounts,
    _require_original_signed_mount_identity,
)
from oh_goon.integrations.fleet_plan_local_lace_intent import (
    local_resource_identities,
    prepared_original_native_mounts,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_signed_workspace_mount_projection,
)
from tests.unit.integrations.test_b12c_live_prework_resource_bridge import (
    _backend,
    _execute,
    _permit,
    _request,
)

PACKAGE = ROOT / "backend/src/oh_goon"


def _workspace(request, root: Path, sha: str = "a") -> None:
    request.read_only_workspace = SimpleNamespace(
        snapshot_path=root, identity=SimpleNamespace(sha256=sha * 64)
    )


def test_unsigned_default_wire_identity_remains_compatible(tmp_path):
    request = _request(tmp_path)
    identity = local_resource_identities(request)["mounts"]
    assert (
        identity
        == OriginalMountIntentDescriptor(
            (), (), native_mounts=prepared_original_native_mounts(request)
        ).resource_identity
    )
    _workspace(request, tmp_path / "source-one")
    assert local_resource_identities(request)["mounts"] != identity
    assert OriginalMountIntentDescriptor(
        (), (), OriginalSandboxMount(str(tmp_path / "source-one"), "/workspace", "a" * 64)
    ).binding_targets == ("/workspace",)
    request.read_only_workspace = None
    assert local_resource_identities(request)["mounts"] == identity


@pytest.mark.parametrize("change", ["source", "sha", "none"])
def test_readonly_source_snapshot_is_part_of_original_signed_mount_identity(tmp_path, change):
    request = _request(tmp_path)
    _workspace(request, tmp_path / "snapshot-one")
    signed = local_resource_identities(request)["mounts"]
    _require_original_signed_mount_identity(request, signed)
    if change == "source":
        _workspace(request, tmp_path / "snapshot-foreign")
    elif change == "sha":
        _workspace(request, tmp_path / "snapshot-one", sha="b")
    else:
        request.read_only_workspace = None
    with pytest.raises(FleetPlanInvalid, match="mount sources differ"):
        _require_original_signed_mount_identity(request, signed)
    with pytest.raises(FleetPlanInvalid, match="mount sources differ"):
        _prepare_mounts(
            "/bin/true",
            request,
            atomic_authority=object(),
            network_prefix=(),
            signed_mounts_identity=signed,
        )


@pytest.mark.parametrize("change", ["source", "sha", "drop"])
def test_reserved_workspace_swaps_fail_before_native_allocation(tmp_path, monkeypatch, change):
    backend, created = _backend(monkeypatch)
    request = _request(tmp_path)
    _workspace(request, tmp_path / "snapshot-one")
    connection, journal, permit = _permit(tmp_path, request, backend)

    class SwappingOriginalOwner:
        def reserve(self, request, backend):
            proof = permit.reserve(request, backend)
            if change == "source":
                _workspace(request, tmp_path / "different")
            elif change == "sha":
                _workspace(request, tmp_path / "snapshot-one", sha="b")
            else:
                request.read_only_workspace = None
            return proof

    request.local_resource_intent = SwappingOriginalOwner()
    with pytest.raises(FleetPlanInvalid, match="mount sources differ"):
        _execute(backend, request)
    created.assert_not_called()
    assert journal.verify("run-one", auth=permit.authenticator)[-1]["kind"] == "RESOURCE_INTENT"
    connection.close()


def test_workspace_mount_cannot_shadow_other_declared_bindings(tmp_path):
    with pytest.raises(ValueError, match="ambiguous"):
        OriginalMountIntentDescriptor(
            (OriginalSandboxMount(str(tmp_path / "other"), "/workspace", "a" * 64),),
            (),
            OriginalSandboxMount(str(tmp_path / "source"), "/workspace", "b" * 64),
        )
    with pytest.raises(ValueError, match="ambiguous"):
        OriginalMountIntentDescriptor(
            (),
            (),
            OriginalSandboxMount(str(tmp_path / "source"), "/etc", "b" * 64),
        )


def test_x0_requires_one_signed_workspace_projection():
    assert _check_signed_workspace_mount_projection(PACKAGE) == []


@pytest.mark.parametrize(
    "mutation",
    ["drop-field", "drop-digest", "drop-target", "drop-source", "drop-sha", "drop-consumption"],
)
def test_x0_mutations_reject_workspace_bypass(tmp_path, mutation):
    files = {
        "core/ports/original_mount_intent.py": PACKAGE / "core/ports/original_mount_intent.py",
        "integrations/fleet_plan_local_lace_intent.py": PACKAGE
        / "integrations/fleet_plan_local_lace_intent.py",
        "integrations/fleet_plan_bubblewrap_isolation.py": PACKAGE
        / "integrations/fleet_plan_bubblewrap_isolation.py",
    }
    for rel, path in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text()
        if mutation == "drop-field" and rel.startswith("core/"):
            text = text.replace(
                "    read_only_workspace: OriginalSandboxMount | None = None\n", "", 1
            )
        elif mutation == "drop-digest" and rel.startswith("core/"):
            text = text.replace(
                "members['read_only_workspace'] = {", "members['unused_workspace'] = {", 1
            )
        elif mutation == "drop-target" and rel.startswith("core/"):
            text = text.replace("targets += (self.read_only_workspace.target,)", "targets += ()", 1)
        elif mutation == "drop-source" and rel.endswith("fleet_plan_local_lace_intent.py"):
            text = text.replace(
                "source=str(request.read_only_workspace.snapshot_path)",
                "source=str(request.attempt.paths.workspace)",
                1,
            )
        elif mutation == "drop-sha" and rel.endswith("fleet_plan_local_lace_intent.py"):
            text = text.replace(
                "sha256=request.read_only_workspace.identity.sha256", "sha256='0' * 64", 1
            )
        elif mutation == "drop-consumption" and rel.endswith("fleet_plan_bubblewrap_isolation.py"):
            text = text.replace(
                "        _require_original_signed_mount_identity(request, signed_mounts_identity)\n",
                "",
                1,
            )
        target.write_text(text)
    assert _check_signed_workspace_mount_projection(tmp_path)
