from __future__ import annotations

import sqlite3
from collections.abc import Collection
from pathlib import Path

from pydantic import TypeAdapter

from oh_goon.core.errors import OperationNotFound, StateConflict
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.effect_attempt import RepositoryEffectAttempt
from oh_goon.core.models.effect_origin import DirectEffectOrigin, FleetEffectOrigin
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.models.reconciliation import RepositoryReconciliation
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import EffectAttemptOwner, EffectAttemptStatus, EffectBoundaryKind

_ATTEMPT_ADAPTER: TypeAdapter[RepositoryEffectAttempt] = TypeAdapter(RepositoryEffectAttempt)
_CAPABILITY_ADAPTER = TypeAdapter(CapabilitySnapshot)
_TARGET_ADAPTER: TypeAdapter[OperationTarget] = TypeAdapter(OperationTarget)
_FINGERPRINT_ADAPTER = TypeAdapter(RepositoryStateFingerprint)
_RECONCILIATION_ADAPTER: TypeAdapter[RepositoryReconciliation] = TypeAdapter(
    RepositoryReconciliation
)


def all_in(connection: sqlite3.Connection) -> list[RepositoryEffectAttempt]:
    rows = connection.execute(
        "SELECT * FROM repository_effect_attempts ORDER BY started_at DESC"
    ).fetchall()
    return [_attempt(row) for row in rows]


