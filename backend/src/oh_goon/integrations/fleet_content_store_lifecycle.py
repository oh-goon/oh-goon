from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.path_authority import ensure_private_directory
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.integrations.exact_file_write import prepared_text_file, replace_prepared_file

STAGE_OWNER_SCHEMA = "oh-goon.content-stage-owner/v1"
STAGE_OWNER_SUFFIX = ".owner.json"


@dataclass(frozen=True, slots=True)
class StagingPruneResult:
    removed: tuple[str, ...]
    live: tuple[str, ...]
    refused: tuple[str, ...]


@dataclass(slots=True)
class FleetContentLease:
    """Short-lived read authority that prevents GC while an object is materialized."""

    lease_id: str
    object_id: str
    owner_repository_id: str
    path: Path
    lock_path: Path
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        with store_lock(self.lock_path):
            self.path.unlink(missing_ok=True)
        self._closed = True

    def __enter__(self) -> FleetContentLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def release_reference(
    layout: ContentStorePathLayout,
    lock_path: Path,
    *,
    reference_id: str,
    owner_repository_id: str,
) -> None:
    validate_reference_id(reference_id)
    validate_repository_id(owner_repository_id)
    with store_lock(lock_path):
        path = layout.reference(owner_repository_id, reference_id)
        if not path.is_file():
            raise _unavailable_reference(owner_repository_id, reference_id)
        payload = read_json_record(path)
        if payload is None or payload.get("owner_repository_id") != owner_repository_id:
            raise _unavailable_reference(owner_repository_id, reference_id)
        path.unlink()


def stage_owner_path(stage: Path) -> Path:
    return stage.parent / f"{stage.name}{STAGE_OWNER_SUFFIX}"


