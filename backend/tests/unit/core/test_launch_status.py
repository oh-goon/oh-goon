from __future__ import annotations

from datetime import UTC, datetime, timedelta

from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.launch_events import LaunchEvent, LaunchEventKind
from oh_goon.core.launch_manifest import LaunchIdentity, LaunchIntent, LaunchServiceIntent
from oh_goon.core.launch_projection import project_launch_manifest
from oh_goon.core.launch_status import (
    LaunchObservationFreshness,
    LaunchOwnerProof,
    safe_launch_status,
)

T0 = datetime(2026, 9, 4, 4, 0, tzinfo=UTC)
IDENTITY = LaunchIdentity(
    launch_id="launch-a",
    workspace="/repo",
    oh_goon_version="1267.0.38",
    owner_generation=1,
)
INTENT = LaunchIntent(
    profile="dev",
    services=(LaunchServiceIntent(service="backend", command=("secret-command", "--token=x")),),
)


def _event(sequence: int, event: LaunchEventKind, **kwargs) -> LaunchEvent:
    if event is LaunchEventKind.SERVICE_SPAWNED:
        payload = kwargs.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("pid"), int) and "birth_identity" not in payload:
            pid = payload["pid"]
            kwargs["payload"] = {**payload, "birth_identity": ProcessBirthIdentity(pid=pid, boot_id="test-boot", start_ticks=sequence).to_payload()}
    return LaunchEvent(
        launch_id="launch-a",
        sequence=sequence,
        event=event,
        timestamp=T0 + timedelta(seconds=sequence),
        asserted_by="owner",
        owner_generation=1,
        **kwargs,
    )


def test_nonterminal_status_preserves_lifecycle_but_marks_owner_proof_unavailable() -> None:
    manifest = project_launch_manifest(
        identity=IDENTITY,
        intent=INTENT,
        events=(
            _event(1, LaunchEventKind.LAUNCH_RESOLVED),
            _event(
                2,
                LaunchEventKind.SERVICE_SPAWNED,
                service="backend",
                service_generation=1,
                payload={"pid": 123},
            ),
        ),
    )
    status = safe_launch_status(manifest)
    assert status.lifecycle.value == "starting"
    assert status.observation_freshness is LaunchObservationFreshness.STALE
    assert status.owner_proof is LaunchOwnerProof.UNAVAILABLE


def test_terminal_status_needs_no_live_owner_proof() -> None:
    manifest = project_launch_manifest(
        identity=IDENTITY,
        intent=INTENT,
        events=(
            _event(1, LaunchEventKind.LAUNCH_RESOLVED),
            _event(2, LaunchEventKind.LAUNCH_ABORTED),
        ),
    )
    status = safe_launch_status(manifest)
    assert status.lifecycle.value == "aborted"
    assert status.observation_freshness is LaunchObservationFreshness.CURRENT
    assert status.owner_proof is LaunchOwnerProof.NOT_REQUIRED


def test_safe_status_whitelists_fields_and_never_serializes_command_or_pid() -> None:
    manifest = project_launch_manifest(
        identity=IDENTITY,
        intent=INTENT,
        events=(
            _event(1, LaunchEventKind.LAUNCH_RESOLVED),
            _event(
                2,
                LaunchEventKind.SERVICE_SPAWNED,
                service="backend",
                service_generation=1,
                payload={"pid": 123},
            ),
        ),
    )
    payload = safe_launch_status(manifest).to_json()
    assert "secret-command" not in payload
    assert "--token=x" not in payload
    assert '"pid"' not in payload
    assert "123" not in payload


def test_matching_owner_birth_makes_nonterminal_status_current() -> None:
    birth = ProcessBirthIdentity(pid=123, boot_id="boot-a", start_ticks=77)
    manifest = project_launch_manifest(
        identity=IDENTITY,
        intent=INTENT,
        events=(
            _event(1, LaunchEventKind.LAUNCH_RESOLVED),
            _event(
                2,
                LaunchEventKind.LAUNCH_OWNER_OBSERVED,
                payload={"pid": 123, "birth_identity": birth.to_payload()},
            ),
        ),
    )
    status = safe_launch_status(manifest, process_observer=lambda pid: birth if pid == 123 else None)
    assert status.lifecycle.value == "resolving"
    assert status.observation_freshness is LaunchObservationFreshness.CURRENT
    assert status.owner_proof is LaunchOwnerProof.AVAILABLE


def test_pid_reuse_or_missing_owner_never_changes_authoritative_lifecycle() -> None:
    expected = ProcessBirthIdentity(pid=123, boot_id="boot-a", start_ticks=77)
    replacement = ProcessBirthIdentity(pid=123, boot_id="boot-a", start_ticks=999)
    manifest = project_launch_manifest(
        identity=IDENTITY,
        intent=INTENT,
        events=(
            _event(1, LaunchEventKind.LAUNCH_RESOLVED),
            _event(
                2,
                LaunchEventKind.LAUNCH_OWNER_OBSERVED,
                payload={"pid": 123, "birth_identity": expected.to_payload()},
            ),
            _event(
                3,
                LaunchEventKind.SERVICE_SPAWNED,
                service="backend",
                service_generation=1,
                payload={"pid": 456},
            ),
        ),
    )
    status = safe_launch_status(manifest, process_observer=lambda _pid: replacement)
    assert status.lifecycle.value == "starting"
    assert status.observation_freshness is LaunchObservationFreshness.STALE
    assert status.owner_proof is LaunchOwnerProof.UNAVAILABLE
