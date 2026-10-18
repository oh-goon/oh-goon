from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Iterable, Mapping

from oh_goon.core.launch_manifest import LaunchLifecycle, LaunchLifecycleState
from oh_goon.core.process_birth import ProcessBirthIdentity

LAUNCH_EVENT_SCHEMA = "oh-goon.launch-event/v1"
LAUNCH_EVENT_KIND = "launch_event"


class LaunchEventKind(StrEnum):
    LAUNCH_RESOLVED = "launch.resolved"
    LAUNCH_OWNER_OBSERVED = "launch.owner_observed"
    SERVICE_SPAWNED = "service.spawned"
    SERVICE_BOUND = "service.bound"
    SERVICE_STARTUP_COMPLETE = "service.startup_complete"
    SERVICE_READY = "service.ready"
    SERVICE_FAILED = "service.failed"
    LAUNCH_READY = "launch.ready"
    LAUNCH_STOPPING = "launch.stopping"
    LAUNCH_STOPPED = "launch.stopped"
    LAUNCH_FAILED = "launch.failed"
    LAUNCH_ABORTED = "launch.aborted"


_SERVICE_EVENTS = frozenset(
    {
        LaunchEventKind.SERVICE_SPAWNED,
        LaunchEventKind.SERVICE_BOUND,
        LaunchEventKind.SERVICE_STARTUP_COMPLETE,
        LaunchEventKind.SERVICE_READY,
        LaunchEventKind.SERVICE_FAILED,
    }
)
_TERMINAL_EVENTS = frozenset(
    {
        LaunchEventKind.LAUNCH_STOPPED,
        LaunchEventKind.LAUNCH_FAILED,
        LaunchEventKind.LAUNCH_ABORTED,
    }
)


@dataclass(frozen=True, slots=True)
class LaunchEvent:
    launch_id: str
    sequence: int
    event: LaunchEventKind
    timestamp: datetime
    asserted_by: str
    owner_generation: int
    service: str | None = None
    service_generation: int | None = None
    payload: Mapping[str, object] = field(default_factory=dict)
    schema: str = LAUNCH_EVENT_SCHEMA
    kind: str = LAUNCH_EVENT_KIND

    def __post_init__(self) -> None:
        if self.schema != LAUNCH_EVENT_SCHEMA or self.kind != LAUNCH_EVENT_KIND:
            raise ValueError("unsupported Launch Event schema/kind")
        if not self.launch_id or not self.asserted_by:
            raise ValueError("Launch Event requires launch_id and asserted_by")
        if self.sequence < 1:
            raise ValueError("Launch Event sequence must be >= 1")
        if self.owner_generation < 1:
            raise ValueError("Launch Event owner_generation must be >= 1")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Launch Event timestamp must be timezone-aware")
        if self.event in _SERVICE_EVENTS:
            if not self.service or self.service_generation is None:
                raise ValueError("service Launch Event requires service and service_generation")
            if self.service_generation < 1:
                raise ValueError("service_generation must be >= 1")
        elif self.service is not None or self.service_generation is not None:
            raise ValueError("launch-scoped event must not carry service identity")
        _validate_event_payload(self.event, self.payload)
        # Events are immutable evidence. Convert caller-owned mappings to a private,
        # read-only view so later mutation cannot change event meaning.
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_payload(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "kind": self.kind,
            "launch_id": self.launch_id,
            "sequence": self.sequence,
            "event": self.event.value,
            "timestamp": self.timestamp.isoformat(),
            "asserted_by": self.asserted_by,
            "owner_generation": self.owner_generation,
            "service": self.service,
            "service_generation": self.service_generation,
            "payload": _json_compatible(self.payload),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))


