from __future__ import annotations

import json
from dataclasses import dataclass

from oh_goon.core.launch_events import LaunchEvent, LaunchEventKind, validate_launch_event_stream

LAUNCH_LOG_SCHEMA = "oh-goon.launch-log/v1"


@dataclass(frozen=True, slots=True)
class SafeLaunchLogEntry:
    sequence: int
    event: str
    timestamp: str
    asserted_by: str
    service: str | None
    service_generation: int | None
    details: dict[str, object]

    def to_payload(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "event": self.event,
            "timestamp": self.timestamp,
            "asserted_by": self.asserted_by,
            "service": self.service,
            "service_generation": self.service_generation,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class SafeLaunchLog:
    launch_id: str
    owner_generation: int
    entries: tuple[SafeLaunchLogEntry, ...]
    schema: str = LAUNCH_LOG_SCHEMA

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "launch_id": self.launch_id,
            "owner_generation": self.owner_generation,
            "entries": [entry.to_payload() for entry in self.entries],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))


def safe_launch_log(events: tuple[LaunchEvent, ...]) -> SafeLaunchLog:
    """Project one exact Launch Event stream without raw process/credential payloads."""

    ordered = validate_launch_event_stream(events)
    if not ordered:
        raise ValueError("launch log requires at least one Launch Event")
    entries = tuple(_safe_entry(event) for event in ordered)
    return SafeLaunchLog(
        launch_id=ordered[0].launch_id,
        owner_generation=ordered[0].owner_generation,
        entries=entries,
    )


def _safe_entry(event: LaunchEvent) -> SafeLaunchLogEntry:
    details: dict[str, object] = {}
    if event.event is LaunchEventKind.SERVICE_BOUND:
        details = {
            "host": event.payload["host"],
            "port": event.payload["port"],
        }
    elif event.event in {LaunchEventKind.SERVICE_FAILED, LaunchEventKind.LAUNCH_FAILED}:
        details = {
            "failure_kind": event.payload["failure_kind"],
            "failure_code": event.payload["failure_code"],
            "reason": event.payload["reason"],
            "retryable": event.payload["retryable"],
        }
    return SafeLaunchLogEntry(
        sequence=event.sequence,
        event=event.event.value,
        timestamp=event.timestamp.isoformat(),
        asserted_by=event.asserted_by,
        service=event.service,
        service_generation=event.service_generation,
        details=details,
    )
