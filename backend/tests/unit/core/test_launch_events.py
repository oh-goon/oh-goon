from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.launch_events import (
    LAUNCH_EVENT_SCHEMA,
    LaunchEvent,
    LaunchEventKind,
    project_launch_lifecycle,
    validate_launch_event_stream,
)
from oh_goon.core.launch_manifest import LaunchLifecycleState

T0 = datetime(2026, 9, 3, 20, 0, tzinfo=UTC)


def _event(sequence: int, kind: LaunchEventKind, **kwargs) -> LaunchEvent:
    if kind is LaunchEventKind.SERVICE_SPAWNED:
        payload = kwargs.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("pid"), int) and "birth_identity" not in payload:
            pid = payload["pid"]
            kwargs["payload"] = {**payload, "birth_identity": ProcessBirthIdentity(pid=pid, boot_id="test-boot", start_ticks=sequence).to_payload()}
    return LaunchEvent(
        launch_id="launch-a",
        sequence=sequence,
        event=kind,
        timestamp=kwargs.pop("timestamp", T0 + timedelta(seconds=sequence)),
        asserted_by="launch-owner",
        owner_generation=4,
        **kwargs,
    )


def test_event_schema_and_json_are_explicit_and_deterministic() -> None:
    event = _event(1, LaunchEventKind.LAUNCH_RESOLVED, payload={"profile": "dev"})
    assert event.to_json() == event.to_json()
    payload = json.loads(event.to_json())
    assert payload["schema"] == LAUNCH_EVENT_SCHEMA
    assert payload["event"] == "launch.resolved"
    assert payload["sequence"] == 1


def test_service_events_require_generation_bound_service_identity() -> None:
    with pytest.raises(ValueError, match="service Launch Event"):
        _event(2, LaunchEventKind.SERVICE_READY)
    event = _event(
        2,
        LaunchEventKind.SERVICE_READY,
        service="backend",
        service_generation=3,
    )
    assert event.service_generation == 3


def test_sequence_is_ordering_authority_not_wall_clock() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED, timestamp=T0 + timedelta(seconds=10)),
        _event(
            2,
            LaunchEventKind.SERVICE_SPAWNED,
            timestamp=T0,
            service="backend",
            service_generation=1,
            payload={"pid": 100},
        ),
    )
    assert validate_launch_event_stream(events) == events
    assert project_launch_lifecycle(events).state is LaunchLifecycleState.STARTING


def test_stream_rejects_mixed_launch_identity_or_owner_generation() -> None:
    first = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    wrong_launch = LaunchEvent(
        launch_id="launch-b",
        sequence=2,
        event=LaunchEventKind.LAUNCH_READY,
        timestamp=T0,
        asserted_by="launch-owner",
        owner_generation=4,
    )
    with pytest.raises(ValueError, match="launch identities"):
        validate_launch_event_stream((first, wrong_launch))
    wrong_owner = LaunchEvent(
        launch_id="launch-a",
        sequence=2,
        event=LaunchEventKind.LAUNCH_READY,
        timestamp=T0,
        asserted_by="other-owner",
        owner_generation=5,
    )
    with pytest.raises(ValueError, match="owner generations"):
        validate_launch_event_stream((first, wrong_owner))


def test_terminal_state_requires_positive_owner_event_and_cannot_be_followed() -> None:
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    spawned = _event(
        2,
        LaunchEventKind.SERVICE_SPAWNED,
        service="backend",
        service_generation=1,
        payload={"pid": 100},
    )
    assert project_launch_lifecycle((resolved, spawned)).state is LaunchLifecycleState.STARTING
    failed = _event(
        3,
        LaunchEventKind.LAUNCH_FAILED,
        payload={
            "failure_kind": "process",
            "failure_code": "child_failed",
            "reason": "backend exited",
            "retryable": False,
        },
    )
    lifecycle = project_launch_lifecycle((resolved, spawned, failed))
    assert lifecycle.state is LaunchLifecycleState.FAILED
    assert lifecycle.terminal_at == failed.timestamp
    with pytest.raises(ValueError, match="after terminal"):
        validate_launch_event_stream(
            (resolved, spawned, failed, _event(4, LaunchEventKind.LAUNCH_STOPPED))
        )


def test_arbitrary_log_text_is_not_a_launch_event_kind() -> None:
    with pytest.raises(ValueError):
        LaunchEventKind("log.ready")


