"""Oh-Goon product identity primitives for repository, execution, schedule, and stable IDs."""

from __future__ import annotations

import hashlib
import json
import math
import stat
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oh_goon.core.path_authority import canonical_path

if TYPE_CHECKING:
    from oh_goon.core.models.target import OperationTarget


def canonical_repository_path(path: Path | str) -> Path:
    return canonical_path(path)


def repository_id(path: Path | str) -> str:
    canonical = str(canonical_repository_path(path))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
    return f"repo-{digest}"


def repository_instance_id(path: Path | str) -> str:
    """Ephemeral authority token for the current filesystem instance of one Git root.

    This is deliberately separate from stable ``repository_id``. It fences read/preview ->
    command races where another repository could replace the same canonical path.
    """
    root = canonical_repository_path(path)
    marker = root / ".git"
    try:
        root_stat = root.lstat()
        marker_stat = marker.lstat()
    except OSError as exc:
        raise ValueError(f"Cannot capture repository instance authority for {root}: {exc}") from exc
    return stable_id(
        "repo-instance",
        {
            "path": str(root),
            "root_dev": root_stat.st_dev,
            "root_ino": root_stat.st_ino,
            "root_mode": root_stat.st_mode,
            "root_ctime_ns": root_stat.st_ctime_ns,
            "git_dev": marker_stat.st_dev,
            "git_ino": marker_stat.st_ino,
            "git_mode": marker_stat.st_mode,
            "git_ctime_ns": marker_stat.st_ctime_ns,
        },
    )


def repository_subject_instance_id(path: Path | str) -> str:
    """Ephemeral identity for the exact filesystem subject admitted for execution.

    Unlike ``repository_instance_id``, this also supports non-Git direct/verification
    repositories. When a Git marker exists it participates in the identity, so replacing
    either the repository root or its Git authority makes later revalidation stale.
    """
    root = canonical_repository_path(path)
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise ValueError(f"Cannot capture repository subject authority for {root}: {exc}") from exc
    marker = root / ".git"
    marker_payload: dict[str, int] | None = None
    try:
        marker_stat = marker.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError(f"Cannot capture repository subject authority for {root}: {exc}") from exc
    else:
        marker_payload = {
            "dev": marker_stat.st_dev,
            "ino": marker_stat.st_ino,
            "mode": marker_stat.st_mode,
        }
        # Git status/index refresh creates and removes index.lock inside .git.
        # This updates directory ctime without replacing the Git authority.
        # Preserve ctime only for a .git *file*, whose own bytes may change.
        if not stat.S_ISDIR(marker_stat.st_mode):
            marker_payload["ctime_ns"] = marker_stat.st_ctime_ns
    return stable_id(
        "repo-subject-instance",
        {
            "path": str(root),
            "root_dev": root_stat.st_dev,
            "root_ino": root_stat.st_ino,
            "root_mode": root_stat.st_mode,
            # Root ctime guards delete/recreate with inode reuse. Normal Git
            # index refresh changes the .git directory, not this root.
            "root_ctime_ns": root_stat.st_ctime_ns,
            "git": marker_payload,
        },
    )


def new_operation_id() -> str:
    return f"op-{uuid.uuid4().hex}"


def new_workspace_id() -> str:
    """Return one opaque durable workspace identity."""

    return f"workspace-{uuid.uuid4().hex}"


def new_checkpoint_id() -> str:
    """Return one opaque durable candidate-content checkpoint identity."""

    return f"checkpoint-{uuid.uuid4().hex}"


def new_assignment_id() -> str:
    """Return one opaque immutable execution-assignment identity."""

    return f"assignment-{uuid.uuid4().hex}"


def new_workspace_lease_id() -> str:
    """Return one opaque durable workspace-possession identity."""

    return f"workspace-lease-{uuid.uuid4().hex}"


def new_execution_materialization_id() -> str:
    """Return one opaque immutable execution-materialization identity."""

    return f"materialization-{uuid.uuid4().hex}"


def new_execution_evidence_acceptance_id() -> str:
    """Return one opaque immutable accepted-evidence receipt identity."""

    return f"evidence-acceptance-{uuid.uuid4().hex}"


def new_physical_execution_id() -> str:
    """Return one opaque identity minted before an owned physical process birth."""
    return f"physical-execution-{uuid.uuid4().hex}"


def opaque_identity_token(identity: str, *, length: int = 8) -> str:
    """Return a short deterministic token without interpreting opaque ID formatting."""
    if not identity:
        raise ValueError("identity must not be empty")
    if length < 4 or length > 64:
        raise ValueError("identity token length must be between 4 and 64")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:length]


def new_fleet_run_id() -> str:
    return f"fleet-{uuid.uuid4().hex}"


def new_schedule_run_id() -> str:
    return f"schedule-run-{uuid.uuid4().hex}"


def new_schedule_generation_id() -> str:
    return f"schedule-gen-{uuid.uuid4().hex}"


def schedule_repository_fleet_run_id(schedule_run_id: str, repository_id: str) -> str:
    """Stable canonical FleetRun identity for one repository in one Schedule occurrence."""
    return stable_id(
        "fleet",
        {"schedule_run_id": schedule_run_id, "repository_id": repository_id},
    )


def repository_attempt_operation_id(fleet_run_id: str, repository_id: str) -> str:
    """Stable operation identity for one captured repository attempt in a FleetRun."""
    return stable_id(
        "op",
        {"fleet_run_id": fleet_run_id, "repository_id": repository_id},
    )


def fleet_step_attempt_id(operation_id: str, step_id: str, attempt_number: int) -> str:
    """Stable identity for one retry attempt inside one repository attempt."""
    if not operation_id:
        raise ValueError("operation_id must not be empty")
    if not step_id:
        raise ValueError("step_id must not be empty")
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    return stable_id(
        "step-attempt",
        {
            "operation_id": operation_id,
            "step_id": step_id,
            "attempt_number": attempt_number,
        },
    )


def validate_repository_attempt_operation_id(
    fleet_run_id: str, repository_id: str, operation_id: str
) -> None:
    """Require the one canonical operation identity for a captured Fleet repository."""
    expected = repository_attempt_operation_id(fleet_run_id, repository_id)
    if operation_id != expected:
        raise ValueError(
            "Fleet repository attempt operation_id must match its canonical "
            f"run/repository identity: expected {expected}, got {operation_id}"
        )


def stable_id(prefix: str, payload: Any) -> str:
    """Hash canonical JSON-like data without coercing identity representation."""
    _require_stable_json_value(payload, set())
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()}"


def _require_stable_json_value(value: Any, ancestors: set[int]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("stable identity numbers must be finite")
        return
    if isinstance(value, (list, tuple, dict)):
        marker = id(value)
        if marker in ancestors:
            raise TypeError("stable identity values must not contain cycles")
        ancestors.add(marker)
        try:
            if isinstance(value, dict):
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise TypeError("stable identity object keys must be strings")
                    _require_stable_json_value(item, ancestors)
            else:
                for item in value:
                    _require_stable_json_value(item, ancestors)
        finally:
            ancestors.remove(marker)
        return
    raise TypeError(f"unsupported stable identity value: {type(value).__name__}")


def operation_intent_key(capability_id: str, target: OperationTarget | None) -> tuple[str, str]:
    """Stable in-memory key for one exact capability/target intent."""
    target_payload = (
        target.model_dump(mode="json", exclude_none=True, exclude={"plan": {"configuration"}})
        if target is not None
        else None
    )
    encoded_target = json.dumps(
        target_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return capability_id, encoded_target
