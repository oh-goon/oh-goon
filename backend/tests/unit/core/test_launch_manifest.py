from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from oh_goon.core.launch_manifest import (
    LAUNCH_MANIFEST_SCHEMA,
    LaunchEndpointIntent,
    LaunchEndpointObservation,
    LaunchFactState,
    LaunchIdentity,
    LaunchIntent,
    LaunchLifecycle,
    LaunchLifecycleState,
    LaunchManifest,
    LaunchObservedState,
    LaunchProcessObservation,
    LaunchServiceIntent,
    LaunchServiceObservation,
    ObservedFact,
)

NOW = datetime(2026, 9, 3, 19, 40, tzinfo=UTC)


def _manifest() -> LaunchManifest:
    endpoint_intent = LaunchEndpointIntent(service="backend", host="127.0.0.1", port=8787)
    process = LaunchProcessObservation(
        pid=ObservedFact(
            value=18452,
            asserted_by="launch-owner",
            observed_at=NOW,
            owner_generation=7,
            service_generation=2,
        )
    )
    endpoint = LaunchEndpointObservation(
        host=ObservedFact(
            value="127.0.0.1",
            asserted_by="backend-runtime",
            observed_at=NOW,
            owner_generation=7,
            service_generation=2,
        ),
        port=ObservedFact(
            value=8787,
            asserted_by="backend-runtime",
            observed_at=NOW,
            owner_generation=7,
            service_generation=2,
        ),
    )
    return LaunchManifest(
        identity=LaunchIdentity(
            launch_id="launch-123",
            workspace="/workspace/oh-goon",
            oh_goon_version="1267.0.38",
            owner_generation=7,
        ),
        intent=LaunchIntent(
            profile="dev",
            services=(
                LaunchServiceIntent(
                    service="backend",
                    command=("uvicorn", "oh_goon.api.app:app"),
                    endpoint=endpoint_intent,
                ),
            ),
        ),
        observed=LaunchObservedState(
            services=(
                LaunchServiceObservation(
                    service="backend",
                    service_generation=2,
                    process=process,
                    bound_endpoint=endpoint,
                    startup=ObservedFact(
                        value=LaunchFactState.SATISFIED,
                        asserted_by="backend-runtime",
                        observed_at=NOW,
                        owner_generation=7,
                        service_generation=2,
                    ),
                    readiness=ObservedFact(
                        value=LaunchFactState.SATISFIED,
                        asserted_by="backend-runtime",
                        observed_at=NOW,
                        owner_generation=7,
                        service_generation=2,
                    ),
                ),
            )
        ),
        lifecycle=LaunchLifecycle(state=LaunchLifecycleState.READY, ready_at=NOW),
    )


def test_manifest_schema_is_explicit_and_serialization_is_deterministic() -> None:
    manifest = _manifest()
    first = manifest.to_json()
    second = manifest.to_json()
    assert first == second
    payload = json.loads(first)
    assert payload["schema"] == LAUNCH_MANIFEST_SCHEMA
    assert payload["kind"] == "launch_manifest"
    assert payload["identity"]["launch_id"] == "launch-123"


def test_intent_does_not_prove_observed_runtime_state() -> None:
    manifest = LaunchManifest(
        identity=LaunchIdentity(
            launch_id="launch-intent-only",
            workspace="/workspace/oh-goon",
            oh_goon_version="1267.0.38",
            owner_generation=1,
        ),
        intent=LaunchIntent(
            profile="dev",
            services=(
                LaunchServiceIntent(
                    service="frontend",
                    command=("npm", "run", "dev"),
                    endpoint=LaunchEndpointIntent(
                        service="frontend", host="127.0.0.1", port=5173
                    ),
                ),
            ),
        ),
        observed=LaunchObservedState(),
        lifecycle=LaunchLifecycle(state=LaunchLifecycleState.RESOLVING),
    )
    payload = manifest.to_payload()
    assert payload["intent"]["services"][0]["endpoint"]["port"] == 5173
    assert payload["observed"]["services"] == []