def test_launch_ready_projects_ready_without_inventing_health() -> None:
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    ready = _event(2, LaunchEventKind.LAUNCH_READY)
    lifecycle = project_launch_lifecycle((resolved, ready))
    assert lifecycle.state is LaunchLifecycleState.READY
    assert lifecycle.ready_at == ready.timestamp


def test_event_payload_is_immutable_after_construction() -> None:
    source = {"pid": 42}
    event = _event(1, LaunchEventKind.LAUNCH_RESOLVED, payload=source)
    source["pid"] = 99
    assert event.payload["pid"] == 42
    with pytest.raises(TypeError):
        event.payload["pid"] = 7  # type: ignore[index]


def test_service_generation_rejects_late_events_from_replaced_process() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(2, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1, payload={"pid": 10}),
        _event(3, LaunchEventKind.SERVICE_STARTUP_COMPLETE, service="backend", service_generation=1),
        _event(4, LaunchEventKind.SERVICE_READY, service="backend", service_generation=1),
        _event(5, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=2, payload={"pid": 20}),
        _event(6, LaunchEventKind.SERVICE_READY, service="backend", service_generation=1),
    )
    with pytest.raises(ValueError, match="stale service generation"):
        validate_launch_event_stream(events)


def test_spawn_and_bound_payloads_are_structured_facts() -> None:
    with pytest.raises(ValueError, match="payload pid"):
        _event(2, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1)
    with pytest.raises(ValueError, match="payload port"):
        _event(2, LaunchEventKind.SERVICE_BOUND, service="backend", service_generation=1, payload={"host": "127.0.0.1", "port": 0})


def test_owner_observation_requires_birth_identity_and_pid_match() -> None:
    with pytest.raises(ValueError, match="birth_identity"):
        _event(2, LaunchEventKind.LAUNCH_OWNER_OBSERVED, payload={"pid": 42})
    birth = ProcessBirthIdentity(pid=43, boot_id="boot", start_ticks=9)
    with pytest.raises(ValueError, match="birth identity pid"):
        _event(
            2,
            LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            payload={"pid": 42, "birth_identity": birth.to_payload()},
        )


def test_service_spawn_requires_birth_identity() -> None:
    with pytest.raises(ValueError, match="birth_identity"):
        LaunchEvent(
            launch_id="launch-a",
            sequence=2,
            event=LaunchEventKind.SERVICE_SPAWNED,
            timestamp=T0 + timedelta(seconds=2),
            asserted_by="launch-owner",
            owner_generation=4,
            service="backend",
            service_generation=1,
            payload={"pid": 42},
        )

def test_owner_observation_is_single_and_must_precede_service_processes() -> None:
    birth = ProcessBirthIdentity(pid=30, boot_id="boot", start_ticks=1).to_payload()
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    owner = _event(2, LaunchEventKind.LAUNCH_OWNER_OBSERVED, payload={"pid": 30, "birth_identity": birth})
    duplicate = _event(3, LaunchEventKind.LAUNCH_OWNER_OBSERVED, payload={"pid": 30, "birth_identity": birth})
    with pytest.raises(ValueError, match="only be observed once"):
        validate_launch_event_stream((resolved, owner, duplicate))

    spawned = _event(
        2,
        LaunchEventKind.SERVICE_SPAWNED,
        service="backend",
        service_generation=1,
        payload={"pid": 41},
    )
    late_owner = _event(3, LaunchEventKind.LAUNCH_OWNER_OBSERVED, payload={"pid": 30, "birth_identity": birth})
    with pytest.raises(ValueError, match="before service processes"):
        validate_launch_event_stream((resolved, spawned, late_owner))


def test_service_generation_state_machine_rejects_duplicate_and_post_failure_events() -> None:
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    spawned = _event(2, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1, payload={"pid": 10})
    bound = _event(3, LaunchEventKind.SERVICE_BOUND, service="backend", service_generation=1, payload={"host": "127.0.0.1", "port": 8787})
    duplicate_bound = _event(4, LaunchEventKind.SERVICE_BOUND, service="backend", service_generation=1, payload={"host": "127.0.0.1", "port": 8787})
    with pytest.raises(ValueError, match="only be asserted once"):
        validate_launch_event_stream((resolved, spawned, bound, duplicate_bound))

    failed = _event(3, LaunchEventKind.SERVICE_FAILED, service="backend", service_generation=1, payload={"failure_kind": "process", "failure_code": "exit", "reason": "gone", "retryable": True})
    late_ready = _event(4, LaunchEventKind.SERVICE_READY, service="backend", service_generation=1)
    with pytest.raises(ValueError, match="after service.failed"):
        validate_launch_event_stream((resolved, spawned, failed, late_ready))


