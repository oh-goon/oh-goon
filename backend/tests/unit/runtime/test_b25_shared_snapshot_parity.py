"""B25 read-only evidence identity: one shared observation, never Gate B proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from oh_goon.core.ports.physical_observation import (
    OriginalRecoveryPhysicalSample,
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
)
from oh_goon.integrations import linux_physical_observer as kernel
from oh_goon.repository_tooling import local_lace_physical_preflight as cli
from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
    check_x0_single_execution_path,
)
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def facts():
    return PhysicalKernelSnapshot(
        (
            ("process", PhysicalKernelFact("PRESENT", "original PID still alive")),
            ("mounts", PhysicalKernelFact("NOT_OBSERVED", "observer mount namespace only")),
            ("cgroup", PhysicalKernelFact("UNKNOWN", "permission denied or raced")),
        )
    )


def test_original_and_diagnostic_share_exact_snapshot_identity_without_authority(
    tmp_path, monkeypatch
):
    connection, journal, session, owner, scope = seeded(tmp_path)
    original_events = journal.verify("run-one", auth=authenticator())
    shared = facts()
    calls = []

    def same_observer(observed_scope, *, proc_root, **kwargs):
        calls.append((observed_scope, proc_root))
        return shared

    monkeypatch.setattr(kernel, "observe_kernel", same_observer)
    diagnostic = cli.observe(scope, proc_root=tmp_path)
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    assert type(sample) is OriginalRecoveryPhysicalSample
    assert diagnostic["observations"] == sample.facts.to_mapping()
    assert (
        diagnostic["kernel_snapshot_identity"] == sample.kernel_snapshot_identity == shared.identity
    )
    assert diagnostic["classification"] == "DEVELOPMENT_DIAGNOSTIC_ONLY"
    assert diagnostic["gate_b"] == "BLOCKED"
    assert sample.envelope.coverage == "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    assert sample.envelope.observer_custody == "UNVERIFIED"
    assert sample.effect_permission == diagnostic["effect_permission"] == "NONE"
    assert (
        not sample.physical_absence_proven
        and not sample.quiescence_proven
        and not sample.teardown_proven
    )
    assert calls == [(scope, tmp_path), (scope, tmp_path)]  # one per independent user entrypoint
    recorded = journal.verify("run-one", auth=authenticator())
    assert session.commit_count == 2 and recorded[:-1] == original_events
    assert recorded[-1]["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
    connection.close()


@pytest.mark.parametrize(
    "bad",
    [
        (),
        (
            ("cgroup", PhysicalKernelFact("UNKNOWN", "unknown")),
            ("cgroup", PhysicalKernelFact("PRESENT", "bad")),
        ),
        (("a" * 65, PhysicalKernelFact("UNKNOWN", "bad")),),
        (("a-b", PhysicalKernelFact("UNKNOWN", "bad")),),
        (("ok", "not a typed fact"),),
        tuple((f"field{i}", PhysicalKernelFact("UNKNOWN", "bounded")) for i in range(33)),
    ],
)
def test_reject_ambiguous_invalid_or_unbounded_snapshots(bad):
    with pytest.raises(ValueError, match="physical snapshot"):
        PhysicalKernelSnapshot(bad)


@pytest.mark.parametrize(
    "state,detail",
    [
        ("ABSENT", "not allowed"),
        ("PRESENT", "line\npoison"),
        ("UNKNOWN", "x" * 4097),
        ("", ""),
        (None, "none"),
    ],
)
def test_invalid_fact_is_rejected_before_projection(state, detail):
    with pytest.raises(ValueError, match="physical kernel fact"):
        PhysicalKernelFact(state, detail)


def test_shared_identity_bound_to_every_state_detail_and_order_without_promoting_absence():
    snapshot = facts()
    assert snapshot.identity.startswith("sha256:") and len(snapshot.identity) == 71
    assert snapshot.identity == facts().identity
    assert (
        snapshot.identity
        != replace(snapshot, components=tuple(reversed(snapshot.components))).identity
    )
    modified = replace(
        snapshot,
        components=(
            (
                snapshot.components[0][0],
                PhysicalKernelFact("NOT_OBSERVED", snapshot.components[0][1].detail),
            ),
            *snapshot.components[1:],
        ),
    )
    assert snapshot.identity != modified.identity
    modified = replace(
        snapshot,
        components=(
            (snapshot.components[0][0], PhysicalKernelFact("PRESENT", "different detail")),
            *snapshot.components[1:],
        ),
    )
    assert snapshot.identity != modified.identity
    with pytest.raises(FrozenInstanceError):
        snapshot.components[0][1].state = "UNKNOWN"
    assert not hasattr(snapshot, "physical_absence_proven")
    assert not hasattr(snapshot, "effect_permission")


def test_no_second_kernel_reader_and_b25_wsl_module_is_required():
    root = Path(__file__).resolve().parents[4]
    assert check_x0_single_execution_path(root) == []
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    assert "embedded='.checkpoint/B25_SOURCE.sha256'" in script
    assert "required_modules+=(--require-module test_b25_shared_snapshot_parity)" in script
    assert "observer_tests+=(tests/unit/runtime/test_b25_shared_snapshot_parity.py)" in script


def test_x0_guards_shared_identity_projection_against_cloned_or_forged_identity(tmp_path):
    root = Path(__file__).resolve().parents[4]
    for relative in (
        "backend/src/oh_goon/core/ports/physical_observation.py",
        "backend/src/oh_goon/core/ports/original_mount_intent.py",
        "backend/src/oh_goon/integrations/linux_physical_observer.py",
        "backend/src/oh_goon/repository_tooling/local_lace_physical_preflight.py",
        "backend/src/oh_goon/runtime/enforcer_lace_original_physical_sampling.py",
        "backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py",
        "backend/pyproject.toml",
    ):
        dest = tmp_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if (root / relative).exists():
            dest.write_bytes((root / relative).read_bytes())
        else:
            dest.write_text("B25 test source marker")
    from tests.factories.x0_architecture import copy_x0_architecture_sources

    copy_x0_architecture_sources(tmp_path)
    assert check_x0_single_execution_path(tmp_path) == []
    cli_source = (
        tmp_path / "backend/src/oh_goon/repository_tooling/local_lace_physical_preflight.py"
    )
    original = cli_source.read_text()
    assert '"kernel_snapshot_identity": snapshot.identity' in original
    cli_source.write_text(
        original.replace(
            '"kernel_snapshot_identity": snapshot.identity',
            '"kernel_snapshot_identity": "sha256:" + "f" * 64',
        )
    )
    assert any(
        "B25 shared snapshot identity" in issue.message
        for issue in check_x0_single_execution_path(tmp_path)
    )
