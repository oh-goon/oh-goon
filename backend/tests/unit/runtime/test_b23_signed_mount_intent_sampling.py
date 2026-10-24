"""B23: signed intent binds declared sandbox targets; still no physical proof."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.ports.original_mount_intent import (
    OriginalHostToolMount,
    OriginalMountIntentDescriptor,
    OriginalSandboxMount,
)
from oh_goon.integrations import linux_physical_observer
from oh_goon.runtime.enforcer_lace_journal import CausalJournalError
from tests.unit.core.test_enforcer_lace_trust import authenticator
from tests.unit.runtime.test_b22_original_physical_sampling import mount_descriptor, seeded


def test_signed_descriptor_matches_original_b12c_mount_hash():
    from oh_goon.core.enforcer_lace_trust import canonical_bytes, digest

    intent = mount_descriptor()
    assert intent.resource_identity == digest(
        "oh-goon/b12c/mounts/v1",
        canonical_bytes(
            {
                "sandbox_bindings": [
                    {"source": "/source/snapshot", "target": "/original/mount", "sha256": "a" * 64}
                ],
                "host_tools": [],
            }
        ),
    )
    assert intent.binding_targets == ("/original/mount",)


@pytest.mark.parametrize(
    "attack",
    [
        "foreign-source",
        "foreign-target",
        "foreign-digest",
        "host-tool",
        "empty",
        "reordered",
        "duplicate",
        "mutable-descriptor",
        "unknown-target",
    ],
)
def test_original_owner_rejects_forged_declared_mount_before_kernel(
    tmp_path: Path, monkeypatch, attack
):
    conn, journal, _session, owner, scope = seeded(tmp_path)
    original = journal.verify("run-one", auth=authenticator())
    descriptor = mount_descriptor()
    if attack == "foreign-source":
        descriptor = OriginalMountIntentDescriptor(
            (replace(descriptor.sandbox_bindings[0], source="/other"),), ()
        )
    elif attack == "foreign-target":
        descriptor = OriginalMountIntentDescriptor(
            (replace(descriptor.sandbox_bindings[0], target="/other"),), ()
        )
    elif attack == "foreign-digest":
        descriptor = OriginalMountIntentDescriptor(
            (replace(descriptor.sandbox_bindings[0], sha256="b" * 64),), ()
        )
    elif attack == "host-tool":
        descriptor = OriginalMountIntentDescriptor(
            descriptor.sandbox_bindings, (OriginalHostToolMount("tool", "/tool/bin", "c" * 64),)
        )
    elif attack == "empty":
        descriptor = OriginalMountIntentDescriptor((), ())
    elif attack == "reordered":
        descriptor = OriginalMountIntentDescriptor(
            descriptor.sandbox_bindings + (OriginalSandboxMount("/more", "/more", "d" * 64),), ()
        )
        scope = replace(scope, mount_targets=("/more", "/original/mount"))
    elif attack == "duplicate":
        with pytest.raises(ValueError, match="ambiguous"):
            OriginalMountIntentDescriptor(
                (descriptor.sandbox_bindings[0], descriptor.sandbox_bindings[0]), ()
            )
        conn.close()
        return
    elif attack == "mutable-descriptor":
        descriptor = {"sandbox_bindings": (), "host_tools": ()}
    elif attack == "unknown-target":
        scope = replace(scope, mount_targets=("/original/mount", "/extra-uncommitted"))
    calls = []
    monkeypatch.setattr(
        linux_physical_observer, "observe_kernel", lambda *_a, **_kw: calls.append(True)
    )
    with pytest.raises((CausalJournalError, ValueError)):
        owner.sample_recovery_physical_unqualified(
            intent_run_seq=2, scope=scope, mount_intent=descriptor, proc_root=tmp_path
        )
    assert calls == []
    assert journal.verify("run-one", auth=authenticator()) == original
    conn.close()


@pytest.mark.parametrize(
    "bad",
    [
        ("relative", "/target", "a" * 64),
        ("/src/../escape", "/target", "a" * 64),
        ("/src", "/bad/./target", "a" * 64),
        ("/src", "/target", "A" * 64),
        ("/src", "/target", "sha256:" + "a" * 64),
        ("/src", "/target", "bad"),
        ("/src\x00evil", "/target", "a" * 64),
    ],
)
def test_noncanonical_source_target_or_digest_blocked(bad):
    with pytest.raises(ValueError, match="noncanonical"):
        OriginalSandboxMount(*bad)


def test_matched_mount_plan_is_still_unqualified(tmp_path, monkeypatch):
    from oh_goon.core.ports.physical_observation import PhysicalKernelFact, PhysicalKernelSnapshot

    conn, journal, _session, owner, scope = seeded(tmp_path)
    signed = journal.verify("run-one", auth=authenticator())
    calls = []

    def observe(kernel_scope, *, proc_root):
        calls.append(kernel_scope)
        return PhysicalKernelSnapshot(
            (("mounts", PhysicalKernelFact("NOT_OBSERVED", "scoped only")),)
        )

    monkeypatch.setattr(linux_physical_observer, "observe_kernel", observe)
    sample = owner.sample_recovery_physical_unqualified(
        intent_run_seq=2, scope=scope, mount_intent=mount_descriptor(), proc_root=tmp_path
    )
    assert calls == [scope]
    assert sample.mount_intent_identity == mount_descriptor().resource_identity
    assert sample.effect_permission == "NONE" and not sample.physical_absence_proven
    assert not sample.quiescence_proven and not sample.teardown_proven
    events = journal.verify("run-one", auth=authenticator())
    assert events[:-1] == signed and events[-1]["kind"] == "RESOURCE_PHYSICAL_SAMPLE_RECORDED"
    with pytest.raises(ValueError, match="cannot mint physical authority"):
        replace(sample, teardown_proven=True)
    conn.close()
