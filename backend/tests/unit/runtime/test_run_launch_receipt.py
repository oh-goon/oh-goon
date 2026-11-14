from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oh_goon.core.launch_events import LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchIdentity,
    LaunchIntent,
    LaunchServiceIntent,
)
from oh_goon.core.process_birth import observe_process_birth
from oh_goon.runtime.local_launch_context import LocalLaunchRuntimeContext
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore
from oh_goon.runtime.run_launch_receipt import current_application_launch_receipt

T0 = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _managed_store(tmp_path: Path) -> tuple[LocalLaunchEventStore, LaunchIntent]:
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
    birth = observe_process_birth(os.getpid())
    assert birth is not None
    store.append(
        "launch-run",
        event=LaunchEventKind.SERVICE_SPAWNED,
        asserted_by="owner",
        timestamp=T0 + timedelta(seconds=1),
        service="backend",
        service_generation=1,
        payload={"pid": birth.pid, "birth_identity": birth.to_payload()},
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


def test_unmanaged_runtime_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "OH_GOON_LAUNCH_ID",
        "OH_GOON_LAUNCH_OWNER_GENERATION",
        "OH_GOON_LAUNCH_BACKEND_GENERATION",
    ):
        monkeypatch.delenv(name, raising=False)
    assert current_application_launch_receipt().kind == "unmanaged"


def test_managed_receipt_captures_resolved_intent_and_observed_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert receipt.backend_pid == os.getpid()
    birth = observe_process_birth(os.getpid())
    assert birth is not None and receipt.backend_boot_id == birth.boot_id
    assert receipt.observed_host == "127.0.0.1"
    assert receipt.observed_port == 8787


def test_managed_receipt_rejects_stale_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_inherited_launch_context_cannot_issue_a_managed_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, _ = _managed_store(tmp_path)
    monkeypatch.setenv("OH_GOON_DB", str(store.root.parent / "oh-goon.db"))
    # Select the same store through its native database-owned path derivation.
    monkeypatch.setattr(LocalLaunchEventStore, "configured", classmethod(lambda cls: store))
    context = LocalLaunchRuntimeContext(
        launch_id="launch-run", owner_generation=3, backend_generation=1
    )
    context.install()
    try:
        # A real child receives the exact context and complete real launch record.
        # Importing the owner in a new process must not label that child as its parent.
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from oh_goon.runtime.run_launch_receipt import current_application_launch_receipt; "
                "current_application_launch_receipt()",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode != 0
        assert "requires its own managed backend process identity" in result.stderr
        assert current_application_launch_receipt().kind == "managed"
    finally:
        context.clear()