@dataclass(slots=True)
class _LaunchStreamState:
    launch_id: str
    owner_generation: int
    previous_sequence: int = 0
    resolved_seen: bool = False
    terminal_seen: bool = False
    launch_ready_seen: bool = False
    launch_stopping_seen: bool = False
    owner_observed_seen: bool = False
    service_generations: dict[str, int] = field(default_factory=dict)
    service_states: dict[str, set[LaunchEventKind]] = field(default_factory=dict)

    def accept(self, event: LaunchEvent) -> None:
        self._accept_envelope(event)
        if event.event is LaunchEventKind.LAUNCH_OWNER_OBSERVED:
            self._accept_owner_observed()
        if event.event in _SERVICE_EVENTS:
            self._accept_service(event)
        self._accept_launch_transition(event.event)
        self.previous_sequence = event.sequence

    def _accept_envelope(self, event: LaunchEvent) -> None:
        if event.launch_id != self.launch_id:
            raise ValueError("Launch Event stream cannot mix launch identities")
        if event.owner_generation != self.owner_generation:
            raise ValueError("Launch Event stream cannot mix owner generations")
        if event.sequence <= self.previous_sequence:
            raise ValueError("Launch Event sequence must be strictly increasing")
        if self.terminal_seen:
            raise ValueError("Launch Event stream cannot transition after terminal event")
        if event.event is LaunchEventKind.LAUNCH_RESOLVED:
            if self.resolved_seen or self.previous_sequence != 0:
                raise ValueError("launch.resolved must be the first and only resolved event")
            self.resolved_seen = True
        elif not self.resolved_seen:
            raise ValueError("launch.resolved must precede runtime events")
        if self.launch_stopping_seen and event.event is not LaunchEventKind.LAUNCH_STOPPED:
            raise ValueError("launch.stopping may only transition to launch.stopped")

    def _accept_owner_observed(self) -> None:
        if self.owner_observed_seen:
            raise ValueError("launch owner process may only be observed once per owner generation")
        if self.service_generations:
            raise ValueError("launch owner process must be observed before service processes")
        if self.launch_ready_seen:
            raise ValueError("launch owner process cannot first be observed after launch.ready")
        self.owner_observed_seen = True

    def _accept_service(self, event: LaunchEvent) -> None:
        assert event.service is not None and event.service_generation is not None
        current = self.service_generations.get(event.service)
        state = self.service_states.setdefault(event.service, set())
        if event.event is LaunchEventKind.SERVICE_SPAWNED:
            self._accept_spawn(event, current, state)
            return
        if current is None:
            raise ValueError("service runtime event requires prior service.spawned")
        if event.service_generation != current:
            raise ValueError("service runtime event belongs to stale service generation")
        if LaunchEventKind.SERVICE_FAILED in state:
            raise ValueError("service generation cannot transition after service.failed")
        if event.event in state:
            raise ValueError(f"{event.event.value} may only be asserted once per service generation")
        self._accept_current_service_fact(event.event, state)

    def _accept_spawn(self, event: LaunchEvent, current: int | None, state: set[LaunchEventKind]) -> None:
        assert event.service is not None and event.service_generation is not None
        if self.launch_ready_seen:
            raise ValueError("service.spawned cannot create a new generation after launch.ready")
        if current is not None and not ({LaunchEventKind.SERVICE_READY, LaunchEventKind.SERVICE_FAILED} & state):
            raise ValueError("service.spawned cannot replace a service generation still starting")
        expected = 1 if current is None else current + 1
        if event.service_generation != expected:
            raise ValueError("service.spawned must advance service generation exactly once")
        self.service_generations[event.service] = event.service_generation
        self.service_states[event.service] = {LaunchEventKind.SERVICE_SPAWNED}

    @staticmethod
    def _accept_current_service_fact(kind: LaunchEventKind, state: set[LaunchEventKind]) -> None:
        if kind is LaunchEventKind.SERVICE_READY:
            if LaunchEventKind.SERVICE_STARTUP_COMPLETE not in state:
                raise ValueError("service.ready requires prior service.startup_complete")
        elif kind is not LaunchEventKind.SERVICE_FAILED and LaunchEventKind.SERVICE_READY in state:
            raise ValueError("service runtime facts cannot arrive after service.ready")
        state.add(kind)

    def _accept_launch_transition(self, kind: LaunchEventKind) -> None:
        handlers = {
            LaunchEventKind.LAUNCH_READY: self._accept_launch_ready,
            LaunchEventKind.LAUNCH_STOPPING: self._accept_launch_stopping,
            LaunchEventKind.LAUNCH_STOPPED: self._accept_launch_stopped,
            LaunchEventKind.LAUNCH_ABORTED: self._accept_launch_aborted,
            LaunchEventKind.LAUNCH_FAILED: self._accept_launch_failed,
        }
        handler = handlers.get(kind)
        if handler is not None:
            handler()

    def _accept_launch_ready(self) -> None:
        if self.launch_ready_seen:
            raise ValueError("launch.ready may only be asserted once")
        if any(LaunchEventKind.SERVICE_FAILED in state for state in self.service_states.values()):
            raise ValueError("launch.ready cannot follow a failed current service generation")
        self.launch_ready_seen = True

    def _accept_launch_stopping(self) -> None:
        if not self.launch_ready_seen:
            raise ValueError("launch.stopping requires prior launch.ready")
        self.launch_stopping_seen = True

    def _accept_launch_stopped(self) -> None:
        if not self.launch_stopping_seen:
            raise ValueError("launch.stopped requires prior launch.stopping")
        self.terminal_seen = True

    def _accept_launch_aborted(self) -> None:
        if self.launch_ready_seen:
            raise ValueError("launch.aborted is only valid before launch.ready")
        self.terminal_seen = True

    def _accept_launch_failed(self) -> None:
        self.terminal_seen = True


