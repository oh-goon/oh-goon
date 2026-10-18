from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.integrations.exact_file_write import remove_file_durably
from oh_goon.integrations.fleet_content_object_lifecycle import (
    lifecycle_path,
    read_object_lifecycle,
)
from oh_goon.integrations.fleet_content_store_lifecycle import (
    is_object_id,
    publish_json_record_locked,
    read_json_record,
    reconcile_leases,
    referenced_objects,
    remove_gc_object_locked,
    store_lock,
)

DELETION_INTENT_SCHEMA = "oh-goon.fleet-content-deletion-intent/v1"


@dataclass(frozen=True, slots=True)
class FleetContentCrashReconciliationResult:
    completed_deletions: tuple[str, ...]
    removed_orphan_lifecycle: tuple[str, ...]
    removed_temporary_records: tuple[str, ...]


def deletion_intent_path(root: Path, object_id: str) -> Path:
    if not is_object_id(object_id):
        raise FleetPlanInvalid(f"Invalid Fleet content deletion intent identity: {object_id}")
    kind, digest = object_id.split("-sha256-", 1)
    return root / "deletion-intents" / kind / f"{digest}.json"


def publish_deletion_intent_locked(root: Path, object_id: str) -> None:
    publish_json_record_locked(
        deletion_intent_path(root, object_id),
        {"schema": DELETION_INTENT_SCHEMA, "object_id": object_id},
    )


def deletion_intent_exists(root: Path, object_id: str) -> bool:
    path = deletion_intent_path(root, object_id)
    if not path.exists():
        return False
    payload = read_json_record(path)
    if payload is None or payload.get("schema") != DELETION_INTENT_SCHEMA or payload.get("object_id") != object_id:
        raise FleetPlanInvalid(f"Fleet content deletion intent is invalid: {object_id}")
    return True


def complete_deletion_locked(root: Path, path: Path, object_id: str) -> None:
    publish_deletion_intent_locked(root, object_id)
    remove_gc_object_locked(path, object_id=object_id)
    remove_file_durably(lifecycle_path(root, object_id))
    remove_file_durably(deletion_intent_path(root, object_id))


def reconcile_interrupted_maintenance(
    root: Path, layout: ContentStorePathLayout, lock_path: Path
) -> FleetContentCrashReconciliationResult:
    """Recover only explicitly interrupted deletion cuts and harmless metadata debris.

    A deletion intent is durable positive evidence that maintenance selected the object
    while it was unreferenced/unleased. New references are refused while the intent
    exists. Recovery still recomputes live protection and fails closed if an impossible
    protected-intent state is observed.
    """

    with store_lock(lock_path):
        protected = referenced_objects(root) | reconcile_leases(root)
        completed: list[str] = []
        for object_id, path in _deletion_intents(root, layout):
            if object_id in protected:
                raise FleetPlanInvalid(
                    f"Fleet content interrupted deletion became protected: {object_id}"
                )
            if os.path.lexists(path):
                remove_gc_object_locked(path, object_id=object_id)
            remove_file_durably(lifecycle_path(root, object_id))
            remove_file_durably(deletion_intent_path(root, object_id))
            completed.append(object_id)

        orphan_lifecycle: list[str] = []
        for object_id in _lifecycle_ids(root):
            if not os.path.lexists(_object_path(layout, object_id)):
                remove_file_durably(lifecycle_path(root, object_id))
                orphan_lifecycle.append(object_id)

        temporary = _remove_abandoned_metadata_temps(root)
        return FleetContentCrashReconciliationResult(
            completed_deletions=tuple(sorted(completed)),
            removed_orphan_lifecycle=tuple(sorted(orphan_lifecycle)),
            removed_temporary_records=tuple(sorted(temporary)),
        )


def _deletion_intents(
    root: Path, layout: ContentStorePathLayout
) -> tuple[tuple[str, Path], ...]:
    found: list[tuple[str, Path]] = []
    for kind in ("tree", "file"):
        directory = root / "deletion-intents" / kind
        if not directory.exists():
            continue
        for record in sorted(directory.glob("*.json"), key=lambda item: item.name):
            digest = record.stem
            object_id = f"{kind}-sha256-{digest}"
            if not deletion_intent_exists(root, object_id):
                continue
            found.append((object_id, _object_path(layout, object_id)))
    return tuple(found)


def _lifecycle_ids(root: Path) -> tuple[str, ...]:
    found: list[str] = []
    for kind in ("tree", "file"):
        directory = root / "object-lifecycle" / kind
        if not directory.exists():
            continue
        for record in sorted(directory.glob("*.json"), key=lambda item: item.name):
            object_id = f"{kind}-sha256-{record.stem}"
            lifecycle = read_object_lifecycle(root, object_id)
            if lifecycle is None or lifecycle.object_id != object_id:
                raise FleetPlanInvalid(f"Fleet content lifecycle metadata is invalid: {record.name}")
            found.append(object_id)
    return tuple(found)


def _remove_abandoned_metadata_temps(root: Path) -> list[str]:
    removed: list[str] = []
    for base in (root / "object-lifecycle", root / "deletion-intents"):
        if not base.exists():
            continue
        for path in sorted(base.rglob(".*.tmp")):
            if path.is_symlink() or not path.is_file():
                raise FleetPlanInvalid(f"Fleet content maintenance temp entry is invalid: {path.name}")
            remove_file_durably(path)
            removed.append(str(path.relative_to(root)))
    return removed


def _object_path(layout: ContentStorePathLayout, object_id: str) -> Path:
    kind, digest = object_id.split("-sha256-", 1)
    return layout.objects(kind) / digest
