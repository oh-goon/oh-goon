"""Unqualified Agent staging cannot reach original native LACE effects."""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_bubblewrap_isolation import BubblewrapProcessIsolation
from oh_goon.integrations.fleet_plan_local_lace_intent import (
    require_original_agent_source_mount_authority,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT


def request(tmp_path, *, target="/safe/input", source=None, host_tool=None, workspace=None):
    root = tmp_path / "attempt"
    root.mkdir(exist_ok=True)
    attempt = SimpleNamespace(
        paths=SimpleNamespace(root=root),
        sandbox_bindings=(
            SimpleNamespace(
                sandbox_path=target,
                snapshot_path=source if source is not None else tmp_path / "clean-frozen-input",
            ),
        ),
        host_tools=(
            ()
            if host_tool is None
            else (
                SimpleNamespace(
                    snapshot_path=host_tool,
                    execution_snapshot_path=host_tool,
                ),
            )
        ),
    )
    return SimpleNamespace(
        attempt=attempt,
        read_only_workspace=(
            None if workspace is None else SimpleNamespace(snapshot_path=workspace)
        ),
    )


@pytest.mark.parametrize(
    "target",
    [
        "/opt/oh-goon/agent-source",
        "/opt/oh-goon/agent-source/skill/SKILL.md",
        "/opt/oh-goon/agent-source/../elsewhere",
    ],
)
def test_unadmitted_agent_target_fails_before_effects(tmp_path, monkeypatch, target):
    candidate = request(tmp_path, target=target)
    backend = BubblewrapProcessIsolation("/never/launch/bwrap")
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_bubblewrap_isolation.verify_selected_backend",
        lambda *_: pytest.fail("selection/native effects must not start"),
    )
    with pytest.raises(FleetPlanInvalid, match="original authenticated LACE custody"):
        backend.execute(candidate, timeout=1, cancel_requested=None)


@pytest.mark.parametrize(
    "kind", ["managed-root", "managed-child", "host-tool", "workspace", "symlink"]
)
def test_no_staged_source_hidden_under_other_mount_class(tmp_path, kind):
    root = tmp_path / "attempt"
    root.mkdir()
    stage = root / "agent-source-prework"
    stage.mkdir()
    candidate = request(tmp_path)
    if kind == "managed-root":
        candidate.attempt.sandbox_bindings = (
            SimpleNamespace(sandbox_path="/safe/input", snapshot_path=stage),
        )
    elif kind == "managed-child":
        candidate.attempt.sandbox_bindings = (
            SimpleNamespace(sandbox_path="/safe/input", snapshot_path=stage / "skill"),
        )
    elif kind == "host-tool":
        candidate.attempt.host_tools = (
            SimpleNamespace(snapshot_path=stage, execution_snapshot_path=stage),
        )
    elif kind == "workspace":
        candidate.read_only_workspace = SimpleNamespace(snapshot_path=stage)
    elif kind == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(stage, target_is_directory=True)
        candidate.attempt.sandbox_bindings = (
            SimpleNamespace(sandbox_path="/safe/input", snapshot_path=alias),
        )
    with pytest.raises(FleetPlanInvalid, match="staged Agent source"):
        require_original_agent_source_mount_authority(candidate)


def test_normal_managed_inputs_are_unchanged(tmp_path):
    candidate = request(tmp_path)
    require_original_agent_source_mount_authority(candidate)
    candidate.attempt.sandbox_bindings = ()
    require_original_agent_source_mount_authority(candidate)


def test_shared_guard_is_on_original_intent_projection_and_bwrap_pre_effect_cut():
    base = ROOT / "backend/src/oh_goon/integrations"
    owner = ast.parse((base / "fleet_plan_local_lace_intent.py").read_text())
    bwrap = ast.parse((base / "fleet_plan_bubblewrap_isolation.py").read_text())
    guards = [
        n
        for n in ast.walk(owner)
        if isinstance(n, ast.FunctionDef)
        and n.name == "require_original_agent_source_mount_authority"
    ]
    assert len(guards) == 1
    projection = next(
        n
        for n in ast.walk(owner)
        if isinstance(n, ast.FunctionDef) and n.name == "local_resource_identities"
    )
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "require_original_agent_source_mount_authority"
        for n in ast.walk(projection)
    )
    cls = next(
        n
        for n in bwrap.body
        if isinstance(n, ast.ClassDef) and n.name == "BubblewrapProcessIsolation"
    )
    execute = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "execute")
    assert isinstance(execute.body[0], ast.Expr)
    assert isinstance(execute.body[0].value, ast.Call)
    assert isinstance(execute.body[0].value.func, ast.Name)
    assert execute.body[0].value.func.id == "require_original_agent_source_mount_authority"
    prepare = next(
        n for n in bwrap.body if isinstance(n, ast.FunctionDef) and n.name == "_prepare_mounts"
    )
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "require_original_agent_source_mount_authority"
        for n in ast.walk(prepare)
    )


def test_original_resource_intent_projection_cannot_sign_unqualified_stage(tmp_path):
    from oh_goon.integrations.fleet_plan_local_lace_intent import local_resource_identities

    stage = tmp_path / "attempt" / "agent-source-prework"
    candidate = request(tmp_path, source=stage)
    # The request has no runtime/cgroup/network fields. Denial must happen
    # first, before projecting or signing RESOURCE_INTENT resources.
    with pytest.raises(FleetPlanInvalid, match="staged Agent source"):
        local_resource_identities(candidate)


def test_x0_architecture_rejects_mutation_of_original_pre_effect_path(tmp_path):
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        _check_original_agent_mount_gate,
    )

    originals = ROOT / "backend/src/oh_goon/integrations"
    injected = tmp_path / "integrations"
    injected.mkdir()
    names = ["fleet_plan_local_lace_intent.py", "fleet_plan_bubblewrap_isolation.py"]
    for name in names:
        (injected / name).write_bytes((originals / name).read_bytes())
    assert _check_original_agent_mount_gate(tmp_path) == []
    baseline = (injected / names[1]).read_text()
    (injected / names[1]).write_text(
        baseline.replace("        require_original_agent_source_mount_authority(request)\n", "", 1)
    )
    assert any(
        "before effects" in issue.message for issue in _check_original_agent_mount_gate(tmp_path)
    )
    (injected / names[1]).write_text(baseline)
    projection = (injected / names[0]).read_text()
    (injected / names[0]).write_text(
        projection.replace("    require_original_agent_source_mount_authority(request)\n", "", 1)
    )
    assert any(
        "RESOURCE_INTENT" in issue.message for issue in _check_original_agent_mount_gate(tmp_path)
    )
