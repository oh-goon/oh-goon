"""B22: original owner single-reader scope binding; no second physical fact publisher."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.ports.original_mount_intent import (
    OriginalMountIntentDescriptor,
    OriginalSandboxMount,
)
from oh_goon.core.ports.physical_observation import (
    OriginalRecoveryPhysicalSample,
    PhysicalKernelFact,
    PhysicalKernelSnapshot,
    PhysicalObservationScope,
)
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_fleet_resource_owner import OriginalFleetLocalLaceRecoveryOwner
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b12_local_resource_intent import _reserve, _store
from tests.unit.runtime.test_b12c2_original_fleet_local_lace import _OriginalSession
from tests.unit.runtime.test_b12d1_native_allocation_journal import observation, publish
from tests.unit.runtime.test_b12d2_recovery_claims import _claim
from tests.unit.runtime.test_b12d2b_authenticated_orphan_inventory import observed, record, subject


def seeded(tmp_path: Path, *, inventory: bool = True):
    conn, journal = _store(tmp_path / "original.sqlite")
    mount_intent = OriginalMountIntentDescriptor(
        (OriginalSandboxMount("/source/snapshot", "/original/mount", "a" * 64),), ()
    )
    from tests.unit.runtime.test_b12_local_resource_intent import _resources

    assert (
        _reserve(journal, resources={**_resources(), "mounts": mount_intent.resource_identity}) == 2
    )
    from dataclasses import replace

    from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest
    from tests.unit.runtime.test_b12_local_resource_intent import _resources

    receipt = replace(
        observation(),
        resource_intent_identity=digest(
            "oh-goon/b12d/resource-binding/v1",
            canonical_bytes({**_resources(), "mounts": mount_intent.resource_identity}),
        ),
    )
    publish(journal, receipt=receipt)
    _claim(journal)
    item = subject(journal)
    inv = replace(
        observed(item), cgroup_processes=((123, "boot-A:999", "mnt:[47]", "sha256:" + "a" * 64),)
    )
    if inventory:
        record(journal, item, inv)
    original = observation()
    scope = PhysicalObservationScope(
        "run-one",
        item.subject_identity,
        original.cgroup_path,
        123,
        456,
        123,
        "boot-A:999",
        ("/original/mount",),
        original.network_namespace,
        original.host_veth,
        original.nft_table,
    )
    session = _OriginalSession(conn)
    owner = OriginalFleetLocalLaceRecoveryOwner(
        session,
        lambda _: None,
        authenticator(),
        "run-one",
        admitted_executor_generation=8,
        admitted_issuer_key_id="key-1",
    )
    return conn, journal, session, owner, scope


def mount_descriptor():
    return OriginalMountIntentDescriptor(
        (OriginalSandboxMount("/source/snapshot", "/original/mount", "a" * 64),), ()
    )


def mock_linux(monkeypatch):
    calls = []
    facts = PhysicalKernelSnapshot(
        (("cgroup", PhysicalKernelFact("NOT_OBSERVED", "local read-only")),)
    )

    def observe(scope, *, proc_root):
        calls.append((scope, proc_root))
        return facts

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", observe)
    return calls


def test_original_owner_samples_exact_shared_mechanism_once_with_unqualified_journal_record(
    tmp_path, monkeypatch
):
    conn, journal, session, owner, scope = seeded(tmp_path)
    calls = mock_linux(monkeypatch)
    original = journal.verify("run-one", auth=authenticator())
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    assert type(sample) is OriginalRecoveryPhysicalSample
    assert sample.scope is scope and sample.subject_identity == scope.subject_identity
    assert sample.claim_run_seq == 4 and sample.control_epoch == 1
    assert sample.classification == "ORIGINAL_OWNER_READ_ONLY_UNQUALIFIED"
    assert sample.effect_permission == "NONE"
    assert not (
        sample.physical_absence_proven or sample.quiescence_proven or sample.teardown_proven
    )
    assert sample.facts.to_mapping()["cgroup"]["state"] == "NOT_OBSERVED"
    with pytest.raises(ValueError, match="cannot mint physical authority"):
        replace(sample, teardown_proven=True)
    with pytest.raises(ValueError, match="cannot mint physical authority"):
        replace(sample, classification="QUIESCENCE_PROVEN")
    assert calls == [(scope, tmp_path)]
    events = journal.verify("run-one", auth=authenticator())
    assert events[:-1] == original
    assert events[-1]["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
    assert events[-1]["payload"]["sample_identity"] == sample.causal_observation_identity
    assert session.commit_count == 2  # one read cut and one validated append cut
    conn.close()


@pytest.mark.parametrize(
    "attack",
    [
        "run",
        "subject",
        "cgroup-path",
        "device",
        "inode",
        "pid",
        "birth",
        "network",
        "issuer",
        "generation",
        "unprovisioned",
        "lease",
        "cancel",
        "missing-inventory",
        "missing-claim",
    ],
)
def test_preobservation_attack_denied_before_any_kernel_read(tmp_path, monkeypatch, attack):
    conn, journal, session, owner, scope = seeded(tmp_path, inventory=attack != "missing-inventory")
    if attack == "run":
        scope = replace(scope, run_id="foreign")
    if attack == "subject":
        scope = replace(scope, subject_identity="sha256:" + "f" * 64)
    if attack == "cgroup-path":
        scope = replace(scope, cgroup_path="/tmp/foreign")
    if attack == "device":
        scope = replace(scope, cgroup_device=345)
    if attack == "inode":
        scope = replace(scope, cgroup_inode=888)
    if attack == "pid":
        scope = replace(scope, tracked_pid=33)
    if attack == "birth":
        scope = replace(scope, tracked_birth="boot-B:100")
    if attack == "network":
        scope = replace(scope, host_veth="foreign")
    if attack == "issuer":
        owner = replace(owner, admitted_issuer_key_id="foreign")
    if attack == "generation":
        owner = replace(owner, admitted_executor_generation=9)
    if attack == "unprovisioned":
        owner = replace(owner, admitted_issuer_key_id=None)
    if attack == "lease":
        session.token = "foreign"
    if attack == "cancel":
        session.cancelled = True
    if attack == "missing-claim":
        # A foreign source Run cannot satisfy this original claim identity.
        owner = replace(owner, run_id="other")
    calls = mock_linux(monkeypatch)
    before = journal.verify("run-one", auth=authenticator())
    with pytest.raises((CausalJournalError, ValueError, KeyError)):
        owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=mount_descriptor()
        )
    assert calls == []
    assert journal.verify("run-one", auth=authenticator()) == before
    conn.close()


@pytest.mark.parametrize("attack", ["transfer", "cancel", "journal-drift", "original-revoked"])
def test_observer_race_never_returns_fact_or_writes_journal(tmp_path, monkeypatch, attack):
    conn, journal, session, owner, scope = seeded(tmp_path)
    original = journal.verify("run-one", auth=authenticator())
    calls = []

    def observer(_scope, *, proc_root):
        calls.append(True)
        if attack == "transfer":
            journal.transfer_control(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                next_epoch=2,
                next_lease_identity="successor",
            )
        elif attack == "cancel":
            session.cancelled = True
        elif attack == "journal-drift":
            journal.append(
                "run-one",
                epoch=1,
                lease_identity="lease-one",
                kind="RUN_CONTROL_FENCED",
                payload={"reason": "test"},
            )
        else:
            session.token = "foreign"
        return PhysicalKernelSnapshot((("process", PhysicalKernelFact("PRESENT", "observed")),))

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", observer)
    with pytest.raises(CausalJournalError):
        owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=mount_descriptor()
        )
    assert calls == [True]
    # The observer never writes journal facts. Any drift is solely test-injected.
    events = journal.verify("run-one", auth=authenticator())
    assert not any(e["kind"] in ("QUIESCENCE_PROVEN", "TEARDOWN_PROVEN") for e in events)
    assert len(events) == len(original) + int(attack == "journal-drift")
    conn.close()


def test_exact_source_wsl_ring_includes_original_owner_negative_contract():
    root = Path(__file__).resolve().parents[4]
    script = (root / "scripts/qualify_b12_development_wsl.sh").read_text()
    makefile = (root / "Makefile").read_text()
    assert (
        "if [[ -f .checkpoint/B22_SOURCE.sha256 ]]; then embedded='.checkpoint/B22_SOURCE.sha256'; fi"
        in script
    )
    assert "required_modules+=(--require-module test_b22_original_physical_sampling)" in script
    assert "observer_tests+=(tests/unit/runtime/test_b22_original_physical_sampling.py)" in script
    recipe = makefile.split("qualify-development-wsl:", 1)[1].split("\n\n", 1)[0]
    assert recipe.index("source-transport-clean") < recipe.index("qualify_b12_development_wsl.sh")


def test_x0_guard_rejects_second_production_kernel_reader(tmp_path):
    from oh_goon.repository_tooling.architecture.x0_single_execution_contract import (
        check_x0_single_execution_path,
    )

    root = Path(__file__).resolve().parents[4]
    relative = [
        "backend/src/oh_goon/repository_tooling/local_lace_physical_preflight.py",
        "backend/src/oh_goon/integrations/linux_physical_observer.py",
        "backend/src/oh_goon/core/ports/physical_observation.py",
        "backend/src/oh_goon/runtime/enforcer_lace_original_physical_sampling.py",
        "backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py",
        "backend/pyproject.toml",
    ]
    for name in relative:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((root / name).read_bytes())
    from tests.factories.x0_architecture import copy_x0_architecture_sources

    copy_x0_architecture_sources(tmp_path)
    assert check_x0_single_execution_path(tmp_path) == []
    owner = tmp_path / "backend/src/oh_goon/runtime/enforcer_lace_fleet_resource_owner.py"
    text = owner.read_text()
    source = "facts = linux_physical_observer.observe_kernel(scope, proc_root=proc_root)"
    assert source in text
    owner.write_text(text.replace(source, source + "\n        " + source))
    assert any(
        "exactly one shared kernel reader" in e.message
        for e in check_x0_single_execution_path(tmp_path)
    )