def validate_launch_event_stream(events: Iterable[LaunchEvent]) -> tuple[LaunchEvent, ...]:
    """Validate the canonical monotonic launch/service state machine."""
    ordered = tuple(events)
    if not ordered:
        return ordered
    state = _LaunchStreamState(ordered[0].launch_id, ordered[0].owner_generation)
    for event in ordered:
        state.accept(event)
    return ordered


def project_launch_lifecycle(events: Iterable[LaunchEvent]) -> LaunchLifecycle:
    ordered = validate_launch_event_stream(events)
    state = LaunchLifecycleState.RESOLVING
    started_at: datetime | None = None
    ready_at: datetime | None = None
    terminal_at: datetime | None = None
    for event in ordered:
        if event.event is LaunchEventKind.LAUNCH_RESOLVED:
            state = LaunchLifecycleState.RESOLVING
        elif event.event in _SERVICE_EVENTS:
            if state is LaunchLifecycleState.RESOLVING:
                state = LaunchLifecycleState.STARTING
            if started_at is None:
                started_at = event.timestamp
            if event.event is LaunchEventKind.SERVICE_FAILED:
                state = LaunchLifecycleState.DEGRADED
        elif event.event is LaunchEventKind.LAUNCH_READY:
            state = LaunchLifecycleState.READY
            ready_at = event.timestamp
            if started_at is None:
                started_at = event.timestamp
        elif event.event is LaunchEventKind.LAUNCH_STOPPING:
            state = LaunchLifecycleState.STOPPING
        elif event.event is LaunchEventKind.LAUNCH_STOPPED:
            state = LaunchLifecycleState.STOPPED
            terminal_at = event.timestamp
        elif event.event is LaunchEventKind.LAUNCH_FAILED:
            state = LaunchLifecycleState.FAILED
            terminal_at = event.timestamp
        elif event.event is LaunchEventKind.LAUNCH_ABORTED:
            state = LaunchLifecycleState.ABORTED
            terminal_at = event.timestamp
    return LaunchLifecycle(
        state=state,
        started_at=started_at,
        ready_at=ready_at,
        terminal_at=terminal_at,
    )



def _validate_event_payload(event: LaunchEventKind, payload: Mapping[str, object]) -> None:
    if event is LaunchEventKind.LAUNCH_OWNER_OBSERVED:
        _validate_process_payload(event.value, payload, birth_required=True)
    elif event is LaunchEventKind.SERVICE_SPAWNED:
        _validate_process_payload(event.value, payload, birth_required=True)
    elif event is LaunchEventKind.SERVICE_BOUND:
        host = payload.get("host")
        port = payload.get("port")
        if not isinstance(host, str) or not host:
            raise ValueError("service.bound requires non-empty payload host")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("service.bound requires payload port in 1..65535")
    elif event in {LaunchEventKind.SERVICE_FAILED, LaunchEventKind.LAUNCH_FAILED}:
        for key in ("failure_kind", "failure_code", "reason"):
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{event.value} requires non-empty payload {key}")
        if not isinstance(payload.get("retryable"), bool):
            raise ValueError(f"{event.value} requires boolean payload retryable")
        next_action = payload.get("next_action")
        if next_action is not None and (not isinstance(next_action, str) or not next_action):
            raise ValueError(f"{event.value} payload next_action must be a non-empty string")


def _validate_process_payload(name: str, payload: Mapping[str, object], *, birth_required: bool) -> None:
    pid = payload.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError(f"{name} requires positive integer payload pid")
    birth_payload = payload.get("birth_identity")
    if birth_payload is None:
        if birth_required:
            raise ValueError(f"{name} requires payload birth_identity")
        return
    birth = ProcessBirthIdentity.from_payload(birth_payload)
    if birth.pid != pid:
        raise ValueError(f"{name} birth identity pid must match payload pid")

def _json_compatible(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value
