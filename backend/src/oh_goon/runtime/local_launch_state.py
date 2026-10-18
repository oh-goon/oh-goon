from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

from oh_goon.core.launch_events import LaunchEvent, LaunchEventKind
from oh_goon.core.launch_manifest import (
    LaunchEndpointIntent,
    LaunchIdentity,
    LaunchIntent,
    LaunchManifest,
    LaunchServiceIntent,
)
from oh_goon.core.launch_projection import project_launch_manifest
from oh_goon.core.path_authority import ensure_private_directory, storage_component
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.exact_file_write import (
    exact_file_write_lock,
    prepared_text_file,
    replace_prepared_file,
)
from oh_goon.runtime.database import default_database_path

LOCAL_LAUNCH_RECORD_SCHEMA = "oh-goon.local-launch-record/v1"


@dataclass(frozen=True, slots=True)
class LocalLaunchEventStore:
    """Atomic storage for owner-issued local Launch Events.

    The store serializes facts and validates their projection. It does not discover
    processes, probe endpoints, choose readiness, transfer ownership, or grant access.
    """

    root: Path

    @classmethod
    def from_database(cls, database_path: Path) -> LocalLaunchEventStore:
        layout = ApplicationStatePathLayout.from_database(database_path)
        return cls(root=layout.local_launches)

    @classmethod
    def configured(cls) -> LocalLaunchEventStore:
        return cls.from_database(default_database_path())

    def create(
        self,
        *,
        identity: LaunchIdentity,
        intent: LaunchIntent,
        asserted_by: str,
        timestamp: datetime,
    ) -> LaunchManifest:
        event = LaunchEvent(
            launch_id=identity.launch_id,
            sequence=1,
            event=LaunchEventKind.LAUNCH_RESOLVED,
            timestamp=timestamp,
            asserted_by=asserted_by,
            owner_generation=identity.owner_generation,
        )
        manifest = project_launch_manifest(identity=identity, intent=intent, events=(event,))
        target = self.path(identity.launch_id)
        ensure_private_directory(target.parent)
        with exact_file_write_lock(target):
            if target.exists():
                raise ValueError("local launch record already exists")
            _publish_locked(target, _record_payload(identity, intent, (event,)))
        return manifest

    def append(
        self,
        launch_id: str,
        *,
        event: LaunchEventKind,
        asserted_by: str,
        timestamp: datetime,
        service: str | None = None,
        service_generation: int | None = None,
        payload: Mapping[str, object] | None = None,
    ) -> LaunchManifest:
        target = self.path(launch_id)
        with exact_file_write_lock(target):
            identity, intent, events = _load_record(target)
            next_event = LaunchEvent(
                launch_id=launch_id,
                sequence=events[-1].sequence + 1,
                event=event,
                timestamp=timestamp,
                asserted_by=asserted_by,
                owner_generation=identity.owner_generation,
                service=service,
                service_generation=service_generation,
                payload={} if payload is None else payload,
            )
            updated = (*events, next_event)
            manifest = project_launch_manifest(identity=identity, intent=intent, events=updated)
            _publish_locked(target, _record_payload(identity, intent, updated))
        return manifest

    def manifest(self, launch_id: str) -> LaunchManifest:
        target = self.path(launch_id)
        with exact_file_write_lock(target):
            identity, intent, events = _load_record(target)
        return project_launch_manifest(identity=identity, intent=intent, events=events)

    def events(self, launch_id: str) -> tuple[LaunchEvent, ...]:
        target = self.path(launch_id)
        with exact_file_write_lock(target):
            _identity, _intent, events = _load_record(target)
        return events

    def path(self, launch_id: str) -> Path:
        if not launch_id:
            raise ValueError("launch_id is required")
        return self.root / f"{storage_component(launch_id)}.json"


def _publish_locked(target: Path, payload: dict[str, object]) -> None:
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    with prepared_text_file(target, content) as temporary:
        replace_prepared_file(temporary, target)


def _record_payload(
    identity: LaunchIdentity,
    intent: LaunchIntent,
    events: tuple[LaunchEvent, ...],
) -> dict[str, object]:
    return {
        "schema": LOCAL_LAUNCH_RECORD_SCHEMA,
        "identity": {
            "launch_id": identity.launch_id,
            "workspace": identity.workspace,
            "oh_goon_version": identity.oh_goon_version,
            "owner_generation": identity.owner_generation,
            "source_identity": identity.source_identity,
            "configuration_identity": identity.configuration_identity,
        },
        "intent": {
            "profile": intent.profile,
            "services": [_service_intent_payload(service) for service in intent.services],
        },
        "events": [event.to_payload() for event in events],
    }


