from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.execution_lock_models import ExecutionLock, LockedExternalArtifact, LockedGoon
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedImport

LOCK_VERSION = 1


def sha256_digest(payload: Any) -> str:
    """One canonical semantic hashing path for execution lock identity."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_execution_lock(
    plan: ResolvedFleetPlan,
    *,
    external_artifacts: Iterable[tuple[str, bytes]] = (),
) -> ExecutionLock:
    """Project one resolved Plan into its compact canonical execution lock."""
    top_level = tuple(item for item in plan.imports if item.parent_import_id is None)
    goons = tuple(LockedGoon(goon=item.name, digest=_goon_digest(plan, item)) for item in top_level)
    external = tuple(
        LockedExternalArtifact(path=_normalize_relative_path(path), digest=_bytes_digest(payload))
        for path, payload in sorted(
            external_artifacts, key=lambda item: _normalize_relative_path(item[0])
        )
    )
    plan_digest = _plan_digest(plan, goons)
    overall = sha256_digest(
        {
            "lock_version": LOCK_VERSION,
            "plan": plan_digest,
            "execution": [item.model_dump(mode="json", exclude_computed_fields=True) for item in goons],
            "external": [item.model_dump(mode="json", exclude_computed_fields=True) for item in external],
        }
    )
    return ExecutionLock(
        lock_version=LOCK_VERSION,
        plan=plan_digest,
        execution=goons,
        external=external,
        digest=overall,
    )


def verify_execution_lock(expected: ExecutionLock, actual: ExecutionLock) -> None:
    """Reject any lock whose structure or final digest differs from resolved semantics."""
    if actual.digest != _recompute_overall(actual):
        raise FleetPlanInvalid(
            "goon.lock final digest is invalid or was tampered with; regenerate goon.lock"
        )
    if actual.plan != expected.plan:
        raise FleetPlanInvalid("goon.lock Plan/root digest is stale; regenerate goon.lock")
    if actual.execution != expected.execution:
        raise FleetPlanInvalid("goon.lock ordered Goon identity is stale; regenerate goon.lock")
    if actual.external != expected.external:
        raise FleetPlanInvalid(
            "goon.lock external artifact identity is stale; regenerate goon.lock"
        )
    if actual.digest != expected.digest:
        raise FleetPlanInvalid("goon.lock execution digest is stale; regenerate goon.lock")


def _plan_digest(
    plan: ResolvedFleetPlan,
    goons: tuple[LockedGoon, ...],
) -> str:
    top_step_ids = {
        step_id
        for item in plan.imports
        if item.parent_import_id is None
        for step_id in item.step_ids
    }
    root_steps = [
        step.model_dump(mode="json", exclude_computed_fields=True) for step in plan.steps if step.step_id not in top_step_ids
    ]
    payload = {
        "contract_version": plan.contract_version,
        "execution": plan.execution.model_dump(mode="json", exclude_computed_fields=True),
        "root_steps": root_steps,
        "root_requirements": _root_requirements(plan),
        "ordered_goons": [item.goon for item in goons],
    }
    return sha256_digest(payload)


def _root_requirements(plan: ResolvedFleetPlan) -> list[dict[str, Any]]:
    imported = {
        json.dumps(req.model_dump(mode="json", exclude_computed_fields=True), sort_keys=True, separators=(",", ":"))
        for item in plan.imports
        for req in item.requirements
    }
    return [
        req.model_dump(mode="json", exclude_computed_fields=True)
        for req in plan.requirements
        if json.dumps(req.model_dump(mode="json", exclude_computed_fields=True), sort_keys=True, separators=(",", ":"))
        not in imported
    ]


def _goon_digest(plan: ResolvedFleetPlan, root: ResolvedImport) -> str:
    subtree = _import_subtree(plan.imports, root.import_id)
    step_ids = set(root.step_ids)
    steps = [step.model_dump(mode="json", exclude_computed_fields=True) for step in plan.steps if step.step_id in step_ids]
    payload = {
        "goon": root.name,
        "requirements": [item.model_dump(mode="json", exclude_computed_fields=True) for item in root.requirements],
        "composition": [
            {
                "import_id": item.import_id,
                "parent_import_id": item.parent_import_id,
                "name": item.name,
                "requirements": [req.model_dump(mode="json", exclude_computed_fields=True) for req in item.requirements],
            }
            for item in subtree
        ],
        "steps": steps,
    }
    return sha256_digest(payload)


def _import_subtree(
    imports: tuple[ResolvedImport, ...], root_id: str
) -> tuple[ResolvedImport, ...]:
    descendants = {root_id}
    selected: list[ResolvedImport] = []
    for item in imports:
        if item.import_id == root_id or item.parent_import_id in descendants:
            descendants.add(item.import_id)
            selected.append(item)
    return tuple(selected)


def _recompute_overall(lock: ExecutionLock) -> str:
    return sha256_digest(
        {
            "lock_version": lock.lock_version,
            "plan": lock.plan,
            "execution": [item.model_dump(mode="json", exclude_computed_fields=True) for item in lock.execution],
            "external": [item.model_dump(mode="json", exclude_computed_fields=True) for item in lock.external],
        }
    )


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _normalize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    candidate = Path(normalized)
    if candidate.is_absolute() or ".." in candidate.parts or normalized in {"", "."}:
        raise FleetPlanInvalid(f"External artifact path must be repository-relative: {path!r}")
    return candidate.as_posix()