def claim_stage_owner(stage: Path) -> None:
    """Bind a disposable stage name to this exact creating process instance.

    The birth record is disposable cleanup metadata, not durable content authority. An
    exclusive/fsynced create is enough: a torn or missing record causes later cleanup
    to refuse the stage rather than guessing orphanhood.
    """

    path = stage_owner_path(stage)
    ensure_private_directory(path.parent)
    content = json.dumps(
        {
            "schema": STAGE_OWNER_SCHEMA,
            "stage_name": stage.name,
            "pid": os.getpid(),
            "process_identity": current_process_identity(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def remove_owned_stage(stage: Path) -> None:
    """Best-effort normal-owner cleanup of one stage and its birth record."""

    remove_path(stage)
    stage_owner_path(stage).unlink(missing_ok=True)


def prune_orphan_staging(layout: ContentStorePathLayout, lock_path: Path) -> StagingPruneResult:
    """Remove only stages whose exact creating process is provably gone.

    Unowned or unreadable staging is deliberately refused: absence of ownership metadata is
    not positive orphan evidence. Live stages are protected even though copying happens
    outside the store-wide lock.
    """

    if sys.platform != "linux":
        raise FleetPlanInvalid(
            "Race-safe Fleet content-stage cleanup currently requires Linux process identity"
        )
    if not layout.staging.exists():
        return StagingPruneResult((), (), ())
    removed: list[str] = []
    live: list[str] = []
    refused: list[str] = []
    with store_lock(lock_path):
        entries = tuple(layout.staging.iterdir())
        owners = {
            path.name[: -len(STAGE_OWNER_SUFFIX)]: path
            for path in entries
            if path.name.endswith(STAGE_OWNER_SUFFIX) and path.is_file()
        }
        stages = {path.name: path for path in entries if not path.name.endswith(STAGE_OWNER_SUFFIX)}
        for stage_name, stage in sorted(stages.items()):
            owner_path = owners.pop(stage_name, None)
            if owner_path is None:
                refused.append(stage_name)
                continue
            payload = read_json_record(owner_path)
            try:
                if (
                    payload is None
                    or payload.get("schema") != STAGE_OWNER_SCHEMA
                    or payload.get("stage_name") != stage_name
                ):
                    raise ValueError("invalid stage owner record")
                pid = int(payload["pid"])
                identity = str(payload["process_identity"])
            except (KeyError, TypeError, ValueError):
                refused.append(stage_name)
                continue
            if process_identity(pid) == identity:
                live.append(stage_name)
                continue
            _remove_gc_stage(stage, stage_name=stage_name)
            owner_path.unlink(missing_ok=True)
            removed.append(stage_name)
        # Owner records whose stage was atomically published before a crash are safe to
        # collect only after their creating process is no longer live.
        for _stage_name, owner_path in sorted(owners.items()):
            payload = read_json_record(owner_path)
            try:
                if payload is None or payload.get("schema") != STAGE_OWNER_SCHEMA:
                    raise ValueError("invalid stage owner record")
                pid = int(payload["pid"])
                identity = str(payload["process_identity"])
            except (KeyError, TypeError, ValueError):
                refused.append(owner_path.name)
                continue
            if process_identity(pid) == identity:
                live.append(owner_path.name)
            else:
                owner_path.unlink(missing_ok=True)
                removed.append(owner_path.name)
    return StagingPruneResult(tuple(sorted(removed)), tuple(sorted(live)), tuple(sorted(refused)))


def _remove_gc_stage(path: Path, *, stage_name: str) -> None:
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet content-stage cleanup failed for {stage_name}: {exc}"
        ) from exc
    if os.path.lexists(path):
        raise FleetPlanInvalid(
            f"Fleet content-stage cleanup could not prove deletion: {stage_name}"
        )


def garbage_collect(root: Path, layout: ContentStorePathLayout, lock_path: Path) -> tuple[str, ...]:
    if sys.platform != "linux":
        raise FleetPlanInvalid(
            "Race-safe Fleet content-store GC currently requires Linux process identity"
        )
    with store_lock(lock_path):
        protected = referenced_objects(root) | reconcile_leases(root)
        removed: list[str] = []
        for kind in ("tree", "file"):
            for path in layout.objects(kind).iterdir():
                object_id = f"{kind}-sha256-{path.name}"
                if path.exists() and object_id not in protected:
                    from oh_goon.integrations.fleet_content_crash_reconciliation import (
                        complete_deletion_locked,
                    )

                    complete_deletion_locked(root, path, object_id)
                    removed.append(object_id)
        return tuple(sorted(removed))


def create_reference_locked(root: Path, object_id: str, *, owner_repository_id: str) -> str:
    """Create one durable object reference while the caller owns the store lock."""
    validate_repository_id(owner_repository_id)
    from oh_goon.integrations.fleet_content_crash_reconciliation import deletion_intent_exists

    if deletion_intent_exists(root, object_id):
        raise FleetPlanInvalid(f"Fleet content object is pending deletion: {object_id}")
    reference_id = f"ref-{uuid.uuid4().hex}"
    publish_json_record_locked(
        ContentStorePathLayout.from_root(root).reference(owner_repository_id, reference_id),
        {
            "reference_id": reference_id,
            "object_id": object_id,
            "owner_repository_id": owner_repository_id,
        },
    )
    return reference_id


def object_id_for_reference(
    layout: ContentStorePathLayout,
    *,
    reference_id: str,
    owner_repository_id: str,
) -> str:
    validate_reference_id(reference_id)
    validate_repository_id(owner_repository_id)
    path = layout.reference(owner_repository_id, reference_id)
    payload = read_json_record(path)
    if (
        payload is None
        or payload.get("reference_id") != reference_id
        or payload.get("owner_repository_id") != owner_repository_id
    ):
        raise _unavailable_reference(owner_repository_id, reference_id)
    object_id = str(payload.get("object_id", ""))
    if not is_object_id(object_id):
        raise FleetPlanInvalid(
            f"Fleet content reference has invalid object identity: {reference_id}"
        )
    return object_id


def referenced_objects(root: Path) -> set[str]:
    referenced: set[str] = set()
    for path in ContentStorePathLayout.from_root(root).references.glob("*/ref-*.json"):
        payload = read_json_record(path)
        if payload is None:
            raise FleetPlanInvalid(
                f"Fleet content-store GC refused because reference metadata is unreadable: {path.name}"
            )
        _validate_reference_record(path, payload)
        referenced.add(str(payload["object_id"]))
    return referenced


def reconcile_leases(root: Path) -> set[str]:
    active: set[str] = set()
    for path in ContentStorePathLayout.from_root(root).leases.glob("*/lease-*.json"):
        payload = read_json_record(path)
        if payload is None:
            raise FleetPlanInvalid(
                f"Fleet content-store GC refused because lease metadata is unreadable: {path.name}"
            )
        object_id, identity, pid = _validated_lease_record(path, payload)
        if process_identity(pid) != identity:
            path.unlink(missing_ok=True)
            continue
        active.add(object_id)
    return active


def new_lease_locked(
    layout: ContentStorePathLayout,
    lock_path: Path,
    *,
    object_id: str,
    owner_repository_id: str,
) -> FleetContentLease:
    """Create one live read lease while the caller owns the store lock."""
    lease_id = f"lease-{uuid.uuid4().hex}"
    path = layout.lease(owner_repository_id, lease_id)
    publish_json_record_locked(
        path,
        {
            "lease_id": lease_id,
            "object_id": object_id,
            "owner_repository_id": owner_repository_id,
            "pid": os.getpid(),
            "process_identity": current_process_identity(),
        },
    )
    return FleetContentLease(lease_id, object_id, owner_repository_id, path, lock_path)


@contextmanager
def store_lock(path: Path) -> Iterator[None]:
    with path.open("a+b") as stream:
        _acquire_lock(stream)
        try:
            yield
        finally:
            _release_lock(stream)


def validate_repository_id(repository_id: str) -> None:
    if (
        not repository_id.startswith("repo-")
        or len(repository_id) != 25
        or any(character not in "0123456789abcdef" for character in repository_id[5:])
    ):
        raise FleetPlanInvalid(f"Invalid Fleet repository owner id: {repository_id}")


def is_object_id(value: str) -> bool:
    parts = value.split("-sha256-", 1)
    return (
        len(parts) == 2
        and parts[0] in {"tree", "file"}
        and len(parts[1]) == 64
        and all(character in "0123456789abcdef" for character in parts[1])
    )


def remove_path(path: Path) -> None:
    """Best-effort cleanup for disposable staging only."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def remove_gc_object_locked(path: Path, *, object_id: str) -> None:
    """Delete one immutable object and prove exact absence before reporting GC success."""
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise FleetPlanInvalid(
            f"Fleet content-store GC failed to remove immutable object: {object_id}: {exc}"
        ) from exc
    if os.path.lexists(path):
        raise FleetPlanInvalid(
            f"Fleet content-store GC could not prove immutable object deletion: {object_id}"
        )


def _validate_reference_record(path: Path, payload: dict[str, object]) -> None:
    reference_id = str(payload.get("reference_id", ""))
    object_id = str(payload.get("object_id", ""))
    owner_repository_id = str(payload.get("owner_repository_id", ""))
    if not is_reference_id(reference_id) or path.name != f"{reference_id}.json":
        raise FleetPlanInvalid(
            f"Fleet content-store GC refused because reference identity is invalid: {path.name}"
        )
    if not is_object_id(object_id):
        raise FleetPlanInvalid(
            f"Fleet content-store GC refused because referenced object identity is invalid: {path.name}"
        )
    _validate_gc_repository_owner(path, owner_repository_id, record_kind="reference")


def _validated_lease_record(path: Path, payload: dict[str, object]) -> tuple[str, str, int]:
    object_id = str(payload.get("object_id", ""))
    owner_repository_id = str(payload.get("owner_repository_id", ""))
    try:
        pid = int(payload["pid"])
        identity = str(payload["process_identity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FleetPlanInvalid(
            f"Fleet content-store GC refused because lease metadata is invalid: {path.name}"
        ) from exc
    if not is_object_id(object_id):
        raise FleetPlanInvalid(
            f"Fleet content-store GC refused because lease object identity is invalid: {path.name}"
        )
    _validate_gc_repository_owner(path, owner_repository_id, record_kind="lease")
    return object_id, identity, pid


def _validate_gc_repository_owner(
    path: Path, owner_repository_id: str, *, record_kind: str
) -> None:
    try:
        validate_repository_id(owner_repository_id)
    except FleetPlanInvalid as exc:
        raise FleetPlanInvalid(
            f"Fleet content-store GC refused because {record_kind} repository ownership is invalid: {path.name}"
        ) from exc


def validate_reference_id(reference_id: str) -> None:
    if not is_reference_id(reference_id):
        raise FleetPlanInvalid(f"Invalid Fleet content reference id: {reference_id}")


def is_reference_id(reference_id: str) -> bool:
    return (
        reference_id.startswith("ref-")
        and len(reference_id) == 36
        and all(character in "0123456789abcdef" for character in reference_id[4:])
    )


def current_process_identity() -> str:
    identity = process_identity(os.getpid())
    return identity if identity is not None else f"pid:{os.getpid()}"


def process_identity(pid: int) -> str | None:
    if sys.platform != "linux":
        return f"pid:{pid}" if pid == os.getpid() else None
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        start_ticks = stat_text.rsplit(")", 1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, IndexError):
        # A same-process lease remains live in restricted runtimes that do not
        # expose procfs. Never extend that fallback to another process: without
        # a start-time identity, PID reuse cannot be distinguished safely.
        return f"pid:{pid}" if pid == os.getpid() else None
    return f"{boot_id}:{start_ticks}"


def publish_json_record_locked(path: Path, payload: dict[str, object]) -> None:
    """Publish one store-lock-protected metadata record with fsynced temp cleanup."""
    ensure_private_directory(path.parent)
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    with prepared_text_file(path, content) as temporary:
        replace_prepared_file(temporary, path)


def read_json_record(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _unavailable_reference(owner_repository_id: str, reference_id: str) -> FleetPlanInvalid:
    return FleetPlanInvalid(
        f"Fleet content reference is unavailable to repository {owner_repository_id}: {reference_id}"
    )


def _acquire_lock(stream: BinaryIO) -> None:
    if sys.platform == "win32":
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        return
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)


def _release_lock(stream: BinaryIO) -> None:
    if sys.platform == "win32":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
