"""Positive kernel holder claims fail before ORIGINAL journal mutation.

The original read-only observer is not independent physical custody.  These
regressions only prohibit internally contradictory unqualified samples.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded
from tests.unit.runtime.test_p1b27_gate_p1p2_original_sample_correspondence import _facts


def _modify(snapshot: PhysicalKernelSnapshot, *, tracked_state: str | None = None,
            holder_state: str | None = None, tracked_detail: str | None = None,
            holder_detail: str | None = None, omit_tracked: bool = False):
    entries = []
    for name, fact in snapshot.components:
        if omit_tracked and name == 'tracked_mount_namespace':
            continue
        if name == 'tracked_mount_namespace':
            fact = PhysicalKernelFact(tracked_state or fact.state,
                                      tracked_detail if tracked_detail is not None else fact.detail)
        if name == 'visible_mount_fd_holders':
            fact = PhysicalKernelFact(holder_state or fact.state,
                                      holder_detail if holder_detail is not None else fact.detail)
        entries.append((name, fact))
    return PhysicalKernelSnapshot(tuple(entries))


@pytest.mark.parametrize('attack', (
    'valid', 'no-tracked-component', 'tracked-unknown', 'tracked-not-observed',
    'duplicate-tracked-hash', 'duplicate-holder-hash', 'duplicate-holder-namespace',
    'missing-holder-census', 'zero-matching-fds', 'too-many-matching-fds',
    'wrong-mount-count', 'zero-pids', 'duplicate-matching-counter',
    'missing-inventory-hash',
))
def test_original_journal_positive_holder_claim_must_be_correlated(tmp_path: Path, monkeypatch, attack: str):
    conn, journal, session, owner, scope = seeded(tmp_path)
    baseline = journal.verify('run-one', auth=authenticator())
    initial = _facts()
    holder = initial.to_mapping()['visible_mount_fd_holders']['detail']
    tracked = initial.to_mapping()['tracked_mount_namespace']['detail']
    if attack == 'no-tracked-component':
        facts = _modify(initial, omit_tracked=True)
    elif attack == 'tracked-unknown':
        facts = _modify(initial, tracked_state='UNKNOWN')
    elif attack == 'tracked-not-observed':
        facts = _modify(initial, tracked_state='NOT_OBSERVED')
    elif attack == 'duplicate-tracked-hash':
        facts = _modify(initial, tracked_detail=tracked + '; target_kernel_sha256=' + 'b' * 64)
    elif attack == 'duplicate-holder-hash':
        facts = _modify(initial, holder_detail=holder + '; target_kernel_sha256=' + 'b' * 64)
    elif attack == 'duplicate-holder-namespace':
        facts = _modify(initial, holder_detail=holder + '; original_namespace=mnt:[999]')
    elif attack == 'missing-holder-census':
        facts = _modify(initial, holder_detail=holder.replace('matching_mount_fds=1; ', ''))
    elif attack == 'zero-matching-fds':
        facts = _modify(initial, holder_detail=holder.replace('matching_mount_fds=1', 'matching_mount_fds=0'))
    elif attack == 'too-many-matching-fds':
        facts = _modify(initial, holder_detail=holder.replace('matching_mount_fds=1', 'matching_mount_fds=3'))
    elif attack == 'wrong-mount-count':
        facts = _modify(initial, holder_detail=holder.replace('target_mount_ids=1', 'target_mount_ids=2'))
    elif attack == 'zero-pids':
        facts = _modify(initial, holder_detail=holder.replace('visible_pids=1', 'visible_pids=0'))
    elif attack == 'duplicate-matching-counter':
        facts = _modify(initial, holder_detail=holder + '; matching_mount_fds=2')
    elif attack == 'missing-inventory-hash':
        facts = _modify(initial, holder_detail=holder.replace('inventory_sha256=' + 'a' * 64 + '; ', ''))
    else:
        facts = initial
    reads = []
    def observe(s, *, proc_root):
        reads.append((s, proc_root))
        return facts
    monkeypatch.setattr(linux_physical_observer, 'observe_kernel', observe)
    try:
        if attack == 'valid':
            sample = owner.sample_recovery_physical_unqualified(
                intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path)
            assert sample.facts.identity == facts.identity
            assert sample.envelope.observer_custody == 'UNVERIFIED'
            assert not sample.physical_absence_proven and not sample.teardown_proven
            assert journal.verify('run-one', auth=authenticator())[-1]['kind'] == 'RESOURCE_PHYSICAL_SAMPLE_RECORDED'
            assert session.commit_count == 2
        else:
            with pytest.raises(CausalJournalError, match='visible mount|positive mount FD'):
                owner.sample_recovery_physical_unqualified(
                    intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path)
            assert journal.verify('run-one', auth=authenticator()) == baseline
            assert session.commit_count == 1
        assert reads == [(scope, tmp_path)]
    finally:
        conn.close()


def test_missing_mount_evidence_never_promotes_scoped_nondetection(tmp_path: Path, monkeypatch):
    conn, journal, session, owner, scope = seeded(tmp_path)
    snapshot = _modify(_facts(), tracked_state='UNKNOWN', holder_state='NOT_OBSERVED',
                       tracked_detail='mount probe unavailable', holder_detail='FD probe scoped only')
    monkeypatch.setattr(linux_physical_observer, 'observe_kernel', lambda s, *, proc_root: snapshot)
    try:
        sample = owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path)
        assert sample.effect_permission == 'NONE'
        assert not sample.physical_absence_proven and not sample.quiescence_proven
        assert journal.verify('run-one', auth=authenticator())[-1]['payload']['observer_custody'] == 'UNVERIFIED'
        assert session.commit_count == 2
    finally:
        conn.close()
