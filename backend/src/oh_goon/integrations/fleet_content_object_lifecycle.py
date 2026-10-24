from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_content_store_lifecycle import (
    is_object_id,
    publish_json_record_locked,
    read_json_record,
)

OBJECT_LIFECYCLE_SCHEMA = "oh-goon.fleet-content-object-lifecycle/v1"
OBJECT_READY = "READY"


@dataclass(frozen=True, slots=True)
class FleetContentObjectLifecycle:
    object_id: str
    state: str
    byte_count: int
    created_at_ns: int
    last_used_at_ns: int


def lifecycle_path(root: Path, object_id: str) -> Path:
    if not is_object_id(object_id):
        raise FleetPlanInvalid(f"Invalid Fleet content object lifecycle identity: {object_id}")
    kind, digest = object_id.split("-sha256-", 1)
    return root / "object-lifecycle" / kind / f"{digest}.json"


def record_object_ready_locked(
    root: Path,
    object_id: str,
    *,
    byte_count: int,
    now_ns: int | None = None,
) -> FleetContentObjectLifecycle:
    """Create or refresh non-authoritative lifecycle evidence for proven content bytes.

    This record never proves object identity or grants read authority. The content store
    must already have strongly proven/published the immutable object before calling it.
    """

    if byte_count < 0:
        raise FleetPlanInvalid("Fleet content lifecycle byte count must be non-negative")
    path = lifecycle_path(root, object_id)
    observed = read_json_record(path) if path.exists() else None
    timestamp = time.time_ns() if now_ns is None else now_ns
    if observed is None:
        created_at_ns = timestamp
    else:
        current = _parse_lifecycle(path, observed)
        if current.object_id != object_id or current.byte_count != byte_count:
            raise FleetPlanInvalid(
                f"Fleet content lifecycle metadata disagrees with proven object: {object_id}"
            )
        created_at_ns = current.created_at_ns
        timestamp = max(timestamp, current.last_used_at_ns)
    payload = {
        "schema": OBJECT_LIFECYCLE_SCHEMA,
        "object_id": object_id,
        "state": OBJECT_READY,
        "byte_count": byte_count,
        "created_at_ns": created_at_ns,
        "last_used_at_ns": timestamp,
    }
    publish_json_record_locked(path, payload)
    return _parse_lifecycle(path, payload)


def touch_object_used_locked(
    root: Path,
    object_id: str,
    *,
    byte_count: int,
    now_ns: int | None = None,
) -> FleetContentObjectLifecycle:
    return record_object_ready_locked(
        root,
        object_id,
        byte_count=byte_count,
        now_ns=now_ns,
    )


def remove_object_lifecycle_locked(root: Path, object_id: str) -> None:
    lifecycle_path(root, object_id).unlink(missing_ok=True)


def read_object_lifecycle(root: Path, object_id: str) -> FleetContentObjectLifecycle | None:
    path = lifecycle_path(root, object_id)
    payload = read_json_record(path)
    return None if payload is None else _parse_lifecycle(path, payload)


def _parse_lifecycle(path: Path, payload: dict[str, object]) -> FleetContentObjectLifecycle:
    try:
        schema = str(payload["schema"])
        object_id = str(payload["object_id"])
        state = str(payload["state"])
        byte_count = int(cast(int | str | float, payload["byte_count"]))
        created_at_ns = int(cast(int | str | float, payload["created_at_ns"]))
        last_used_at_ns = int(cast(int | str | float, payload["last_used_at_ns"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise FleetPlanInvalid(f"Fleet content lifecycle metadata is invalid: {path.name}") from exc
    if schema != OBJECT_LIFECYCLE_SCHEMA or not is_object_id(object_id):
        raise FleetPlanInvalid(f"Fleet content lifecycle metadata is invalid: {path.name}")
    if (
        state != OBJECT_READY
        or byte_count < 0
        or created_at_ns < 0
        or last_used_at_ns < created_at_ns
    ):
        raise FleetPlanInvalid(f"Fleet content lifecycle metadata is invalid: {path.name}")
    return FleetContentObjectLifecycle(
        object_id=object_id,
        state=state,
        byte_count=byte_count,
        created_at_ns=created_at_ns,
        last_used_at_ns=last_used_at_ns,
    )