def test_unknown_runtime_fact_is_not_replaced_with_fake_observation() -> None:
    manifest = _manifest()
    service = manifest.observed.services[0]
    assert service.health is None
    assert service.state("health") is LaunchFactState.UNKNOWN


def test_observed_service_must_belong_to_intent() -> None:
    with pytest.raises(ValueError, match="observed launch services"):
        LaunchManifest(
            identity=LaunchIdentity(
                launch_id="launch-invalid",
                workspace="/workspace/oh-goon",
                oh_goon_version="1267.0.38",
                owner_generation=1,
            ),
            intent=LaunchIntent(profile="dev", services=()),
            observed=LaunchObservedState(
                services=(LaunchServiceObservation(service="backend", service_generation=1),)
            ),
            lifecycle=LaunchLifecycle(state=LaunchLifecycleState.STARTING),
        )


def test_observation_generation_mismatch_is_rejected() -> None:
    with pytest.raises(ValueError, match="service generation"):
        LaunchServiceObservation(
            service="backend",
            service_generation=2,
            process=LaunchProcessObservation(
                pid=ObservedFact(
                    value=99,
                    asserted_by="launch-owner",
                    observed_at=NOW,
                    owner_generation=1,
                    service_generation=1,
                )
            ),
        )


def test_observed_fact_requires_timezone_and_owner_generation() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ObservedFact(
            value=1,
            asserted_by="owner",
            observed_at=datetime(2026, 9, 3),
            owner_generation=1,
        )
    with pytest.raises(ValueError, match="owner_generation"):
        ObservedFact(
            value=1,
            asserted_by="owner",
            observed_at=NOW,
            owner_generation=0,
        )


def test_service_state_observation_requires_matching_generation_and_owner() -> None:
    with pytest.raises(ValueError, match="service generation"):
        LaunchServiceObservation(
            service="backend",
            service_generation=2,
            readiness=ObservedFact(
                value=LaunchFactState.SATISFIED,
                asserted_by="backend-runtime",
                observed_at=NOW,
                owner_generation=7,
                service_generation=1,
            ),
        )

    manifest = _manifest()
    service = manifest.observed.services[0]
    stale = LaunchServiceObservation(
        service=service.service,
        service_generation=service.service_generation,
        readiness=ObservedFact(
            value=LaunchFactState.SATISFIED,
            asserted_by="stale-owner",
            observed_at=NOW,
            owner_generation=6,
            service_generation=service.service_generation,
        ),
    )
    with pytest.raises(ValueError, match="owner generation"):
        LaunchManifest(
            identity=manifest.identity,
            intent=manifest.intent,
            observed=LaunchObservedState(services=(stale,)),
            lifecycle=manifest.lifecycle,
        )

@pytest.mark.parametrize(
    ("state", "terminal"),
    [
        (LaunchLifecycleState.RESOLVING, False),
        (LaunchLifecycleState.STARTING, False),
        (LaunchLifecycleState.READY, False),
        (LaunchLifecycleState.DEGRADED, False),
        (LaunchLifecycleState.STOPPING, False),
        (LaunchLifecycleState.STOPPED, True),
        (LaunchLifecycleState.FAILED, True),
        (LaunchLifecycleState.ABORTED, True),
    ],
)
def test_launch_lifecycle_is_single_terminality_owner(state, terminal) -> None:
    lifecycle = LaunchLifecycle(
        state=state,
        terminal_at=NOW if terminal else None,
    )
    assert lifecycle.is_terminal is terminal


@pytest.mark.parametrize(
    ("state", "terminal_at"),
    [
        (LaunchLifecycleState.READY, NOW),
        (LaunchLifecycleState.DEGRADED, NOW),
        (LaunchLifecycleState.STOPPING, NOW),
        (LaunchLifecycleState.STOPPED, None),
        (LaunchLifecycleState.FAILED, None),
        (LaunchLifecycleState.ABORTED, None),
    ],
)
def test_launch_lifecycle_rejects_state_timestamp_disagreement(state, terminal_at) -> None:
    with pytest.raises(ValueError, match="terminal_at must be present exactly when state is terminal"):
        LaunchLifecycle(state=state, terminal_at=terminal_at)
