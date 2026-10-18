from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from oh_goon.core.launch_events import LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchFactState,
    LaunchIdentity,
    LaunchIntent,
    LaunchServiceIntent,
)
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.runtime.local_launch_state import LocalLaunchEventStore

T0 = datetime(2026, 9, 3, 21, 0, tzinfo=UTC)


def _birth(pid: int) -> dict[str, object]:
    return ProcessBirthIdentity(pid=pid, boot_id="boot-test", start_ticks=1).to_payload()


def _store(tmp_path: Path) -> LocalLaunchEventStore:
    return LocalLaunchEventStore.from_database(tmp_path / "state" / "oh-goon.db")


def _identity() -> LaunchIdentity:
    return LaunchIdentity(
        launch_id="launch-test",
        workspace="/workspace/oh-goon",
        oh_goon_version="1267.0.38",
        owner_generation=1,
    )


def _intent() -> LaunchIntent:
    return LaunchIntent(
        profile="dev",
        services=(
            LaunchServiceIntent(
                service="backend",
                command=("uvicorn", "oh_goon.api.app:app"),
                endpoint=LaunchEndpointIntent(service="backend", host="127.0.0.1", port=8787),
            ),
        ),
    )


def test_store_persists_events_and_projects_manifest_without_secret_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    manifest = store.create(identity=_identity(), intent=_intent(), asserted_by="dev-launch-owner", timestamp=T0)
    assert manifest.lifecycle.state.value == "resolving"

    manifest = store.append(
        "launch-test",
        event=LaunchEventKind.SERVICE_SPAWNED,
        asserted_by="dev-launch-owner",
        timestamp=T0 + timedelta(seconds=1),
        service="backend",
        service_generation=1,
        payload={"pid": 101, "birth_identity": _birth(101)},
    )
    backend = manifest.observed.services[0]
    assert backend.process is not None and backend.process.pid.value == 101
    assert backend.state("readiness") is LaunchFactState.UNKNOWN
    assert store.manifest("launch-test").to_json() == manifest.to_json()

    raw = store.path("launch-test").read_text(encoding="utf-8")
    assert "OH_GOON_LOCAL_ACCESS_TOKEN" not in raw
    assert "#access=" not in raw


def test_store_allocates_sequence_under_one_atomic_writer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(identity=_identity(), intent=_intent(), asserted_by="owner", timestamp=T0)
    store.append(
        "launch-test",
        event=LaunchEventKind.SERVICE_SPAWNED,
        asserted_by="owner",
        timestamp=T0 + timedelta(seconds=1),
        service="backend",
        service_generation=1,
        payload={"pid": 101, "birth_identity": _birth(101)},
    )
    store.append(
        "launch-test",
        event=LaunchEventKind.SERVICE_STARTUP_COMPLETE,
        asserted_by="backend-runtime",
        timestamp=T0 + timedelta(seconds=2),
        service="backend",
        service_generation=1,
    )
    assert [event.sequence for event in store.events("launch-test")] == [1, 2, 3]


def test_invalid_append_is_rejected_before_record_replacement(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(identity=_identity(), intent=_intent(), asserted_by="owner", timestamp=T0)
    before = store.path("launch-test").read_bytes()
    with pytest.raises(ValueError, match="prior service.spawned"):
        store.append(
            "launch-test",
            event=LaunchEventKind.SERVICE_READY,
            asserted_by="backend-runtime",
            timestamp=T0 + timedelta(seconds=1),
            service="backend",
            service_generation=1,
        )
    assert store.path("launch-test").read_bytes() == before


def test_record_is_deterministic_json_and_ignores_unknown_top_level_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(identity=_identity(), intent=_intent(), asserted_by="owner", timestamp=T0)
    path = store.path("launch-test")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["future_field"] = {"compatible": True}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.manifest("launch-test").identity.launch_id == "launch-test"
