from __future__ import annotations

import json
import sqlite3

from oh_goon.core.errors import (
    ScheduleDefinitionStale,
    ScheduleExecutionActive,
    ScheduleNotFound,
    StateConflict,
)
from oh_goon.core.models.schedule import (
    ScheduleCreate,
    ScheduleDefinition,
    ScheduleDeleteRequest,
    ScheduleReplace,
)
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.models.writable_schedule import WritableSchedulePolicy
from oh_goon.core.product_identity import new_schedule_generation_id
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import ScheduleState
from oh_goon.runtime.branch_advances import BranchAdvanceStore
from oh_goon.runtime.database import LocalDatabase


class ScheduleDefinitionStore:
    """SQLite owner for explicit repository-target-set Schedule definitions."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def definitions(self) -> list[ScheduleDefinition]:
        with self._database.transaction() as connection:
            return self.definitions_in(connection)

    def definitions_in(self, connection: sqlite3.Connection) -> list[ScheduleDefinition]:
        return [
            _definition(row)
            for row in connection.execute(
                "SELECT * FROM schedule_definitions ORDER BY name, schedule_id"
            ).fetchall()
        ]

    def get(self, schedule_id: str) -> ScheduleDefinition:
        with self._database.transaction() as connection:
            return self.get_in(connection, schedule_id)

    def get_in(self, connection: sqlite3.Connection, schedule_id: str) -> ScheduleDefinition:
        row = connection.execute(
            "SELECT * FROM schedule_definitions WHERE schedule_id=?", (schedule_id,)
        ).fetchone()
        if row is None:
            raise ScheduleNotFound(f"Unknown Schedule: {schedule_id}")
        return _definition(row)

    def writable_policy(self, generation_id: str) -> WritableSchedulePolicy | None:
        with self._database.transaction() as connection:
            return self.writable_policy_in(connection, generation_id)

    @staticmethod
    def writable_policy_in(
        connection: sqlite3.Connection, generation_id: str
    ) -> WritableSchedulePolicy | None:
        try:
            row = connection.execute(
                "SELECT seed_advance_id, policy_json FROM schedule_writable_policies "
                "WHERE generation_id=?",
                (generation_id,),
            ).fetchone()
        except sqlite3.OperationalError as exc:
            if str(exc) != "no such table: schedule_writable_policies":
                raise
            return None
        if row is None:
            return None
        policy = WritableSchedulePolicy.model_validate_json(row["policy_json"])
        if policy.seed_advance_id != row["seed_advance_id"]:
            raise StateConflict("Writable Schedule policy differs from its reserved seed")
        return policy

    def seed_reserved(self, advance_id: str) -> bool:
        with self._database.transaction() as connection:
            try:
                row = connection.execute(
                    "SELECT 1 FROM schedule_writable_policies WHERE seed_advance_id=?",
                    (advance_id,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                if str(exc) != "no such table: schedule_writable_policies":
                    raise
                return False
            return row is not None

    def create(
        self,
        schedule_id: str,
        request: ScheduleCreate,
        *,
        writable_policy: WritableSchedulePolicy | None = None,
    ) -> ScheduleDefinition:
        existing = self._find(schedule_id)
        if existing is not None:
            return self._require_matching_create(existing, request, writable_policy)
        now = utc_now()
        definition = ScheduleDefinition(
            schedule_id=schedule_id,
            generation_id=new_schedule_generation_id(),
            record_version=0,
            created_at=now,
            updated_at=now,
            **request.model_dump(),
        )
        try:
            with self._database.transaction(write=True) as connection:
                connection.execute(
                    """INSERT INTO schedule_definitions(
                        schedule_id, generation_id, name, repository_ids_json, repository_instance_ids_json, target_json, interval_minutes, state, record_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _definition_values(definition),
                )
                if writable_policy is not None:
                    _reserve_writable_policy(connection, definition, writable_policy)
            return definition
        except sqlite3.IntegrityError:
            existing = self._find(schedule_id)
            if existing is None:
                raise
            return self._require_matching_create(existing, request, writable_policy)
        except BaseException:
            existing = self._find(schedule_id)
            if existing is not None and self._matches_create(existing, request, writable_policy):
                return existing
            raise

    def _matches_create(
        self,
        existing: ScheduleDefinition,
        request: ScheduleCreate,
        writable_policy: WritableSchedulePolicy | None,
    ) -> bool:
        return (
            _matches_create(existing, request)
            and self.writable_policy(existing.generation_id) == writable_policy
        )

    def _require_matching_create(
        self,
        existing: ScheduleDefinition,
        request: ScheduleCreate,
        writable_policy: WritableSchedulePolicy | None,
    ) -> ScheduleDefinition:
        if not self._matches_create(existing, request, writable_policy):
            raise StateConflict(
                f"Schedule identity {existing.schedule_id} is already bound to another definition"
            )
        return existing

    def replace(self, schedule_id: str, request: ScheduleReplace) -> ScheduleDefinition:
        try:
            with self._database.transaction(write=True) as connection:
                current = self.get_in(connection, schedule_id)
                if request.generation_id != current.generation_id:
                    raise ScheduleDefinitionStale(
                        f"Schedule definition generation changed concurrently: {schedule_id}"
                    )
                if self.writable_policy_in(connection, current.generation_id) is not None and (
                    request.repository_ids != current.repository_ids
                    or request.repository_instance_ids != current.repository_instance_ids
                    or request.target != current.target
                ):
                    raise StateConflict("Writable Schedule can only update name, timing and state")
                if request.record_version == current.record_version - 1 and _matches_replace(
                    current, request
                ):
                    return current
                if request.record_version != current.record_version:
                    raise ScheduleDefinitionStale(
                        f"Schedule definition changed concurrently: {schedule_id}"
                    )
                definition = ScheduleDefinition(
                    schedule_id=schedule_id,
                    generation_id=current.generation_id,
                    record_version=current.record_version + 1,
                    created_at=current.created_at,
                    updated_at=utc_now(),
                    **request.model_dump(exclude={"generation_id", "record_version"}),
                )
                target = definition.target
                result = connection.execute(
                    """UPDATE schedule_definitions SET name=?, repository_ids_json=?, repository_instance_ids_json=?, target_json=?, interval_minutes=?, state=?, record_version=?, updated_at=?
                    WHERE schedule_id=? AND generation_id=? AND record_version=?""",
                    (
                        definition.name,
                        json.dumps(definition.repository_ids),
                        json.dumps(definition.repository_instance_ids, sort_keys=True),
                        target.model_dump_json(exclude_computed_fields=True),
                        definition.interval_minutes,
                        definition.state.value,
                        definition.record_version,
                        definition.updated_at.isoformat(),
                        schedule_id,
                        current.generation_id,
                        current.record_version,
                    ),
                )
                if result.rowcount != 1:
                    raise ScheduleDefinitionStale(
                        f"Schedule definition changed concurrently: {schedule_id}"
                    )
                return definition
        except BaseException:
            existing = self._find(schedule_id)
            if (
                existing is not None
                and existing.generation_id == request.generation_id
                and existing.record_version == request.record_version + 1
                and _matches_replace(existing, request)
            ):
                return existing
            raise

    def delete(self, schedule_id: str, request: ScheduleDeleteRequest) -> None:
        deleted = False
        try:
            with self._database.transaction(write=True) as connection:
                row = connection.execute(
                    "SELECT * FROM schedule_definitions WHERE schedule_id=?", (schedule_id,)
                ).fetchone()
                if row is None:
                    return
                current = _definition(row)
                if (
                    request.generation_id != current.generation_id
                    or request.record_version != current.record_version
                ):
                    raise ScheduleDefinitionStale(
                        f"Schedule definition changed concurrently: {schedule_id}"
                    )
                if (
                    connection.execute(
                        "SELECT 1 FROM schedule_executions WHERE schedule_id=? AND status='RUNNING' LIMIT 1",
                        (schedule_id,),
                    ).fetchone()
                    is not None
                ):
                    raise ScheduleExecutionActive(
                        f"Schedule {schedule_id} cannot be deleted while an execution is active"
                    )
                result = connection.execute(
                    "DELETE FROM schedule_definitions WHERE schedule_id=? AND generation_id=? AND record_version=?",
                    (schedule_id, current.generation_id, current.record_version),
                )
                if result.rowcount != 1:
                    raise ScheduleDefinitionStale(
                        f"Schedule definition changed concurrently: {schedule_id}"
                    )
                deleted = True
        except BaseException:
            if deleted and self._find(schedule_id) is None:
                return
            raise

    def _find(self, schedule_id: str) -> ScheduleDefinition | None:
        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_definitions WHERE schedule_id=?", (schedule_id,)
            ).fetchone()
        return _definition(row) if row is not None else None


