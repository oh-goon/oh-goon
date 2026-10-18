from __future__ import annotations

from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime, timedelta

import pytest

from oh_goon.core.launch_events import LaunchEvent, LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchFactState,
    LaunchIdentity,
    LaunchIntent,
    LaunchLifecycleState,
    LaunchServiceIntent,
)
from oh_goon.core.launch_projection import project_launch_manifest
from oh_goon.core.process_birth import ProcessBirthIdentity

T0 = datetime(2026, 9, 3, 20, 30, tzinfo=UTC)
IDENTITY = LaunchIdentity(
    launch_id="launch-1",
    workspace="/workspace/oh-goon",
    oh_goon_version="1267.0.38",
    owner_generation=3,
)
INTENT = LaunchIntent(
    profile="dev",
    services=(
        LaunchServiceIntent(
            service="backend",
            command=("uvicorn", "oh_goon.api.app:app"),
            endpoint=LaunchEndpointIntent(service="backend", host="127.0.0.1", port=8787),
        ),
        LaunchServiceIntent(
            service="frontend",
            command=("npm", "run", "dev"),
            endpoint=LaunchEndpointIntent(service="frontend", host="127.0.0.1", port=5173),
            required=False,
        ),
    ),
)


def _event(sequence: int, event: LaunchEventKind, **kwargs) -> LaunchEvent:
    if event is LaunchEventKind.SERVICE_SPAWNED:
        payload = kwargs.get("payload")
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("pid"), int)
            and "birth_identity" not in payload
        ):
            pid = payload["pid"]
            kwargs["payload"] = {
                **payload,
                "birth_identity": ProcessBirthIdentity(
                    pid=pid, boot_id="test-boot", start_ticks=sequence
                ).to_payload(),
            }
    return LaunchEvent(
        launch_id=IDENTITY.launch_id,
        sequence=sequence,
        event=event,
        timestamp=T0 + timedelta(seconds=sequence),
        asserted_by=kwargs.pop("asserted_by", "launch-owner"),
        owner_generation=IDENTITY.owner_generation,
        **kwargs,
    )


def _backend_ready_events() -> tuple[LaunchEvent, ...]:
    return (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.SERVICE_SPAWNED,
            service="backend",
            service_generation=1,
            payload={"pid": 41},
        ),
        _event(
            3,
            LaunchEventKind.SERVICE_BOUND,
            service="backend",
            service_generation=1,
            asserted_by="backend-runtime",
            payload={"host": "127.0.0.1", "port": 8787},
        ),
        _event(
            4,
            LaunchEventKind.SERVICE_STARTUP_COMPLETE,
            service="backend",
            service_generation=1,
            asserted_by="backend-runtime",
        ),
        _event(
            5,
            LaunchEventKind.SERVICE_READY,
            service="backend",
            service_generation=1,
            asserted_by="backend-runtime",
        ),
    )


def test_projection_preserves_provenance_for_every_positive_service_fact() -> None:
    manifest = project_launch_manifest(
        identity=IDENTITY, intent=INTENT, events=_backend_ready_events()
    )
    backend = manifest.observed.services[0]
    assert backend.process is not None and backend.process.pid.asserted_by == "launch-owner"
    assert (
        backend.bound_endpoint is not None
        and backend.bound_endpoint.host.asserted_by == "backend-runtime"
    )
    assert backend.startup is not None and backend.startup.asserted_by == "backend-runtime"
    assert backend.readiness is not None and backend.readiness.asserted_by == "backend-runtime"
    assert backend.health is None
    assert backend.state("health") is LaunchFactState.UNKNOWN


def test_launch_ready_requires_all_required_services_ready() -> None:
    events = (*_backend_ready_events(), _event(6, LaunchEventKind.LAUNCH_READY))
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    assert manifest.lifecycle.state is LaunchLifecycleState.READY
    assert manifest.lifecycle.ready_at == events[-1].timestamp

    incomplete = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.SERVICE_SPAWNED,
            service="backend",
            service_generation=1,
            payload={"pid": 41},
        ),
        _event(3, LaunchEventKind.LAUNCH_READY),
    )
    with pytest.raises(ValueError, match="required service"):
        project_launch_manifest(identity=IDENTITY, intent=INTENT, events=incomplete)


def test_ready_cannot_be_inferred_from_spawn_or_bound() -> None:
    events = _backend_ready_events()[:3]
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    backend = manifest.observed.services[0]
    assert backend.state("startup") is LaunchFactState.UNKNOWN
    assert backend.state("readiness") is LaunchFactState.UNKNOWN
    assert manifest.lifecycle.state is LaunchLifecycleState.STARTING


