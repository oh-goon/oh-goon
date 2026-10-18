from __future__ import annotations

from pathlib import Path

from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.runtime.local_launch_binding import LocalLaunchBindingStore
from oh_goon.runtime.local_launch_owner import (
    LocalLaunchOwner,
    built_local_intent,
    contributor_dev_intent,
)
from oh_goon.runtime.local_launch_reuse import (
    LocalLaunchReuseAction,
    assess_local_launch_reuse,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


def _stores(tmp_path: Path) -> tuple[LocalLaunchEventStore, LocalLaunchBindingStore]:
    events = LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")
    return events, LocalLaunchBindingStore.from_event_store(events)


def test_no_binding_authorizes_new_launch_without_discovery(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    decision = assess_local_launch_reuse(
        workspace=tmp_path,
        intent=contributor_dev_intent(),
        binding_store=bindings,
        event_store=events,
    )
    assert decision.action is LocalLaunchReuseAction.START_NEW
    assert decision.code == "no_binding"
    assert decision.launch_id is None


def test_exact_live_owner_and_intent_reuses_current_launch(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    owner = LocalLaunchOwner.create(
        intent=contributor_dev_intent(),
        workspace=tmp_path,
        asserted_by="test-owner",
        store=events,
        binding_store=bindings,
    )
    manifest = events.manifest(owner.identity.launch_id)
    expected = manifest.observed.owner_process
    assert expected is not None and expected.birth_identity is not None
    birth = expected.birth_identity.value

    decision = assess_local_launch_reuse(
        workspace=tmp_path,
        intent=contributor_dev_intent(),
        binding_store=bindings,
        event_store=events,
        process_observer=lambda pid: birth if pid == birth.pid else None,
    )
    assert decision.action is LocalLaunchReuseAction.REUSE
    assert decision.code == "compatible_live_owner"
    assert decision.launch_id == owner.identity.launch_id
    assert decision.status is not None
    assert decision.status.owner_proof.value == "available"


def test_same_pid_with_different_birth_never_authorizes_reuse(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    owner = LocalLaunchOwner.create(
        intent=contributor_dev_intent(),
        workspace=tmp_path,
        asserted_by="test-owner",
        store=events,
        binding_store=bindings,
    )
    manifest = events.manifest(owner.identity.launch_id)
    observed = manifest.observed.owner_process
    assert observed is not None and observed.birth_identity is not None
    expected = observed.birth_identity.value
    replacement = ProcessBirthIdentity(
        pid=expected.pid,
        boot_id=expected.boot_id,
        start_ticks=expected.start_ticks + 1,
    )

    decision = assess_local_launch_reuse(
        workspace=tmp_path,
        intent=contributor_dev_intent(),
        binding_store=bindings,
        event_store=events,
        process_observer=lambda _pid: replacement,
    )
    assert decision.action is LocalLaunchReuseAction.CONFLICT
    assert decision.code == "owner_unproven"


def test_nonterminal_different_intent_is_explicit_conflict(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    owner = LocalLaunchOwner.create(
        intent=contributor_dev_intent(),
        workspace=tmp_path,
        asserted_by="test-owner",
        store=events,
        binding_store=bindings,
    )
    manifest = events.manifest(owner.identity.launch_id)
    observed = manifest.observed.owner_process
    assert observed is not None and observed.birth_identity is not None
    birth = observed.birth_identity.value

    decision = assess_local_launch_reuse(
        workspace=tmp_path,
        intent=built_local_intent(host="127.0.0.1", port=8787),
        binding_store=bindings,
        event_store=events,
        process_observer=lambda _pid: birth,
    )
    assert decision.action is LocalLaunchReuseAction.CONFLICT
    assert decision.code == "intent_mismatch"
    assert "different resolved launch intent" in decision.reason


def test_terminal_previous_launch_authorizes_new_owner(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    owner = LocalLaunchOwner.create(
        intent=contributor_dev_intent(),
        workspace=tmp_path,
        asserted_by="test-owner",
        store=events,
        binding_store=bindings,
    )
    owner.finish_owned_launch()

    decision = assess_local_launch_reuse(
        workspace=tmp_path,
        intent=contributor_dev_intent(),
        binding_store=bindings,
        event_store=events,
    )
    assert decision.action is LocalLaunchReuseAction.START_NEW
    assert decision.code == "previous_terminal"


def test_unproven_nonterminal_owner_blocks_both_reuse_and_replacement(tmp_path: Path) -> None:
    events, bindings = _stores(tmp_path)
    owner = LocalLaunchOwner.create(
        intent=contributor_dev_intent(),
        workspace=tmp_path,
        asserted_by="test-owner",
        store=events,
        binding_store=bindings,
        owner_pid=99_999_999,
    )
    decision = assess_local_launch_reuse(
        workspace=tmp_path,
        intent=contributor_dev_intent(),
        binding_store=bindings,
        event_store=events,
        process_observer=lambda _pid: None,
    )
    assert decision.action is LocalLaunchReuseAction.CONFLICT
    assert decision.code == "owner_unproven"
    assert decision.launch_id == owner.identity.launch_id