def _service_intent_payload(service: LaunchServiceIntent) -> dict[str, object]:
    endpoint = None
    if service.endpoint is not None:
        endpoint = {
            "service": service.endpoint.service,
            "host": service.endpoint.host,
            "port": service.endpoint.port,
            "exposure": service.endpoint.exposure,
        }
    return {
        "service": service.service,
        "command": list(service.command),
        "required": service.required,
        "endpoint": endpoint,
    }


def _load_record(
    target: Path,
) -> tuple[LaunchIdentity, LaunchIntent, tuple[LaunchEvent, ...]]:
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("local launch record does not exist") from exc
    if not isinstance(payload, dict) or payload.get("schema") != LOCAL_LAUNCH_RECORD_SCHEMA:
        raise ValueError("unsupported local launch record schema")
    identity = _parse_identity(_mapping(payload, "identity"))
    intent = _parse_intent(_mapping(payload, "intent"))
    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError("local launch record requires events")
    events = tuple(_parse_event(item) for item in raw_events)
    project_launch_manifest(identity=identity, intent=intent, events=events)
    return identity, intent, events


def _parse_identity(payload: Mapping[str, object]) -> LaunchIdentity:
    return LaunchIdentity(
        launch_id=_string(payload, "launch_id"),
        workspace=_string(payload, "workspace"),
        oh_goon_version=_string(payload, "oh_goon_version"),
        owner_generation=_integer(payload, "owner_generation"),
        source_identity=_optional_string(payload.get("source_identity")),
        configuration_identity=_optional_string(payload.get("configuration_identity")),
    )


def _parse_intent(payload: Mapping[str, object]) -> LaunchIntent:
    raw_services = payload.get("services")
    if not isinstance(raw_services, list):
        raise ValueError("local launch intent services must be a list")
    return LaunchIntent(
        profile=_string(payload, "profile"),
        services=tuple(_parse_service_intent(item) for item in raw_services),
    )


def _parse_service_intent(value: object) -> LaunchServiceIntent:
    if not isinstance(value, dict):
        raise ValueError("local launch service intent must be an object")
    raw_command = value.get("command")
    if not isinstance(raw_command, list) or not all(isinstance(item, str) for item in raw_command):
        raise ValueError("local launch service command must be a string list")
    endpoint_value = value.get("endpoint")
    endpoint = None
    if endpoint_value is not None:
        if not isinstance(endpoint_value, dict):
            raise ValueError("local launch endpoint intent must be an object")
        endpoint = LaunchEndpointIntent(
            service=_string(endpoint_value, "service"),
            host=_string(endpoint_value, "host"),
            port=_integer(endpoint_value, "port"),
            exposure=_string(endpoint_value, "exposure"),
        )
    required = value.get("required")
    if not isinstance(required, bool):
        raise ValueError("local launch service required must be boolean")
    return LaunchServiceIntent(
        service=_string(value, "service"),
        command=tuple(raw_command),
        required=required,
        endpoint=endpoint,
    )


def _parse_event(value: object) -> LaunchEvent:
    if not isinstance(value, dict):
        raise ValueError("local Launch Event must be an object")
    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("local Launch Event payload must be an object")
    return LaunchEvent(
        launch_id=_string(value, "launch_id"),
        sequence=_integer(value, "sequence"),
        event=LaunchEventKind(_string(value, "event")),
        timestamp=datetime.fromisoformat(_string(value, "timestamp")),
        asserted_by=_string(value, "asserted_by"),
        owner_generation=_integer(value, "owner_generation"),
        service=_optional_string(value.get("service")),
        service_generation=_optional_integer(value.get("service_generation")),
        payload=payload,
        schema=_string(value, "schema"),
        kind=_string(value, "kind"),
    )


def _mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"local launch record {key} must be an object")
    return value


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"local launch record {key} must be a non-empty string")
    return value


def _integer(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"local launch record {key} must be an integer")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("local launch optional string must be non-empty when present")
    return value


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("local launch optional integer must be integer when present")
    return value
