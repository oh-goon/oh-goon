"""B26 native orphan process provenance, never independent physical absence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12d2b_authenticated_orphan_inventory import observed, record, subject
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded

GOOD = (123, "boot-A:999", "mnt:[47]", "sha256:" + "a" * 64)
OTHER = (124, "boot-A:1000", "mnt:[48]", "sha256:" + "b" * 64)


def _sample_with_roster(tmp_path, monkeypatch, roster):
    conn, journal, session, owner, scope = seeded(tmp_path, inventory=False)
    item = subject(journal)
    inv = replace(observed(item), cgroup_processes=tuple(roster))
    record(journal, item, inv)
    original_events = journal.verify("run-one", auth=authenticator())
    scans = []

    def observe(scope, *, proc_root):
        scans.append((scope, proc_root))
        return PhysicalKernelSnapshot((("process", PhysicalKernelFact("PRESENT", "bounded")),))

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", observe)
    return conn, journal, session, owner, scope, original_events, scans


def test_single_original_inventory_roster_is_stably_bound_to_exact_envelope(tmp_path, monkeypatch):
    conn, journal, session, owner, scope, events, scans = _sample_with_roster(
        tmp_path, monkeypatch, [GOOD, OTHER]
    )
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    assert sample.envelope.observed_processes == (GOOD, OTHER)
    assert sample.envelope.native_cgroup_device == sample.scope.cgroup_device
    assert sample.envelope.native_cgroup_inode == sample.scope.cgroup_inode
    assert sample.envelope.coverage == "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    assert sample.envelope.observer_custody == "UNVERIFIED"
    assert sample.envelope.effect_permission == sample.effect_permission == "NONE"
    assert (
        not sample.physical_absence_proven
        and not sample.quiescence_proven
        and not sample.teardown_proven
    )
    assert scans == [(scope, tmp_path)]
    assert session.commit_count == 2
    recorded = journal.verify("run-one", auth=authenticator())
    assert recorded[:-1] == events and recorded[-1]["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
    altered = replace(sample.envelope, observed_processes=(GOOD,))
    assert altered.identity != sample.envelope.identity
    assert sample.envelope.identity.startswith("sha256:")
    assert len(sample.envelope.identity) == 71
    with pytest.raises(ValueError, match="cannot grant authority"):
        replace(sample.envelope, observed_processes=(GOOD,) * 4097)
    with pytest.raises(ValueError, match="cannot grant authority"):
        replace(sample.envelope, native_cgroup_inode=0)
    with pytest.raises(ValueError, match="cannot mint physical authority"):
        replace(sample, scope=replace(scope, tracked_pid=9999))
    conn.close()


@pytest.mark.parametrize(
    "roster",
    [
        (),
        (GOOD, GOOD),
        (OTHER, GOOD),
        ((123, "boot-A:888", GOOD[2], GOOD[3]),),
        (GOOD, (124, "boot-B:1000", OTHER[2], OTHER[3])),
        (GOOD, (123, "boot-A:1000", OTHER[2], OTHER[3])),
        ((123, "boot-A:999", "mnt:[0]", GOOD[3]),),
        ((123, "boot-A:999", "mnt:extra", GOOD[3]),),
        ((123, "boot-A:999", GOOD[2], "sha256:" + "z" * 64),),
        (
            (123, "boot-A:999", GOOD[2], GOOD[3]),
            (124, "boot-A:1", OTHER[2], "sha256:" + "a" * 64),
            (123, "boot-A:999", GOOD[2], GOOD[3]),
        ),
    ],
)
def test_ambiguous_or_missing_signed_roster_rejects_before_kernel_read(
    tmp_path, monkeypatch, roster
):
    conn, journal, session, owner, scope, events, scans = _sample_with_roster(
        tmp_path, monkeypatch, roster
    )
    with pytest.raises(CausalJournalError):
        owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
        )
    assert scans == []
    assert journal.verify("run-one", auth=authenticator()) == events
    conn.close()
