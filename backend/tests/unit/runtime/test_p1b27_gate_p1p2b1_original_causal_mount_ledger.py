"""P1/P2B1: one original read-only sample, bound to exact Run and mount ledger."""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded
from tests.unit.runtime.test_p1b27_gate_p1p2_original_sample_correspondence import _facts


def _valid_ledger():
    return _facts()


@pytest.mark.parametrize(
    "detail",
    (
        "tracked process mount namespace=mnt:[47]; target_kernel_sha256=" + "a" * 64,
        "tracked process mount namespace=mnt:[47]; target_mount_ids=[43]; visible_targets=0/1; ambiguous_target_mounts=False; target_kernel_sha256="
        + "a" * 64,
        "tracked process mount namespace=mnt:[47]; target_mount_ids=[43]; visible_targets=1/2; ambiguous_target_mounts=False; target_kernel_sha256="
        + "a" * 64,
        "tracked process mount namespace=mnt:[47]; target_mount_ids=[43, 43]; visible_targets=1/1; ambiguous_target_mounts=False; target_kernel_sha256="
        + "a" * 64,
        "tracked process mount namespace=mnt:[47]; target_mount_ids=[0]; visible_targets=1/1; ambiguous_target_mounts=False; target_kernel_sha256="
        + "a" * 64,
        "tracked process mount namespace=mnt:[47]; target_mount_ids=[43]; visible_targets=1/1; ambiguous_target_mounts=True; target_kernel_sha256="
        + "a" * 64,
        "tracked process mount namespace=mnt:[47]; target_mount_ids=[43]; visible_targets=1/1; ambiguous_target_mounts=False; target_kernel_sha256="
        + "a" * 64
        + "; visible_targets=0/1",
    ),
)
def test_false_positive_native_mount_ledger_is_rejected_before_owner_second_cut(
    tmp_path, monkeypatch, detail
):
    conn, journal, session, owner, scope = seeded(tmp_path)
    original_events = journal.verify("run-one", auth=authenticator())
    monkeypatch.setattr(
        linux_physical_observer,
        "observe_kernel",
        lambda *_args, **_kwargs: _facts(tracked_detail=detail),
    )
    with pytest.raises(CausalJournalError, match="visible mount"):
        owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
        )
    assert session.commit_count == 1
    assert journal.verify("run-one", auth=authenticator()) == original_events
    conn.close()


def test_causal_identity_binds_original_claim_scope_and_kernel_without_authority(
    tmp_path, monkeypatch
):
    conn, journal, session, owner, scope = seeded(tmp_path)
    facts = _valid_ledger()
    monkeypatch.setattr(linux_physical_observer, "observe_kernel", lambda *_args, **_kwargs: facts)
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    identity = sample.causal_observation_identity
    assert identity == digest(
        "oh-goon/b27/original-physical-candidate/v1",
        canonical_bytes(
            {
                "envelope_identity": sample.envelope.identity,
                "scope": {
                    **asdict(sample.scope),
                    "mount_targets": list(sample.scope.mount_targets),
                },
                "kernel_snapshot_identity": sample.facts.identity,
                "mount_intent_identity": sample.mount_intent_identity,
                "classification": sample.classification,
                "effect_permission": sample.effect_permission,
            }
        ),
    )
    assert identity.startswith("sha256:") and len(identity) == 71
    assert identity == sample.causal_observation_identity
    assert sample.kernel_snapshot_identity == facts.identity
    assert identity != facts.identity  # raw observations are NOT Run-bound
    # A different signed inventory with IDENTICAL kernel facts cannot replay.
    different_envelope = replace(sample.envelope, inventory_identity="sha256:" + "f" * 64)
    assert replace(sample, envelope=different_envelope).causal_observation_identity != identity
    # Even a scoped network-resource identity change changes the candidate.
    changed_scope = replace(sample.scope, network_namespace="different")
    assert replace(sample, scope=changed_scope).causal_observation_identity != identity
    # The checked shared snapshot state/detail is part of the causal identity.
    changed_facts = PhysicalKernelSnapshot(
        (("tracked_mount_namespace", PhysicalKernelFact("UNKNOWN", "probe raced")),)
    )
    assert replace(sample, facts=changed_facts).causal_observation_identity != identity
    assert not sample.physical_absence_proven and not sample.quiescence_proven
    assert not sample.teardown_proven and sample.effect_permission == "NONE"
    assert sample.envelope.observer_custody == "UNVERIFIED"
    conn.close()


def test_incomplete_kernel_read_retains_unqualified_causal_identity(tmp_path, monkeypatch):
    conn, _, _, owner, scope = seeded(tmp_path)
    snapshot = PhysicalKernelSnapshot(
        (
            ("tracked_mount_namespace", PhysicalKernelFact("UNKNOWN", "mountinfo race")),
            ("visible_mount_fd_holders", PhysicalKernelFact("NOT_OBSERVED", "scoped nondetection")),
        )
    )
    monkeypatch.setattr(
        linux_physical_observer, "observe_kernel", lambda *_args, **_kwargs: snapshot
    )
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    assert sample.causal_observation_identity.startswith("sha256:")
    assert sample.classification == "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
    assert not sample.physical_absence_proven and not sample.teardown_proven
    conn.close()
