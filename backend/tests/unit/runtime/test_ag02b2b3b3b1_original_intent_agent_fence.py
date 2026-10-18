"""Regression: signed Agent prework never becomes an implicit RESOURCE_INTENT.

This is a single-authority pre-effect fence, NOT a qualified agent mount.
"""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.ports.original_mount_intent import (
    OriginalHostToolMount,
    OriginalMountIntentDescriptor,
    OriginalSandboxMount,
)
from oh_goon.integrations.fleet_plan_local_lace_intent import (
    local_resource_identities,
    prepared_original_native_mounts,
)
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.runtime.test_ag02b2b1_original_agent_source_reservation import fixture, reserve
from tests.unit.runtime.test_b12_local_resource_intent import _reserve, _resources, _store


def test_original_writer_blocks_signed_agent_prework_even_without_mount_in_request(tmp_path):
    connection, journal, args, _ = fixture(tmp_path)
    reserve(connection, journal, args)
    before = journal.verify("run-one", auth=args["auth"])
    assert before[-1]["kind"] == "AGENT_SOURCE_PREWORK_RESERVED"
    # In particular, an arbitrary otherwise-valid *command* resource-intent
    # packet with no staged-source mount cannot launch this admitted Agent Step.
    resources = _resources()
    connection.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(CausalJournalError, match="no qualified signed RESOURCE_INTENT"):
            journal.publish_local_resource_intent_in(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                auth=args["auth"],
                step_id=args["step_id"],
                attempt_identity="sha256:" + "d" * 64,
                execution_environment_identity="sha256:" + "a" * 64,
                backend_capability_manifest_identity="sha256:" + "b" * 64,
                resources=resources,
            )
    finally:
        connection.execute("ROLLBACK")
    assert journal.verify("run-one", auth=args["auth"]) == before
    connection.close()


def test_unrelated_existing_command_step_keeps_original_resource_writer(tmp_path):
    connection, journal = _store(tmp_path / "command.sqlite")
    assert _reserve(journal) == 2
    assert (
        journal.verify(
            "run-one",
            auth=__import__(
                "tests.unit.core.test_enforcer_lace_trust", fromlist=["authenticator"]
            ).authenticator(),
        )[-1]["kind"]
        == "RESOURCE_INTENT"
    )
    connection.close()


def test_one_original_mount_descriptor_matches_intent_projection(tmp_path):
    source = tmp_path / "frozen"
    request = SimpleNamespace(
        attempt=SimpleNamespace(
            paths=SimpleNamespace(
                root=tmp_path / "attempt",
                workspace=tmp_path / "workspace",
                cache=tmp_path / "cache",
                etc=tmp_path / "etc",
                home=tmp_path / "home",
                tmp=tmp_path / "tmp",
                config=tmp_path / "config",
            ),
            runtime_root=SimpleNamespace(
                root=tmp_path / "runtime",
                identity=SimpleNamespace(sha256="e" * 64),
                uv_system_python="/usr/bin/python3",
            ),
            egress=SimpleNamespace(model_dump=lambda **_: {"mode": "none"}),
            resources=SimpleNamespace(model_dump=lambda **_: {"cpu_millis": 500}),
            nested_strict_execution=False,
            sandbox_bindings=(
                SimpleNamespace(
                    snapshot_path=source,
                    sandbox_path="/opt/test",
                    identity=SimpleNamespace(sha256="c" * 64),
                ),
            ),
            host_tools=(
                SimpleNamespace(
                    name="sh",
                    execution_snapshot_path=tmp_path / "tool",
                    execution_identity=SimpleNamespace(sha256="d" * 64),
                ),
            ),
        ),
        cwd="/workspace",
        read_only_workspace=None,
    )
    actual = local_resource_identities(request)["mounts"]
    expected = OriginalMountIntentDescriptor(
        (OriginalSandboxMount(str(source), "/opt/test", "c" * 64),),
        (OriginalHostToolMount("sh", str(tmp_path / "tool"), "d" * 64),),
        native_mounts=prepared_original_native_mounts(request),
    ).resource_identity
    assert actual == expected
    assert actual != digest(
        "oh-goon/b12c/mounts/v1",
        canonical_bytes(
            {
                "sandbox_bindings": [
                    {"source": str(source), "target": "/opt/test", "sha256": "c" * 64}
                ],
                "host_tools": [
                    {"name": "sh", "source": str(tmp_path / "tool"), "sha256": "d" * 64}
                ],
            }
        ),
    )


def test_ambiguous_mount_targets_rejected_by_one_descriptor(tmp_path):
    target = SimpleNamespace(
        root=tmp_path / "attempt",
        workspace=tmp_path,
        cache=tmp_path,
        etc=tmp_path / "etc",
        home=tmp_path / "home",
        tmp=tmp_path / "tmp",
        config=tmp_path / "config",
    )
    base = SimpleNamespace(
        attempt=SimpleNamespace(
            paths=target,
            runtime_root=SimpleNamespace(
                root=tmp_path, identity=SimpleNamespace(sha256="e" * 64), uv_system_python="python"
            ),
            egress=SimpleNamespace(model_dump=lambda **_: {}),
            resources=SimpleNamespace(model_dump=lambda **_: {}),
            nested_strict_execution=False,
            sandbox_bindings=tuple(
                SimpleNamespace(
                    snapshot_path=tmp_path / str(i),
                    sandbox_path="/same",
                    identity=SimpleNamespace(sha256="f" * 64),
                )
                for i in range(2)
            ),
            host_tools=(),
        ),
        cwd="/sandbox",
        read_only_workspace=None,
    )
    with pytest.raises(ValueError, match="ambiguous"):
        local_resource_identities(base)


def test_x0_original_resource_writer_must_keep_no_effect_agent_fence():
    source = ROOT / "backend/src/oh_goon/runtime/enforcer_lace_journal.py"
    tree = ast.parse(source.read_text())
    owners = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "publish_local_resource_intent_in"
    ]
    assert len(owners) == 1
    fn = owners[0]
    helper = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == "_require_original_resource_intent_step_order"
    ]
    assert len(helper) == 1
    assert any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_require_original_resource_intent_step_order"
        for n in ast.walk(fn)
    )
    for fact in ("AGENT_SOURCE_PREWORK_STAGED", "AGENT_SOURCE_PREWORK_RESERVED"):
        assert any(isinstance(n, ast.Constant) and n.value == fact for n in ast.walk(helper[0]))