def _definition_values(definition: ScheduleDefinition) -> tuple[object, ...]:
    return (
        definition.schedule_id,
        definition.generation_id,
        definition.name,
        json.dumps(definition.repository_ids),
        json.dumps(definition.repository_instance_ids, sort_keys=True),
        definition.target.model_dump_json(exclude_computed_fields=True),
        definition.interval_minutes,
        definition.state.value,
        definition.record_version,
        definition.created_at.isoformat(),
        definition.updated_at.isoformat(),
    )


def _definition(row: sqlite3.Row) -> ScheduleDefinition:
    target = FleetPlanTarget.model_validate_json(row["target_json"])
    return ScheduleDefinition(
        schedule_id=row["schedule_id"],
        generation_id=row["generation_id"],
        record_version=int(row["record_version"]),
        name=row["name"],
        repository_ids=tuple(json.loads(row["repository_ids_json"])),
        repository_instance_ids=dict(json.loads(row["repository_instance_ids_json"])),
        target=target,
        interval_minutes=int(row["interval_minutes"]),
        state=ScheduleState(row["state"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _request_from_definition(definition: ScheduleDefinition) -> ScheduleCreate:
    return ScheduleCreate(
        name=definition.name,
        repository_ids=definition.repository_ids,
        repository_instance_ids=definition.repository_instance_ids,
        target=definition.target,
        interval_minutes=definition.interval_minutes,
        state=definition.state,
    )


def _matches_create(existing: ScheduleDefinition, request: ScheduleCreate) -> bool:
    return _request_from_definition(existing) == request


def _reserve_writable_policy(
    connection: sqlite3.Connection,
    definition: ScheduleDefinition,
    policy: WritableSchedulePolicy,
) -> None:
    receipt = BranchAdvanceStore.find_in(connection, policy.seed_advance_id)
    if (
        receipt is None
        or receipt.published_at is None
        or receipt.next_run is not None
        or definition.repository_ids != (receipt.preparation.repository_id,)
    ):
        raise StateConflict(
            "Writable Schedule requires an unused published branch receipt for its repository"
        )
    prepared = receipt.preparation
    try:
        connection.execute(
            "INSERT INTO schedule_writable_policies "
            "(generation_id, seed_advance_id, checkout_identity, branch_owner_subject_id, branch_ref, policy_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                definition.generation_id,
                policy.seed_advance_id,
                prepared.checkout_identity,
                prepared.branch_owner_subject_id,
                prepared.branch_ref,
                policy.model_dump_json(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise StateConflict("Writable Schedule already owns this seed, branch or checkout") from exc


def _matches_replace(existing: ScheduleDefinition, request: ScheduleReplace) -> bool:
    requested = ScheduleCreate.model_validate(
        request.model_dump(exclude={"generation_id", "record_version"})
    )
    return _request_from_definition(existing) == requested
