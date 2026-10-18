from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from oh_goon.core.launch_events import LaunchEvent, LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchIdentity,
    LaunchIntent,
    LaunchServiceIntent,
)
from oh_goon.core.launch_navigation import safe_launch_navigation_target
from oh_goon.core.launch_projection import project_launch_manifest
from oh_goon.core.process_birth import ProcessBirthIdentity

T0 = datetime(2026, 9, 4, 5, 0, tzinfo=UTC)
BIRTH = ProcessBirthIdentity(pid=123, boot_id="boot-a", start_ticks=77)
IDENTITY = LaunchIdentity(
    launch_id="launch-a",
    workspace="/repo",
    oh_goon_version="1267.0.48",
    owner_generation=1,
)


def _intent(profile: str = "contributor-dev") -> LaunchIntent:
    if profile == "built-local":
        return LaunchIntent(
            profile=profile,
            services=(
                LaunchServiceIntent(
                    service="backend",
                    command=("uvicorn",),
                    endpoint=LaunchEndpointIntent("backend", "127.0.0.1", 8787),
                ),
            ),
        )
    return LaunchIntent(
        profile=profile,
        services=(
            LaunchServiceIntent(
                service="backend",
                command=("uvicorn",),
                endpoint=LaunchEndpointIntent("backend", "127.0.0.1", 8787),
            ),
            LaunchServiceIntent(
                service="frontend",
                command=("npm", "run", "dev"),
                endpoint=LaunchEndpointIntent("frontend", "127.0.0.1", 5173),
            ),
        ),
    )


def _event(sequence: int, event: LaunchEventKind, **kwargs) -> LaunchEvent:
    return LaunchEvent(
        launch_id="launch-a",
        sequence=sequence,
        event=event,
        timestamp=T0 + timedelta(seconds=sequence),
        asserted_by="owner",
        owner_generation=1,
        **kwargs,
    )


def _ready_manifest(*, profile: str = "contributor-dev", host: str = "127.0.0.1", port: int = 5173):
    service = "backend" if profile == "built-local" else "frontend"
    events = [
        _event(1, LaunchEventKind.LAUNCH_RESOLVED),
        _event(
            2,
            LaunchEventKind.LAUNCH_OWNER_OBSERVED,
            payload={"pid": 123, "birth_identity": BIRTH.to_payload()},
        ),
    ]
    sequence = 3
    services = ["backend"] if profile == "built-local" else ["backend", "frontend"]
    for name in services:
        events.extend(
            [
                _event(
                    sequence,
                    LaunchEventKind.SERVICE_SPAWNED,
                    service=name,
                    service_generation=1,
                    payload={
                        "pid": 200 + sequence,
                        "birth_identity": ProcessBirthIdentity(
                            pid=200 + sequence,
                            boot_id=f"boot-{name}",
                            start_ticks=sequence,
                        ).to_payload(),
                    },
                ),
                _event(
                    sequence + 1,
                    LaunchEventKind.SERVICE_STARTUP_COMPLETE,
                    service=name,
                    service_generation=1,
                ),
                _event(
                    sequence + 2,
                    LaunchEventKind.SERVICE_BOUND,
                    service=name,
                    service_generation=1,
                    payload={
                        "host": host if name == service else "127.0.0.1",
                        "port": port if name == service else 8787,
                    },
                ),
                _event(
                    sequence + 3,
                    LaunchEventKind.SERVICE_READY,
                    service=name,
                    service_generation=1,
                ),
            ]
        )
        sequence += 4
    events.append(_event(sequence, LaunchEventKind.LAUNCH_READY))
    return project_launch_manifest(identity=IDENTITY, intent=_intent(profile), events=tuple(events))


def test_contributor_navigation_uses_observed_ready_frontend_not_configured_port() -> None:
    manifest = _ready_manifest(port=5199)
    target = safe_launch_navigation_target(
        manifest,
        process_observer=lambda pid: BIRTH if pid == 123 else None,
    )
    assert target.service == "frontend"
    assert target.endpoint == "http://127.0.0.1:5199"


def test_built_navigation_uses_observed_backend() -> None:
    manifest = _ready_manifest(profile="built-local", port=8899)
    target = safe_launch_navigation_target(
        manifest,
        process_observer=lambda pid: BIRTH if pid == 123 else None,
    )
    assert target.service == "backend"
    assert target.endpoint == "http://127.0.0.1:8899"


def test_navigation_refuses_unproven_owner_and_non_loopback_observation() -> None:
    manifest = _ready_manifest()
    with pytest.raises(ValueError, match="owner cannot be positively proven"):
        safe_launch_navigation_target(manifest, process_observer=lambda _pid: None)

    public = _ready_manifest(host="0.0.0.0")
    with pytest.raises(ValueError, match="not loopback-only"):
        safe_launch_navigation_target(
            public,
            process_observer=lambda pid: BIRTH if pid == 123 else None,
        )
