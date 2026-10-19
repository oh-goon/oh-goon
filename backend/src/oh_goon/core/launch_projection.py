from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from oh_goon.core.launch_events import (
    LaunchEvent,
    LaunchEventKind,
    project_launch_lifecycle,
    validate_launch_event_stream,
)
from oh_goon.core.launch_manifest import (
    LaunchEndpointObservation,
    LaunchFactState,
    LaunchFailure,
    LaunchIdentity,
    LaunchIntent,
    LaunchManifest,
    LaunchObservedState,
    LaunchProcessObservation,
    LaunchServiceObservation,
    ObservedFact,
)
from oh_goon.core.process_birth import ProcessBirthIdentity


def project_launch_manifest(
    *,
    identity: LaunchIdentity,
    intent: LaunchIntent,
    events: Iterable[LaunchEvent],
) -> LaunchManifest:
    """Reduce owner-issued Launch Events into the canonical read model.

    The reducer has no runtime authority. It cannot start, stop, probe, retry, or
    repair services; it only validates and materializes structured owner facts.
    """

    ordered = validate_launch_event_stream(events)
    for event in ordered:
        match (
            event.launch_id == identity.launch_id,
            event.owner_generation == identity.owner_generation,
        ):
            case (False, _):
                raise ValueError("Launch Event identity does not match Launch Manifest identity")
            case (_, False):
                raise ValueError(
                    "Launch Event owner generation does not match Launch Manifest identity"
                )

    requested = {service.service: service for service in intent.services}
    observed: dict[str, LaunchServiceObservation] = {}
    owner_process: LaunchProcessObservation | None = None
    failure: LaunchFailure | None = None

    for event in ordered:
        if event.service is not None and event.service not in requested:
            raise ValueError("Launch Event service does not exist in launch intent")
        match event.event:
            case LaunchEventKind.LAUNCH_OWNER_OBSERVED:
                owner_process = _process_observation(event, service_generation=None)
            case LaunchEventKind.SERVICE_SPAWNED:
                observed[event.service] = _spawned_observation(event)
            case LaunchEventKind.SERVICE_BOUND:
                service = _current_service(observed, event)
                observed[event.service] = replace(service, bound_endpoint=_endpoint_fact(event))
            case LaunchEventKind.SERVICE_STARTUP_COMPLETE:
                service = _current_service(observed, event)
                observed[event.service] = replace(
                    service,
                    startup=_state_fact(event, LaunchFactState.SATISFIED),
                )
            case LaunchEventKind.SERVICE_READY:
                service = _current_service(observed, event)
                if service.state("startup") is not LaunchFactState.SATISFIED:
                    raise ValueError("service.ready requires authoritative startup completion")
                observed[event.service] = replace(
                    service,
                    readiness=_state_fact(event, LaunchFactState.SATISFIED),
                )
            case LaunchEventKind.SERVICE_FAILED:
                service = _current_service(observed, event)
                startup = service.startup
                if service.state("startup") is LaunchFactState.UNKNOWN:
                    startup = _state_fact(event, LaunchFactState.UNSATISFIED)
                observed[event.service] = replace(
                    service,
                    startup=startup,
                    readiness=_state_fact(event, LaunchFactState.UNSATISFIED),
                )
                failure = _failure_from_event(event)
            case LaunchEventKind.LAUNCH_READY:
                _require_ready_services(intent, observed)
            case LaunchEventKind.LAUNCH_FAILED:
                failure = _failure_from_event(event)

    services = tuple(observed[name] for name in sorted(observed))
    return LaunchManifest(
        identity=identity,
        intent=intent,
        observed=LaunchObservedState(owner_process=owner_process, services=services),
        lifecycle=project_launch_lifecycle(ordered),
        failure=failure,
    )


def _spawned_observation(event: LaunchEvent) -> LaunchServiceObservation:
    assert event.service is not None and event.service_generation is not None
    return LaunchServiceObservation(
        service=event.service,
        service_generation=event.service_generation,
        process=_process_observation(event, service_generation=event.service_generation),
    )


def _process_observation(
    event: LaunchEvent, *, service_generation: int | None
) -> LaunchProcessObservation:
    common = {
        "asserted_by": event.asserted_by,
        "observed_at": event.timestamp,
        "owner_generation": event.owner_generation,
        "service_generation": service_generation,
    }
    birth_payload = event.payload.get("birth_identity")
    birth = None
    if birth_payload is not None:
        birth = ObservedFact(
            value=ProcessBirthIdentity.from_payload(birth_payload),
            **common,
        )
    return LaunchProcessObservation(
        pid=ObservedFact(value=int(event.payload["pid"]), **common),
        birth_identity=birth,
    )


def _current_service(
    observed: dict[str, LaunchServiceObservation],
    event: LaunchEvent,
) -> LaunchServiceObservation:
    assert event.service is not None and event.service_generation is not None
    service = observed.get(event.service)
    if service is None or service.service_generation != event.service_generation:
        raise ValueError("Launch Event has no current matching service process")
    return service


def _endpoint_fact(event: LaunchEvent) -> LaunchEndpointObservation:
    assert event.service_generation is not None
    common = {
        "asserted_by": event.asserted_by,
        "observed_at": event.timestamp,
        "owner_generation": event.owner_generation,
        "service_generation": event.service_generation,
    }
    return LaunchEndpointObservation(
        host=ObservedFact(value=str(event.payload["host"]), **common),
        port=ObservedFact(value=int(event.payload["port"]), **common),
    )


def _state_fact(event: LaunchEvent, state: LaunchFactState) -> ObservedFact[LaunchFactState]:
    assert event.service_generation is not None
    return ObservedFact(
        value=state,
        asserted_by=event.asserted_by,
        observed_at=event.timestamp,
        owner_generation=event.owner_generation,
        service_generation=event.service_generation,
    )


def _failure_from_event(event: LaunchEvent) -> LaunchFailure:
    next_action = event.payload.get("next_action")
    return LaunchFailure(
        kind=str(event.payload["failure_kind"]),
        code=str(event.payload["failure_code"]),
        owner=event.asserted_by,
        reason=str(event.payload["reason"]),
        retryable=bool(event.payload["retryable"]),
        next_action=None if next_action is None else str(next_action),
    )


def _require_ready_services(
    intent: LaunchIntent,
    observed: dict[str, LaunchServiceObservation],
) -> None:
    for requested in intent.services:
        if not requested.required:
            continue
        service = observed.get(requested.service)
        if service is None or service.state("readiness") is not LaunchFactState.SATISFIED:
            raise ValueError("launch.ready requires every required service to be ready")