def test_service_ready_requires_startup_complete_and_freezes_runtime_facts() -> None:
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    spawned = _event(2, LaunchEventKind.SERVICE_SPAWNED, service="frontend", service_generation=1, payload={"pid": 20})
    ready = _event(3, LaunchEventKind.SERVICE_READY, service="frontend", service_generation=1)
    with pytest.raises(ValueError, match="startup_complete"):
        validate_launch_event_stream((resolved, spawned, ready))

    startup = _event(3, LaunchEventKind.SERVICE_STARTUP_COMPLETE, service="frontend", service_generation=1)
    ready = _event(4, LaunchEventKind.SERVICE_READY, service="frontend", service_generation=1)
    late_bound = _event(5, LaunchEventKind.SERVICE_BOUND, service="frontend", service_generation=1, payload={"host": "127.0.0.1", "port": 5173})
    with pytest.raises(ValueError, match="after service.ready"):
        validate_launch_event_stream((resolved, spawned, startup, ready, late_bound))


def test_failed_generation_may_restart_only_at_next_exact_generation() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(2, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1, payload={"pid": 10}),
        _event(3, LaunchEventKind.SERVICE_FAILED, service="backend", service_generation=1, payload={"failure_kind": "process", "failure_code": "exit", "reason": "gone", "retryable": True}),
        _event(4, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=2, payload={"pid": 11}),
    )
    assert validate_launch_event_stream(events) == events
    live_replace = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(2, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1, payload={"pid": 10}),
        _event(3, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=2, payload={"pid": 11}),
    )
    with pytest.raises(ValueError, match="still starting"):
        validate_launch_event_stream(live_replace)


def test_launch_terminal_state_machine_distinguishes_stop_abort_and_failure() -> None:
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    with pytest.raises(ValueError, match="requires prior launch.ready"):
        validate_launch_event_stream((resolved, _event(2, LaunchEventKind.LAUNCH_STOPPING)))
    with pytest.raises(ValueError, match="requires prior launch.stopping"):
        validate_launch_event_stream((resolved, _event(2, LaunchEventKind.LAUNCH_STOPPED)))

    ready = _event(2, LaunchEventKind.LAUNCH_READY)
    with pytest.raises(ValueError, match="only valid before launch.ready"):
        validate_launch_event_stream((resolved, ready, _event(3, LaunchEventKind.LAUNCH_ABORTED)))

    stopping = _event(3, LaunchEventKind.LAUNCH_STOPPING)
    with pytest.raises(ValueError, match="only transition to launch.stopped"):
        validate_launch_event_stream((resolved, ready, stopping, _event(4, LaunchEventKind.LAUNCH_FAILED, payload={"failure_kind": "process", "failure_code": "x", "reason": "x", "retryable": False})))
    stopped = _event(4, LaunchEventKind.LAUNCH_STOPPED)
    assert validate_launch_event_stream((resolved, ready, stopping, stopped))


def test_launch_ready_rejects_failed_current_generation_and_new_spawn_after_ready() -> None:
    resolved = _event(1, LaunchEventKind.LAUNCH_RESOLVED)
    spawned = _event(2, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1, payload={"pid": 10})
    failed = _event(3, LaunchEventKind.SERVICE_FAILED, service="backend", service_generation=1, payload={"failure_kind": "process", "failure_code": "exit", "reason": "gone", "retryable": True})
    with pytest.raises(ValueError, match="failed current"):
        validate_launch_event_stream((resolved, spawned, failed, _event(4, LaunchEventKind.LAUNCH_READY)))

    launch_ready = _event(2, LaunchEventKind.LAUNCH_READY)
    new_spawn = _event(3, LaunchEventKind.SERVICE_SPAWNED, service="backend", service_generation=1, payload={"pid": 10})
    with pytest.raises(ValueError, match="after launch.ready"):
        validate_launch_event_stream((resolved, launch_ready, new_spawn))
