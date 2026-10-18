from __future__ import annotations

from datetime import UTC, datetime

from oh_goon.core.launch_activity import safe_launch_log
from oh_goon.core.launch_events import LaunchEvent, LaunchEventKind


def test_safe_launch_log_omits_process_birth_payload_but_keeps_safe_failure_detail() -> None:
    events = (
        LaunchEvent(
            launch_id="launch-1",
            sequence=1,
            event=LaunchEventKind.LAUNCH_RESOLVED,
            timestamp=datetime.now(UTC),
            asserted_by="owner",
            owner_generation=1,
        ),
        LaunchEvent(
            launch_id="launch-1",
            sequence=2,
            event=LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            timestamp=datetime.now(UTC),
            asserted_by="owner",
            owner_generation=1,
            payload={
                "pid": 123,
                "birth_identity": {
                    "provider": "linux-procfs-v1",
                    "pid": 123,
                    "boot_id": "boot-secret-ish",
                    "start_ticks": 456,
                },
            },
        ),
        LaunchEvent(
            launch_id="launch-1",
            sequence=3,
            event=LaunchEventKind.SERVICE_SPAWNED,
            timestamp=datetime.now(UTC),
            asserted_by="owner",
            owner_generation=1,
            service="backend",
            service_generation=1,
            payload={
                "pid": 321,
                "birth_identity": {
                    "provider": "linux-procfs-v1",
                    "pid": 321,
                    "boot_id": "boot-secret-ish",
                    "start_ticks": 654,
                },
            },
        ),
        LaunchEvent(
            launch_id="launch-1",
            sequence=4,
            event=LaunchEventKind.SERVICE_FAILED,
            timestamp=datetime.now(UTC),
            asserted_by="owner",
            owner_generation=1,
            service="backend",
            service_generation=1,
            payload={
                "failure_kind": "process",
                "failure_code": "child_exit",
                "reason": "backend exited",
                "retryable": True,
            },
        ),
        LaunchEvent(
            launch_id="launch-1",
            sequence=5,
            event=LaunchEventKind.LAUNCH_FAILED,
            timestamp=datetime.now(UTC),
            asserted_by="owner",
            owner_generation=1,
            payload={
                "failure_kind": "process",
                "failure_code": "required_service_exit",
                "reason": "required service exited",
                "retryable": True,
            },
        ),
    )

    log = safe_launch_log(events)
    payload = log.to_json()
    assert log.schema == "oh-goon.launch-log/v1"
    assert "child_exit" in payload
    assert "backend exited" in payload
    assert "boot-secret-ish" not in payload
    assert '"pid"' not in payload
    assert "start_ticks" not in payload