def test_service_failure_is_structured_and_does_not_invent_terminal_launch_state() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.SERVICE_SPAWNED,
            service="backend",
            service_generation=1,
            payload={"pid": 41},
        ),
        _event(
            3,
            LaunchEventKind.SERVICE_FAILED,
            service="backend",
            service_generation=1,
            asserted_by="backend-runtime",
            payload={
                "failure_kind": "process",
                "failure_code": "child_exit",
                "reason": "backend exited",
                "retryable": False,
            },
        ),
    )
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    backend = manifest.observed.services[0]
    assert backend.state("startup") is LaunchFactState.UNSATISFIED
    assert backend.state("readiness") is LaunchFactState.UNSATISFIED
    assert manifest.lifecycle.state is LaunchLifecycleState.DEGRADED
    assert manifest.lifecycle.terminal_at is None
    assert manifest.failure is not None and manifest.failure.code == "child_exit"


def test_new_service_generation_resets_old_runtime_observations() -> None:
    events = (
        *_backend_ready_events(),
        _event(
            6,
            LaunchEventKind.SERVICE_SPAWNED,
            service="backend",
            service_generation=2,
            payload={"pid": 99},
        ),
    )
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    backend = manifest.observed.services[0]
    assert backend.service_generation == 2
    assert backend.process is not None and backend.process.pid.value == 99
    assert backend.bound_endpoint is None
    assert backend.state("startup") is LaunchFactState.UNKNOWN
    assert backend.state("readiness") is LaunchFactState.UNKNOWN


def test_projection_keeps_owner_and_service_birth_identity_separate() -> None:
    owner_birth = ProcessBirthIdentity(pid=30, boot_id="boot-a", start_ticks=100)
    service_birth = ProcessBirthIdentity(pid=41, boot_id="boot-a", start_ticks=110)
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            payload={"pid": 30, "birth_identity": owner_birth.to_payload()},
        ),
        _event(
            3,
            LaunchEventKind.SERVICE_SPAWNED,
            service="backend",
            service_generation=1,
            payload={"pid": 41, "birth_identity": service_birth.to_payload()},
        ),
    )
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    assert manifest.observed.owner_process is not None
    assert manifest.observed.owner_process.birth_identity is not None
    assert manifest.observed.owner_process.birth_identity.value == owner_birth
    backend = manifest.observed.services[0]
    assert backend.process is not None and backend.process.birth_identity is not None
    assert backend.process.birth_identity.value == service_birth


def test_projection_rejects_launch_identity_mismatch() -> None:
    events = (
        dataclass_replace(_event(1, LaunchEventKind.LAUNCH_RESOLVED), launch_id="launch-other"),
    )
    with pytest.raises(ValueError, match="identity"):
        project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)


def test_projection_rejects_owner_generation_mismatch() -> None:
    events = (dataclass_replace(_event(1, LaunchEventKind.LAUNCH_RESOLVED), owner_generation=4),)
    with pytest.raises(ValueError, match="owner generation"):
        project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)


def test_projection_rejects_service_not_declared_by_intent() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.SERVICE_SPAWNED,
            service="worker",
            service_generation=1,
            payload={"pid": 71},
        ),
    )
    with pytest.raises(ValueError, match="does not exist in launch intent"):
        project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)


def test_launch_failure_projects_structured_terminal_failure() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.LAUNCH_FAILED,
            payload={
                "failure_kind": "authority",
                "failure_code": "owner_exit",
                "reason": "launch owner exited",
                "retryable": False,
                "next_action": "inspect owner logs",
            },
        ),
    )
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    assert manifest.lifecycle.state is LaunchLifecycleState.FAILED
    assert manifest.lifecycle.terminal_at == events[-1].timestamp
    assert manifest.failure is not None
    assert manifest.failure.code == "owner_exit"
    assert manifest.failure.next_action == "inspect owner logs"


def test_service_failure_after_startup_preserves_satisfied_startup_fact() -> None:
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.SERVICE_SPAWNED,
            service="backend",
            service_generation=1,
            payload={"pid": 41},
        ),
        _event(
            3,
            LaunchEventKind.SERVICE_STARTUP_COMPLETE,
            service="backend",
            service_generation=1,
            asserted_by="backend-runtime",
        ),
        _event(
            4,
            LaunchEventKind.SERVICE_FAILED,
            service="backend",
            service_generation=1,
            asserted_by="backend-runtime",
            payload={
                "failure_kind": "process",
                "failure_code": "post_startup_exit",
                "reason": "backend exited after startup",
                "retryable": True,
            },
        ),
    )
    manifest = project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
    backend = manifest.observed.services[0]
    assert backend.state("startup") is LaunchFactState.SATISFIED
    assert backend.state("readiness") is LaunchFactState.UNSATISFIED
    assert manifest.failure is not None and manifest.failure.code == "post_startup_exit"


def test_duplicate_launch_owner_observation_is_rejected_by_stream_authority() -> None:
    first_birth = ProcessBirthIdentity(pid=30, boot_id="boot-a", start_ticks=100)
    second_birth = ProcessBirthIdentity(pid=31, boot_id="boot-a", start_ticks=101)
    events = (
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            payload={"pid": 30, "birth_identity": first_birth.to_payload()},
        ),
        _event(
            3,
            LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            payload={"pid": 31, "birth_identity": second_birth.to_payload()},
        ),
    )
    with pytest.raises(ValueError, match="may only be observed once"):
        project_launch_manifest(identity=IDENTITY, intent=INTENT, events=events)