def insert(connection: sqlite3.Connection, attempt: RepositoryEffectAttempt) -> None:
    connection.execute(
        """
        INSERT INTO repository_effect_attempts(
            operation_id, repository_id, repository_path, capability_json, target_json,
            owner_kind, fleet_run_id, effect_boundary, execution_input_id, status, baseline_json, reconciliation_json,
            record_version, started_at, updated_at, note
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _storage_payload(attempt),
    )


def by_statuses_in(
    connection: sqlite3.Connection,
    statuses: Collection[EffectAttemptStatus],
) -> list[RepositoryEffectAttempt]:
    placeholders = ",".join("?" for _ in statuses)
    rows = connection.execute(
        f"SELECT * FROM repository_effect_attempts "
        f"WHERE status IN ({placeholders}) ORDER BY started_at",
        tuple(item.value for item in statuses),
    ).fetchall()
    return [_attempt(row) for row in rows]


def by_fleet_run_in(
    connection: sqlite3.Connection,
    fleet_run_id: str,
) -> list[RepositoryEffectAttempt]:
    rows = connection.execute(
        "SELECT * FROM repository_effect_attempts "
        "WHERE owner_kind = 'FLEET' AND fleet_run_id = ? ORDER BY started_at",
        (fleet_run_id,),
    ).fetchall()
    return [_attempt(row) for row in rows]


def current(
    connection: sqlite3.Connection,
    operation_id: str,
) -> RepositoryEffectAttempt:
    row = connection.execute(
        "SELECT * FROM repository_effect_attempts WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    if row is None:
        raise OperationNotFound(f"Unknown repository effect attempt: {operation_id}")
    return _attempt(row)


def compare_and_set(
    connection: sqlite3.Connection,
    current_attempt: RepositoryEffectAttempt,
    next_attempt: RepositoryEffectAttempt,
) -> RepositoryEffectAttempt:
    _require_transition_lineage(current_attempt, next_attempt)
    if next_attempt.record_version != current_attempt.record_version:
        raise ValueError("Domain transition must preserve record_version until persistence")
    persisted_payload = next_attempt.model_dump(mode="python", exclude_computed_fields=True)
    persisted_payload["record_version"] = current_attempt.record_version + 1
    persisted = _ATTEMPT_ADAPTER.validate_python(persisted_payload)
    payload = _storage_payload(persisted)
    cursor = connection.execute(
        """
        UPDATE repository_effect_attempts
        SET repository_id=?, repository_path=?, capability_json=?, target_json=?,
            owner_kind=?, fleet_run_id=?, effect_boundary=?, execution_input_id=?, status=?, baseline_json=?, reconciliation_json=?,
            record_version=?, started_at=?, updated_at=?, note=?
        WHERE operation_id=? AND record_version=?
        """,
        (*payload[1:], current_attempt.operation_id, current_attempt.record_version),
    )
    if cursor.rowcount != 1:
        raise StateConflict(
            f"Effect attempt transition lost its compare-and-set: {current_attempt.operation_id}"
        )
    return persisted


def delete_current(
    connection: sqlite3.Connection,
    current_attempt: RepositoryEffectAttempt,
) -> None:
    """CAS-delete exactly the already-validated durable attempt revision."""
    cursor = connection.execute(
        "DELETE FROM repository_effect_attempts WHERE operation_id=? AND record_version=?",
        (current_attempt.operation_id, current_attempt.record_version),
    )
    if cursor.rowcount != 1:
        raise StateConflict(
            f"Effect attempt delete lost its compare-and-set: {current_attempt.operation_id}"
        )


def _require_transition_lineage(
    current_attempt: RepositoryEffectAttempt,
    next_attempt: RepositoryEffectAttempt,
) -> None:
    if next_attempt.operation_id != current_attempt.operation_id:
        raise ValueError("Effect attempt transition cannot change operation identity")
    if next_attempt.repository != current_attempt.repository:
        raise ValueError("Effect attempt transition cannot change repository identity")
    if next_attempt.capability != current_attempt.capability:
        raise ValueError("Effect attempt transition cannot change capability authority")
    if next_attempt.target != current_attempt.target:
        raise ValueError("Effect attempt transition cannot change operation target")
    if next_attempt.origin != current_attempt.origin:
        raise ValueError("Effect attempt transition cannot change execution origin")
    if next_attempt.baseline != current_attempt.baseline:
        raise ValueError("Effect attempt transition cannot change repository baseline")
    if next_attempt.effect_boundary != current_attempt.effect_boundary:
        raise ValueError("Effect attempt transition cannot change effect boundary")
    if next_attempt.started_at != current_attempt.started_at:
        raise ValueError("Effect attempt transition cannot change start time")
    if (
        current_attempt.execution_input_id is not None
        and next_attempt.execution_input_id != current_attempt.execution_input_id
    ):
        raise ValueError("Effect attempt transition cannot change bound execution input identity")


def _storage_payload(attempt: RepositoryEffectAttempt) -> tuple[object, ...]:
    reconciliation = getattr(attempt, "reconciliation", None)
    note = getattr(attempt, "note", None)
    return (
        attempt.operation_id,
        attempt.repository.repository_id,
        str(attempt.repository.path),
        attempt.capability.model_dump_json(exclude_computed_fields=True),
        attempt.target.model_dump_json(exclude_computed_fields=True)
        if attempt.target is not None
        else None,
        attempt.source.value,
        attempt.fleet_run_id,
        attempt.effect_boundary.value,
        attempt.execution_input_id,
        attempt.status.value,
        attempt.baseline.model_dump_json(exclude_computed_fields=True),
        reconciliation.model_dump_json(exclude_computed_fields=True)
        if reconciliation is not None
        else None,
        attempt.record_version,
        attempt.started_at.isoformat(),
        attempt.updated_at.isoformat(),
        note,
    )


def _attempt(row: sqlite3.Row) -> RepositoryEffectAttempt:
    owner = EffectAttemptOwner(row["owner_kind"])
    origin: DirectEffectOrigin | FleetEffectOrigin
    if owner is EffectAttemptOwner.DIRECT:
        origin = DirectEffectOrigin(kind=EffectAttemptOwner.DIRECT)
    else:
        origin = FleetEffectOrigin(
            kind=EffectAttemptOwner.FLEET,
            fleet_run_id=str(row["fleet_run_id"]),
        )
    payload: dict[str, object] = {
        "operation_id": row["operation_id"],
        "repository": {
            "repository_id": row["repository_id"],
            "path": Path(row["repository_path"]),
        },
        "capability": _CAPABILITY_ADAPTER.validate_json(row["capability_json"]),
        "target": _TARGET_ADAPTER.validate_json(row["target_json"]) if row["target_json"] else None,
        "origin": origin,
        "effect_boundary": EffectBoundaryKind(row["effect_boundary"]),
        "execution_input_id": row["execution_input_id"],
        "status": EffectAttemptStatus(row["status"]),
        "baseline": _FINGERPRINT_ADAPTER.validate_json(row["baseline_json"]),
        "record_version": int(row["record_version"]),
        "started_at": row["started_at"],
        "updated_at": row["updated_at"],
    }
    if row["reconciliation_json"]:
        payload["reconciliation"] = _RECONCILIATION_ADAPTER.validate_json(
            row["reconciliation_json"]
        )
    if row["note"]:
        payload["note"] = row["note"]
    return _ATTEMPT_ADAPTER.validate_python(payload)
