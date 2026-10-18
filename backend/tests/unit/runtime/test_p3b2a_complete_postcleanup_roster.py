"""P3B2A: only complete ORIGINAL shared observer rounds can be compared.

Even complete local nondetection has NO authority over detached/hidden resources.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copy2

import pytest

from oh_goon.core.ports.local_lace_cleanup import NativeCleanupResult
from oh_goon.core.ports.physical_observation import (
    ORIGINAL_KERNEL_COMPONENTS,
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
)
from oh_goon.integrations import linux_physical_observer
from oh_goon.repository_tooling.architecture.repository_layout import ROOT
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    _check_p3b2a_complete_postcleanup_roster,
)
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def _cleanup(owner):
    ticket = owner.prepare_cleanup(owner.rediscovery_subject(intent_run_seq=2))
    return owner.execute_native_cleanup(
        ticket,
        lambda: NativeCleanupResult(
            ("CGROUP",),
            "CLEANUP_ACTIONS_ATTEMPTED",
        ),
    )


def _round(owner, scope, root):
    return owner.sample_recovery_physical_unqualified(
        intent_run_seq=2,
        scope=scope,
        mount_intent=mount_descriptor(),
        proc_root=root,
    )


def _assessment(owner, scope):
    result = owner.assess_recovery_physical_convergence_unqualified(
        intent_run_seq=2,
        scope=scope,
        mount_intent=mount_descriptor(),
    )
    assert result.physical_absence_proven is False
    assert result.quiescence_proven is False
    assert result.teardown_proven is False
    assert result.effect_permission == "NONE"
    assert result.observer_custody == "UNVERIFIED"
    return result


@pytest.mark.parametrize(
    "components",
    [
        ("cgroup",),
        ORIGINAL_KERNEL_COMPONENTS[:-1],
        ORIGINAL_KERNEL_COMPONENTS[1:],
        ORIGINAL_KERNEL_COMPONENTS[::-1],
        (*ORIGINAL_KERNEL_COMPONENTS[:-1], "unknown_new_probe"),
    ],
)
def test_partial_or_altered_postcleanup_observations_remain_incomplete(
    tmp_path, monkeypatch, components
):
    conn, journal, _, owner, scope = seeded(tmp_path)
    snapshot = PhysicalKernelSnapshot(
        tuple((name, PhysicalKernelFact("NOT_OBSERVED", "scoped only")) for name in components)
    )
    monkeypatch.setattr(
        linux_physical_observer,
        "observe_kernel",
        lambda _scope, *, proc_root: snapshot,
    )
    try:
        _cleanup(owner)
        _round(owner, scope, tmp_path)
        _round(owner, scope, tmp_path)
        result = _assessment(owner, scope)
        assert result.state == "INCOMPLETE_OR_CHANGED_SCOPE"
        assert result.post_cleanup_rounds == 2
        assert (
            len(
                [
                    e
                    for e in journal.verify("run-one", auth=owner.authenticator)
                    if e["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
                ]
            )
            == 2
        )
    finally:
        conn.close()


def test_mixed_full_and_partial_rounds_never_count_as_convergence(tmp_path, monkeypatch):
    conn, _, _, owner, scope = seeded(tmp_path)
    snapshots = iter(
        [
            PhysicalKernelSnapshot(
                tuple(
                    (name, PhysicalKernelFact("NOT_OBSERVED", "bounded scoped probe"))
                    for name in ORIGINAL_KERNEL_COMPONENTS
                )
            ),
            PhysicalKernelSnapshot(
                (("cgroup", PhysicalKernelFact("NOT_OBSERVED", "scoped only")),)
            ),
        ]
    )
    monkeypatch.setattr(
        linux_physical_observer, "observe_kernel", lambda _scope, *, proc_root: next(snapshots)
    )
    try:
        _cleanup(owner)
        _round(owner, scope, tmp_path)
        _round(owner, scope, tmp_path)
        assert _assessment(owner, scope).state == "INCOMPLETE_OR_CHANGED_SCOPE"
    finally:
        conn.close()


def test_full_original_roster_remains_nonauthoritative(tmp_path, monkeypatch):
    conn, _, _, owner, scope = seeded(tmp_path)
    snapshot = PhysicalKernelSnapshot(
        tuple(
            (name, PhysicalKernelFact("NOT_OBSERVED", "bounded local nondetection"))
            for name in ORIGINAL_KERNEL_COMPONENTS
        )
    )
    monkeypatch.setattr(
        linux_physical_observer, "observe_kernel", lambda _scope, *, proc_root: snapshot
    )
    try:
        _cleanup(owner)
        _round(owner, scope, tmp_path)
        _round(owner, scope, tmp_path)
        result = _assessment(owner, scope)
        assert result.state == "REPEATED_SCOPED_NONDETECTION"
        assert result.post_cleanup_rounds == 2
        assert result.coverage == "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    finally:
        conn.close()


def test_shared_observer_component_order_is_the_one_port_roster():
    # No second scanner or duplicate enum in the original P3 owner.
    assert ORIGINAL_KERNEL_COMPONENTS == (
        "cgroup",
        "subtree",
        "process",
        "mounts",
        "network",
        "tracked_mount_namespace",
        "tracked_network_namespace",
        "visible_namespace_fd_holders",
        "visible_foreign_mount_targets",
        "visible_mount_fd_holders",
    )


def test_x0_rejects_bypassed_roster_or_new_observer(tmp_path):
    sources = (
        "core/ports/physical_observation.py",
        "integrations/linux_physical_observer.py",
        "runtime/enforcer_lace_original_physical_sampling.py",
    )
    package = ROOT / "backend/src/oh_goon"
    for name in sources:
        dest = tmp_path / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy2(package / name, dest)
    assert not _check_p3b2a_complete_postcleanup_roster(tmp_path)
    for name, old, new in (
        (sources[0], '"visible_mount_fd_holders",', '"duplicate_component",'),
        (sources[1], "if tuple(first) != ORIGINAL_KERNEL_COMPONENTS:", "if False:"),
        (sources[2], "!= ORIGINAL_KERNEL_COMPONENTS", "== ORIGINAL_KERNEL_COMPONENTS"),
    ):
        file = tmp_path / name
        original = file.read_text()
        assert old in original
        file.write_text(original.replace(old, new, 1))
        assert _check_p3b2a_complete_postcleanup_roster(tmp_path)
        file.write_text(original)


@pytest.mark.parametrize(
    "survivor",
    [
        "cgroup",
        "subtree",
        "process",
        "mounts",
        "network",
        "visible_namespace_fd_holders",
        "visible_foreign_mount_targets",
    ],
)
def test_any_non_mount_fd_positive_survivor_blocks_nondetection(tmp_path, monkeypatch, survivor):
    conn, _, _, owner, scope = seeded(tmp_path)
    snapshot = PhysicalKernelSnapshot(
        tuple(
            (
                name,
                PhysicalKernelFact(
                    "PRESENT" if name == survivor else "NOT_OBSERVED",
                    "bounded local resource",
                ),
            )
            for name in ORIGINAL_KERNEL_COMPONENTS
        )
    )
    monkeypatch.setattr(
        linux_physical_observer, "observe_kernel", lambda _scope, *, proc_root: snapshot
    )
    try:
        _cleanup(owner)
        _round(owner, scope, tmp_path)
        _round(owner, scope, tmp_path)
        result = _assessment(owner, scope)
        assert result.state == "VISIBLE_RESOURCES_REMAIN"
    finally:
        conn.close()


def test_original_shared_double_scanner_enforces_complete_port_roster():
    def component():
        return {"state": "NOT_OBSERVED", "detail": "scoped only"}

    probes = {
        name: (lambda *args, **kwargs: component()) for name in linux_physical_observer.PROBES
    }
    probes["_tracked_namespace_inventory"] = lambda *_args: {
        "tracked_mount_namespace": component(),
        "tracked_network_namespace": component(),
    }
    snapshot = linux_physical_observer.observe_kernel(
        object(),
        proc_root=Path("/nonexistent-test-proc"),
        diagnostic_probes=probes,
    )
    assert tuple(name for name, _ in snapshot.components) == ORIGINAL_KERNEL_COMPONENTS
    probes["_tracked_namespace_inventory"] = lambda *_args: {
        "tracked_mount_namespace": component(),
    }
    with pytest.raises(ValueError, match="component roster changed"):
        linux_physical_observer.observe_kernel(
            object(),
            proc_root=Path("/nonexistent-test-proc"),
            diagnostic_probes=probes,
        )
