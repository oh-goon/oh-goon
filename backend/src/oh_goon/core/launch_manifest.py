from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from oh_goon.core.process_birth import ProcessBirthIdentity

LAUNCH_MANIFEST_SCHEMA = "oh-goon.launch/v1"
LAUNCH_MANIFEST_KIND = "launch_manifest"

T = TypeVar("T")


class LaunchLifecycleState(StrEnum):
    RESOLVING = "resolving"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    ABORTED = "aborted"


class LaunchFactState(StrEnum):
    UNKNOWN = "unknown"
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"


@dataclass(frozen=True, slots=True)
class ObservedFact(Generic[T]):
    """One runtime fact with the identity needed to reject stale observations."""

    value: T
    asserted_by: str
    observed_at: datetime
    owner_generation: int
    service_generation: int | None = None

    def __post_init__(self) -> None:
        if not self.asserted_by:
            raise ValueError("observed fact asserted_by is required")
        if self.owner_generation < 1:
            raise ValueError("observed fact owner_generation must be >= 1")
        if self.service_generation is not None and self.service_generation < 1:
            raise ValueError("observed fact service_generation must be >= 1")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("observed fact observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LaunchEndpointIntent:
    service: str
    host: str
    port: int
    exposure: str = "local"

    def __post_init__(self) -> None:
        if not self.service or not self.host:
            raise ValueError("launch endpoint intent requires service and host")
        if not 1 <= self.port <= 65535:
            raise ValueError("launch endpoint port must be in 1..65535")


@dataclass(frozen=True, slots=True)
class LaunchServiceIntent:
    service: str
    command: tuple[str, ...]
    required: bool = True
    endpoint: LaunchEndpointIntent | None = None

    def __post_init__(self) -> None:
        if not self.service:
            raise ValueError("launch service name is required")
        if not self.command:
            raise ValueError("launch service command is required")
        if self.endpoint is not None and self.endpoint.service != self.service:
            raise ValueError("launch service endpoint must belong to the same service")


@dataclass(frozen=True, slots=True)
class LaunchIntent:
    profile: str
    services: tuple[LaunchServiceIntent, ...]

    def __post_init__(self) -> None:
        if not self.profile:
            raise ValueError("launch profile is required")
        names = [service.service for service in self.services]
        if len(names) != len(set(names)):
            raise ValueError("launch intent service names must be unique")

    def identity(self) -> str:
        """Stable identity of resolved launch intent; never derived from observed state."""

        payload = {
            "profile": self.profile,
            "services": [
                {
                    "service": service.service,
                    "command": list(service.command),
                    "required": service.required,
                    "endpoint": None
                    if service.endpoint is None
                    else {
                        "service": service.endpoint.service,
                        "host": service.endpoint.host,
                        "port": service.endpoint.port,
                        "exposure": service.endpoint.exposure,
                    },
                }
                for service in self.services
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True, slots=True)
class LaunchProcessObservation:
    pid: ObservedFact[int]
    birth_identity: ObservedFact[ProcessBirthIdentity] | None = None

    def __post_init__(self) -> None:
        if self.pid.value <= 0:
            raise ValueError("observed process pid must be positive")
        if self.birth_identity is not None:
            if self.birth_identity.value.pid != self.pid.value:
                raise ValueError("process birth identity pid must match observed pid")
            if self.birth_identity.owner_generation != self.pid.owner_generation:
                raise ValueError("process pid and birth identity must share owner generation")
            if self.birth_identity.service_generation != self.pid.service_generation:
                raise ValueError("process pid and birth identity must share service generation")


@dataclass(frozen=True, slots=True)
class LaunchEndpointObservation:
    host: ObservedFact[str]
    port: ObservedFact[int]

    def __post_init__(self) -> None:
        if not self.host.value:
            raise ValueError("observed endpoint host is required")
        if not 1 <= self.port.value <= 65535:
            raise ValueError("observed endpoint port must be in 1..65535")
        if self.host.owner_generation != self.port.owner_generation:
            raise ValueError("observed endpoint facts must share owner generation")
        if self.host.service_generation != self.port.service_generation:
            raise ValueError("observed endpoint facts must share service generation")


@dataclass(frozen=True, slots=True)
class LaunchServiceObservation:
    service: str
    service_generation: int
    process: LaunchProcessObservation | None = None
    bound_endpoint: LaunchEndpointObservation | None = None
    startup: ObservedFact[LaunchFactState] | None = None
    readiness: ObservedFact[LaunchFactState] | None = None
    health: ObservedFact[LaunchFactState] | None = None

    def __post_init__(self) -> None:
        if not self.service:
            raise ValueError("launch service observation requires service")
        if self.service_generation < 1:
            raise ValueError("launch service generation must be >= 1")
        if (
            self.process is not None
            and self.process.pid.service_generation != self.service_generation
        ):
            raise ValueError("process observation belongs to another service generation")
        if self.bound_endpoint is not None:
            observed_generation = self.bound_endpoint.host.service_generation
            if observed_generation != self.service_generation:
                raise ValueError("endpoint observation belongs to another service generation")
        for name, fact in (
            ("startup", self.startup),
            ("readiness", self.readiness),
            ("health", self.health),
        ):
            if fact is not None and fact.service_generation != self.service_generation:
                raise ValueError(f"{name} observation belongs to another service generation")

    def state(self, name: str) -> LaunchFactState:
        if name not in {"startup", "readiness", "health"}:
            raise ValueError(f"unknown service state fact: {name}")
        fact = getattr(self, name)
        return LaunchFactState.UNKNOWN if fact is None else fact.value


@dataclass(frozen=True, slots=True)
class LaunchObservedState:
    owner_process: LaunchProcessObservation | None = None
    services: tuple[LaunchServiceObservation, ...] = ()

    def __post_init__(self) -> None:
        if self.owner_process is not None:
            if self.owner_process.pid.service_generation is not None:
                raise ValueError("launch owner process must not carry service generation")
            if (
                self.owner_process.birth_identity is not None
                and self.owner_process.birth_identity.service_generation is not None
            ):
                raise ValueError("launch owner birth identity must not carry service generation")
        names = [service.service for service in self.services]
        if len(names) != len(set(names)):
            raise ValueError("observed launch service names must be unique")


@dataclass(frozen=True, slots=True)
class LaunchIdentity:
    launch_id: str
    workspace: str
    oh_goon_version: str
    owner_generation: int
    source_identity: str | None = None
    configuration_identity: str | None = None

    def __post_init__(self) -> None:
        if not self.launch_id or not self.workspace or not self.oh_goon_version:
            raise ValueError("launch identity requires launch_id, workspace and version")
        if self.owner_generation < 1:
            raise ValueError("launch owner_generation must be >= 1")


@dataclass(frozen=True, slots=True)
class LaunchLifecycle:
    state: LaunchLifecycleState
    started_at: datetime | None = None
    ready_at: datetime | None = None
    terminal_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in {
            LaunchLifecycleState.STOPPED,
            LaunchLifecycleState.FAILED,
            LaunchLifecycleState.ABORTED,
        }

    def __post_init__(self) -> None:
        for value in (self.started_at, self.ready_at, self.terminal_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("launch lifecycle timestamps must be timezone-aware")
        if self.is_terminal != (self.terminal_at is not None):
            raise ValueError(
                "launch lifecycle terminal_at must be present exactly when state is terminal"
            )


@dataclass(frozen=True, slots=True)
class LaunchFailure:
    kind: str
    code: str
    owner: str
    reason: str
    retryable: bool
    next_action: str | None = None

    def __post_init__(self) -> None:
        if not self.kind or not self.code or not self.owner or not self.reason:
            raise ValueError("launch failure kind/code/owner/reason are required")


@dataclass(frozen=True, slots=True)
class LaunchManifest:
    """Versioned read model of launch intent and authoritative observed facts."""

    identity: LaunchIdentity
    intent: LaunchIntent
    observed: LaunchObservedState
    lifecycle: LaunchLifecycle
    failure: LaunchFailure | None = None
    schema: str = LAUNCH_MANIFEST_SCHEMA
    kind: str = LAUNCH_MANIFEST_KIND

    def __post_init__(self) -> None:
        if self.schema != LAUNCH_MANIFEST_SCHEMA:
            raise ValueError("unsupported Launch Manifest schema")
        if self.kind != LAUNCH_MANIFEST_KIND:
            raise ValueError("unsupported Launch Manifest kind")
        requested = {service.service for service in self.intent.services}
        observed = {service.service for service in self.observed.services}
        if not observed.issubset(requested):
            raise ValueError("observed launch services must exist in launch intent")
        if self.observed.owner_process is not None:
            owner_facts = [self.observed.owner_process.pid]
            if self.observed.owner_process.birth_identity is not None:
                owner_facts.append(self.observed.owner_process.birth_identity)
            for fact in owner_facts:
                if fact.owner_generation != self.identity.owner_generation:
                    raise ValueError(
                        "observed owner fact belongs to another launch owner generation"
                    )
        for service in self.observed.services:
            for fact in _service_observed_facts(service):
                if fact.owner_generation != self.identity.owner_generation:
                    raise ValueError("observed fact belongs to another launch owner generation")

    def to_payload(self) -> dict[str, object]:
        return _json_compatible(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), sort_keys=True, separators=(",", ":"))


def _json_compatible(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    return value


def _service_observed_facts(service: LaunchServiceObservation) -> tuple[ObservedFact[object], ...]:
    facts: list[ObservedFact[object]] = []
    if service.process is not None:
        facts.append(service.process.pid)
        if service.process.birth_identity is not None:
            facts.append(service.process.birth_identity)
    if service.bound_endpoint is not None:
        facts.extend((service.bound_endpoint.host, service.bound_endpoint.port))
    for fact in (service.startup, service.readiness, service.health):
        if fact is not None:
            facts.append(fact)
    return tuple(facts)
