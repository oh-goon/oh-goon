"""P1/P2 bridge: one Fleet owner binds kernel-positive identity to native signed roster.

Synthetic receipt tests; NOT independent physical custody, signed fact publication,
real Bubblewrap FD consumption, disappearance, or Gate B approval.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def _facts(
    *,
    namespace: str = "mnt:[47]",
    holder_namespace: str = "mnt:[47]",
    target: str = "a" * 64,
    holder_target: str = "a" * 64,
    tracked_detail: str | None = None,
    holder_detail: str | None = None,
):
    return PhysicalKernelSnapshot(
        (
            (
                "tracked_mount_namespace",
                PhysicalKernelFact(
                    "PRESENT",
                    tracked_detail
                    if tracked_detail is not None
                    else f"tracked process mount namespace={namespace}; target_mount_ids=[43]; "
                    f"visible_targets=1/1; ambiguous_target_mounts=False; "
                    f"target_kernel_sha256={target}; local-only",
                ),
            ),
            (
                "visible_mount_fd_holders",
                PhysicalKernelFact(
                    "PRESENT",
                    holder_detail
                    if holder_detail is not None
                    else f"visible_pids=1; original_namespace={holder_namespace}; target_mount_ids=1; "
                    f"target_kernel_sha256={holder_target}; inspected_fds=2; matching_mount_fds=1; "
                    f"inventory_sha256={'a' * 64}; local-only",
                ),
            ),
        )
    )


@pytest.mark.parametrize(
    "attack",
    (
        "valid",
        "foreign-namespace",
        "fd-foreign-namespace",
        "foreign-mount-roster",
        "missing-tracked-namespace",
        "missing-fd-identity",
        "missing-target-identity",
    ),
)
def test_original_owner_fences_positive_native_mount_fd_identity(
    tmp_path: Path, monkeypatch, attack: str
):
    conn, journal, session, owner, scope = seeded(tmp_path)
    records = journal.verify("run-one", auth=authenticator())
    fact = {
        "valid": _facts(),
        "foreign-namespace": _facts(namespace="mnt:[48]"),
        "fd-foreign-namespace": _facts(holder_namespace="mnt:[48]"),
        "foreign-mount-roster": _facts(holder_target="b" * 64),
        "missing-tracked-namespace": _facts(tracked_detail="target_kernel_sha256=" + "a" * 64),
        "missing-fd-identity": _facts(holder_detail="target_kernel_sha256=" + "a" * 64),
        "missing-target-identity": _facts(
            tracked_detail="tracked process mount namespace=mnt:[47]"
        ),
    }[attack]
    seen = []

    def read_shared_kernel(s, *, proc_root):
        seen.append((s, proc_root))
        return fact

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", read_shared_kernel)
    if attack == "valid":
        sample = owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
        )
        assert sample.kernel_snapshot_identity == fact.identity
        assert sample.envelope.observer_custody == "UNVERIFIED"
        assert sample.effect_permission == "NONE"
        assert not sample.physical_absence_proven and not sample.teardown_proven
    else:
        with pytest.raises(CausalJournalError, match="visible mount"):
            owner.sample_recovery_physical_unqualified(
                intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
            )
    assert seen == [(scope, tmp_path)]
    recorded = journal.verify("run-one", auth=authenticator())
    if attack == "valid":
        assert (
            recorded[:-1] == records and recorded[-1]["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
        )
    else:
        assert recorded == records
    assert session.commit_count == (2 if attack == "valid" else 1)
    conn.close()


def test_unknown_namespace_does_not_become_missing_or_absence_proof(tmp_path, monkeypatch):
    conn, journal, session, owner, scope = seeded(tmp_path)
    snapshot = PhysicalKernelSnapshot(
        (
            ("tracked_mount_namespace", PhysicalKernelFact("UNKNOWN", "kernel probe raced")),
            ("visible_mount_fd_holders", PhysicalKernelFact("NOT_OBSERVED", "local view only")),
        )
    )
    monkeypatch.setattr(linux_physical_observer, "observe_kernel", lambda s, *, proc_root: snapshot)
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    assert sample.kernel_snapshot_identity == snapshot.identity
    assert sample.classification == "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
    assert not sample.physical_absence_proven and not sample.quiescence_proven
    assert session.commit_count == 2
    conn.close()


def test_one_shared_reader_and_original_wsl_qualification_owner():
    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        check_x0_single_execution_path,
    )

    assert check_x0_single_execution_path(ROOT) == []
    text = (ROOT / "scripts/qualify_b12_development_wsl.sh").read_text()
    module = "test_p1b27_gate_p1p2_original_sample_correspondence"
    assert text.count(f"--require-module {module}") == 1
    assert text.count(f"tests/unit/runtime/{module}.py") == 1
    assert text.index(f"--require-module {module}") < text.index(f"tests/unit/runtime/{module}.py")
