"""P3B1: causal post-cleanup visibility is informative, not physical absence."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from oh_goon.core.ports.local_lace_cleanup import NativeCleanupResult
from oh_goon.core.ports.physical_observation import (
    ORIGINAL_KERNEL_COMPONENTS,
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
)
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def observed(monkeypatch, states):
    sequence = iter(states)

    def read(scope, *, proc_root):
        state = next(sequence)
        return PhysicalKernelSnapshot(
            tuple(
                (
                    name,
                    PhysicalKernelFact(
                        state if name == "cgroup" else "NOT_OBSERVED", "bounded scoped kernel probe"
                    ),
                )
                for name in ORIGINAL_KERNEL_COMPONENTS
            )
        )

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", read)


def read(owner, scope):
    return owner.assess_recovery_physical_convergence_unqualified(
        intent_run_seq=2,
        scope=scope,
        mount_intent=mount_descriptor(),
    )


def scan(owner, scope, path):
    return owner.sample_recovery_physical_unqualified(
        intent_run_seq=2,
        scope=scope,
        mount_intent=mount_descriptor(),
        proc_root=path,
    )


def cleanup(owner):
    subject = owner.rediscovery_subject(intent_run_seq=2)
    ticket = owner.prepare_cleanup(subject)
    return owner.execute_native_cleanup(
        ticket,
        lambda: NativeCleanupResult(
            ("CGROUP",),
            "CLEANUP_ACTIONS_ATTEMPTED",
        ),
    )


def assert_unqualified(result):
    assert result.observer_custody == "UNVERIFIED"
    assert result.coverage == "INCOMPLETE_NATIVE_RESOURCE_SCOPE"
    assert result.effect_permission == "NONE"
    assert result.physical_absence_proven is False
    assert result.quiescence_proven is False
    assert result.teardown_proven is False
    assert {x.name for x in fields(result)} == {
        "state",
        "post_cleanup_rounds",
        "cleanup_started_run_seq",
        "cleanup_recorded_run_seq",
        "last_sample_run_seq",
        "observer_custody",
        "coverage",
        "physical_absence_proven",
        "quiescence_proven",
        "teardown_proven",
        "effect_permission",
    }


def test_not_started_and_not_sampled_cannot_become_absence(tmp_path, monkeypatch):
    conn, journal, _, owner, scope = seeded(tmp_path)
    try:
        before = journal.verify("run-one", auth=authenticator())
        result = read(owner, scope)
        assert result.state == "CLEANUP_NOT_STARTED"
        assert_unqualified(result)
        assert journal.verify("run-one", auth=authenticator()) == before
    finally:
        conn.close()


def test_post_cleanup_requires_two_scoped_distinct_rounds_and_never_proves_absence(
    tmp_path, monkeypatch
):
    conn, journal, _, owner, scope = seeded(tmp_path)
    observed(monkeypatch, ["NOT_OBSERVED"] * 3)
    try:
        scan(owner, scope, tmp_path)  # pre-cleanup, cannot count
        cleanup(owner)
        result = read(owner, scope)
        assert result.state == "NO_POST_CLEANUP_OBSERVATION"
        scan(owner, scope, tmp_path)
        result = read(owner, scope)
        assert result.state == "INSUFFICIENT_POST_CLEANUP_ROUNDS"
        assert result.post_cleanup_rounds == 1
        scan(owner, scope, tmp_path)
        result = read(owner, scope)
        assert result.state == "REPEATED_SCOPED_NONDETECTION"
        assert result.post_cleanup_rounds == 2
        assert_unqualified(result)
        assert (
            len(
                [
                    e
                    for e in journal.verify("run-one", auth=authenticator())
                    if e["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
                ]
            )
            == 3
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "states,expected",
    [
        (["PRESENT", "NOT_OBSERVED"], "VISIBLE_RESOURCES_REMAIN"),
        (["NOT_OBSERVED", "PRESENT"], "VISIBLE_RESOURCES_REMAIN"),
        (["UNKNOWN", "NOT_OBSERVED"], "INCOMPLETE_OR_CHANGED_SCOPE"),
        (["NOT_OBSERVED", "UNKNOWN"], "INCOMPLETE_OR_CHANGED_SCOPE"),
    ],
)
def test_surviving_resource_or_incomplete_probe_fails_closed(
    tmp_path, monkeypatch, states, expected
):
    conn, journal, _, owner, scope = seeded(tmp_path)
    observed(monkeypatch, states)
    try:
        cleanup(owner)
        scan(owner, scope, tmp_path)
        scan(owner, scope, tmp_path)
        result = read(owner, scope)
        assert result.state == expected
        assert_unqualified(result)
    finally:
        conn.close()


def test_interrupted_cleanup_never_reaches_convergence(tmp_path, monkeypatch):
    conn, journal, _, owner, scope = seeded(tmp_path)
    observed(monkeypatch, ["NOT_OBSERVED"] * 2)
    try:
        subject = owner.rediscovery_subject(intent_run_seq=2)
        ticket = owner.prepare_cleanup(subject)
        with pytest.raises(KeyboardInterrupt):
            owner.execute_native_cleanup(ticket, lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        scan(owner, scope, tmp_path)
        scan(owner, scope, tmp_path)
        result = read(owner, scope)
        assert result.state == "CLEANUP_INTERRUPTED"
        assert_unqualified(result)
    finally:
        conn.close()


def test_original_lease_fence_rejects_unauthorized_assessment(tmp_path):
    conn, journal, session, owner, scope = seeded(tmp_path)
    try:
        session.token = "foreign"
        with pytest.raises(CausalJournalError):
            read(owner, scope)
        assert not any(
            e["kind"].startswith("RESOURCE_CONVERGENCE")
            for e in journal.verify("run-one", auth=authenticator())
        )
    finally:
        conn.close()


def test_scope_change_in_post_cleanup_round_is_not_merged(tmp_path, monkeypatch):
    # Test a valid journal containing two individually authenticated, differently
    # scoped original rounds: never compare them as one convergence window.
    from dataclasses import replace

    conn, journal, _, owner, scope = seeded(tmp_path)
    observed(monkeypatch, ["NOT_OBSERVED"] * 2)
    try:
        cleanup(owner)
        scan(owner, scope, tmp_path)
        # Changing the original signed scope to an unrelated mount target must
        # be rejected by the owner before a second sampling attempt.
        hostile = replace(scope, mount_targets=("/foreign",))
        with pytest.raises(CausalJournalError):
            scan(owner, hostile, tmp_path)
        assert read(owner, scope).state == "INSUFFICIENT_POST_CLEANUP_ROUNDS"
    finally:
        conn.close()


def test_cannot_elevate_scoped_nondetection_by_rehashing_event(tmp_path, monkeypatch):
    # Original P3A journal verification remains the authority for event integrity.
    from oh_goon.core.enforcer_lace_trust import canonical_bytes
    from oh_goon.runtime.enforcer_lace_journal import _event_identity

    conn, journal, _, owner, scope = seeded(tmp_path)
    observed(monkeypatch, ["NOT_OBSERVED"] * 2)
    try:
        cleanup(owner)
        scan(owner, scope, tmp_path)
        scan(owner, scope, tmp_path)
        row = conn.execute(
            "SELECT run_seq,event_kind,event_payload,previous_identity "
            "FROM lace_run_events WHERE run_id=? ORDER BY run_seq DESC LIMIT 1",
            ("run-one",),
        ).fetchone()
        number, kind, encoded, previous = row
        payload = json.loads(encoded)
        payload["physical_absence_proven"] = True
        updated = canonical_bytes(payload)
        eid = _event_identity("run-one", number, kind, updated, previous)
        conn.execute(
            "UPDATE lace_run_events SET event_payload=?,event_identity=? "
            "WHERE run_id=? AND run_seq=?",
            (updated, eid, "run-one", number),
        )
        conn.execute(
            "UPDATE lace_run_authority SET head_identity=? WHERE run_id=?", (eid, "run-one")
        )
        with pytest.raises(CausalJournalError):
            read(owner, scope)
    finally:
        conn.close()


def test_x0_rejects_convergence_bypass_and_duplicate_authority(tmp_path):
    from shutil import copy2

    from oh_goon.repository_tooling.architecture.repository_layout import ROOT
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        _check_p3b1_original_convergence_assessment,
    )

    root = ROOT / "backend/src/oh_goon"
    paths = [
        "runtime/enforcer_lace_original_physical_sampling.py",
        "runtime/enforcer_lace_fleet_resource_owner.py",
    ]
    for rel in paths:
        dest = tmp_path / "runtime" / rel.split("/")[-1]
        dest.parent.mkdir(parents=True, exist_ok=True)
        copy2(root / rel, dest)
    assert _check_p3b1_original_convergence_assessment(tmp_path) == []
    for rel, token in (
        (paths[0], 'fact["state"] == "PRESENT"'),
        (paths[0], "len(post) < 2"),
        (paths[0], "binding.envelope.identity"),
        (paths[1], "journal.verify(self.run_id, auth=self.authenticator)"),
        (paths[1], "self.session.original_fleet_lease_token()"),
    ):
        target = tmp_path / "runtime" / rel.split("/")[-1]
        original = target.read_text()
        assert token in original
        if rel == paths[1]:
            head, body = original.split(
                "    def assess_recovery_physical_convergence_unqualified", 1
            )
            modified = (
                head
                + "    def assess_recovery_physical_convergence_unqualified"
                + body.replace(token, "None", 1)
            )
        else:
            modified = original.replace(token, "None", 1)
        target.write_text(modified)
        assert _check_p3b1_original_convergence_assessment(tmp_path)
        target.write_text(original)
