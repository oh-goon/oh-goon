from __future__ import annotations

from pathlib import Path

from oh_goon.core.launch_manifest import LaunchFactState, LaunchLifecycleState
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.runtime.local_launch_owner import (
    BACKEND_GENERATION,
    BACKEND_SERVICE,
    LocalLaunchOwner,
    built_local_intent,
)
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore


def _store(tmp_path: Path) -> LocalLaunchEventStore:
    return LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")


def test_owner_records_positive_runtime_facts_without_absorbing_native_runtime_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "oh_goon.runtime.local_launch_owner.observe_process_birth",
        lambda pid: ProcessBirthIdentity(pid=pid, boot_id="boot-test", start_ticks=1),
    )
    store = _store(tmp_path)
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path,
        asserted_by="cli-start",
        store=store,
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, 1234)
    backend_runtime = LocalLaunchOwner.load(
        owner.identity.launch_id,
        asserted_by="backend-runtime",
        store=store,
    )
    backend_runtime.startup_complete(BACKEND_SERVICE, BACKEND_GENERATION)
    owner.bound(BACKEND_SERVICE, BACKEND_GENERATION, host="127.0.0.1", port=8787)
    owner.ready(BACKEND_SERVICE, BACKEND_GENERATION)
    manifest = owner.launch_ready()

    backend = manifest.observed.services[0]
    assert backend.process is not None and backend.process.pid.value == 1234
    assert backend.process.pid.asserted_by == "cli-start"
    assert backend.startup is not None and backend.startup.asserted_by == "backend-runtime"
    assert backend.state("readiness") is LaunchFactState.SATISFIED
    assert manifest.lifecycle.state is LaunchLifecycleState.READY

    stopped = owner.finish_owned_launch()
    assert stopped.lifecycle.state is LaunchLifecycleState.STOPPED


def test_finish_before_ready_is_aborted_not_fake_stopped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "oh_goon.runtime.local_launch_owner.observe_process_birth",
        lambda pid: ProcessBirthIdentity(pid=pid, boot_id="boot-test", start_ticks=1),
    )
    owner = LocalLaunchOwner.create(
        intent=built_local_intent(host="127.0.0.1", port=8787),
        workspace=tmp_path,
        asserted_by="cli-start",
        store=_store(tmp_path),
    )
    owner.spawned(BACKEND_SERVICE, BACKEND_GENERATION, 99)
    manifest = owner.finish_owned_launch()
    assert manifest.lifecycle.state is LaunchLifecycleState.ABORTED
