from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oh_goon.core.launch_events import LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchIdentity,
    LaunchIntent,
    LaunchServiceIntent,
)
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.runtime.local_launch_context import LocalLaunchRuntimeContext
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.run_launch_receipt import current_application_launch_receipt

T0 = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _managed_store(tmp_path):
    store = LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")
    intent = LaunchIntent(
        profile="built-local",
        services=(
            LaunchServiceIntent(
                service="backend",
                command=("uvicorn",),
                endpoint=LaunchEndpointIntent(service="backend", host="127.0.0.1", port=8787),
            ),
        ),
    )
    store.create(
        identity=LaunchIdentity(
            launch_id="launch-run",
            workspace="/workspace",
            oh_goon_version="1267.0.51",
            owner_generation=3,
        ),
        intent=intent,
        asserted_by="owner",
        timestamp=T0,
    )
    birth = ProcessBirthIdentity(pid=4242, boot_id="boot-a", start_ticks=99)
    store.append(
        "launch-run",
        event=LaunchEventKind.SERVICE_SPAWNED,
        asserted_by="owner",
        timestamp=T0 + timedelta(seconds=1),
        service="backend",
        service_generation=1,
        payload={"pid": 4242, "birth_identity": birth.to_payload()},
    )
    store.append(
        "launch-run",
        event=LaunchEventKind.SERVICE_BOUND,
        asserted_by="owner",
        timestamp=T0 + timedelta(seconds=2),
        service="backend",
        service_generation=1,
        payload={"host": "127.0.0.1", "port": 8787},
    )
    store.append(
        "launch-run",
        event=LaunchEventKind.SERVICE_STARTUP_COMPLETE,
        asserted_by="backend-runtime",
        timestamp=T0 + timedelta(seconds=3),
        service="backend",
        service_generation=1,
    )
    return store, intent


def test_unmanaged_runtime_is_explicit(monkeypatch):
    for name in (
        "OH_GOON_LAUNCH_ID",
        "OH_GOON_LAUNCH_OWNER_GENERATION",
        "OH_GOON_LAUNCH_BACKEND_GENERATION",
    ):
        monkeypatch.delenv(name, raising=False)
    assert current_application_launch_receipt().kind == "unmanaged"


def test_managed_receipt_captures_resolved_intent_and_observed_backend(monkeypatch, tmp_path):
    store, intent = _managed_store(tmp_path)
    monkeypatch.setattr(LocalLaunchEventStore, "configured", classmethod(lambda cls: store))
    context = LocalLaunchRuntimeContext(
        launch_id="launch-run", owner_generation=3, backend_generation=1
    )
    context.install()
    try:
        receipt = current_application_launch_receipt()
    finally:
        context.clear()
    assert receipt.kind == "managed"
    assert receipt.resolved_intent_identity == intent.identity()
    assert receipt.backend_pid == 4242
    assert receipt.backend_boot_id == "boot-a"
    assert receipt.observed_host == "127.0.0.1"
    assert receipt.observed_port == 8787


def test_managed_receipt_rejects_stale_generation(monkeypatch, tmp_path):
    store, _ = _managed_store(tmp_path)
    monkeypatch.setattr(LocalLaunchEventStore, "configured", classmethod(lambda cls: store))
    context = LocalLaunchRuntimeContext(
        launch_id="launch-run", owner_generation=3, backend_generation=2
    )
    context.install()
    try:
        with pytest.raises(ValueError, match="exact observed backend launch generation"):
            current_application_launch_receipt()
    finally:
        context.clear()
