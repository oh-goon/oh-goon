"""Record the native Git effect before allowing the next original Fleet Run."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from typing import Literal

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.branch_advance import (
    BranchAdvanceReceipt,
    NextBranchRunRequest,
    PreparedBranchAdvance,
    branch_round_run_id,
)
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.run_admission_lock import serialize_branch_handoff


def _read(
    connection: sqlite3.Connection,
    *,
    field: Literal["advance_id", "next_fleet_run_id"],
    identity: str,
) -> BranchAdvanceReceipt | None:
    row = connection.execute(
        "SELECT advance_id, branch_owner_subject_id, branch_ref, expected_tip, preparation_json, "
        "published_at, next_fleet_run_id, next_request_sha256, next_repository_subject_id "
        f"FROM branch_advances WHERE {field}=?",
        (identity,),
    ).fetchone()
    if row is None:
        return None
    preparation = PreparedBranchAdvance.model_validate_json(row[4])
    if tuple(row[:4]) != (
        preparation.advance_id,
        preparation.branch_owner_subject_id,
        preparation.branch_ref,
        preparation.expected_tip,
    ):
        raise StateConflict("Retained branch advancement differs from its durable scope")
    return BranchAdvanceReceipt.model_validate(
        {
            "preparation": preparation,
            "published_at": row[5],
            "next_run": (
                {
                    "fleet_run_id": row[6],
                    "request_sha256": row[7],
                    "repository_subject_id": row[8],
                }
                if row[6] is not None
                else None
            ),
        }
    )


def _require_receipt(connection: sqlite3.Connection, advance_id: str) -> BranchAdvanceReceipt:
    receipt = _read(connection, field="advance_id", identity=advance_id)
    if receipt is None:
        raise StateConflict("Branch advancement has no original prepared receipt")
    return receipt


class BranchAdvanceStore:
    def __init__(self, database: LocalDatabase) -> None:
        self._database = database

    def _find(
        self, field: Literal["advance_id", "next_fleet_run_id"], identity: str
    ) -> BranchAdvanceReceipt | None:
        with self._database.transaction() as connection:
            try:
                return _read(connection, field=field, identity=identity)
            except sqlite3.OperationalError as exc:
                # A read-only view of the original epoch may predate this additive table.
                # Writable startup installs it without altering earlier Run authority.
                if str(exc) != "no such table: branch_advances":
                    raise
                return None

    def find(self, advance_id: str) -> BranchAdvanceReceipt | None:
        return self._find("advance_id", advance_id)

    def find_by_next_run(self, fleet_run_id: str) -> BranchAdvanceReceipt | None:
        return self._find("next_fleet_run_id", fleet_run_id)

    def prepare(self, preparation: PreparedBranchAdvance) -> BranchAdvanceReceipt:
        """Bind the fresh attempt to one exact prior branch tip and native proposal."""
        with self._database.transaction(write=True) as connection:
            original = _read(connection, field="advance_id", identity=preparation.advance_id)
            if original is not None:
                if original.preparation != preparation:
                    raise StateConflict("Branch advancement ID already binds a different proposal")
                return original
            competing = connection.execute(
                "SELECT advance_id FROM branch_advances "
                "WHERE branch_owner_subject_id=? AND branch_ref=? AND expected_tip=?",
                (
                    preparation.branch_owner_subject_id,
                    preparation.branch_ref,
                    preparation.expected_tip,
                ),
            ).fetchone()
            if competing is not None:
                raise StateConflict("This exact prior branch tip already has an advancement")
            connection.execute(
                "INSERT INTO branch_advances "
                "(advance_id, branch_owner_subject_id, branch_ref, expected_tip, preparation_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    preparation.advance_id,
                    preparation.branch_owner_subject_id,
                    preparation.branch_ref,
                    preparation.expected_tip,
                    preparation.model_dump_json(),
                ),
            )
            return BranchAdvanceReceipt(preparation=preparation)

    def confirm(
        self, preparation: PreparedBranchAdvance, *, observed_tip: str
    ) -> BranchAdvanceReceipt:
        """Settle the exact native effect only after the caller proves its resulting tip."""
        if observed_tip != preparation.commit:
            raise StateConflict("Observed branch tip differs from its prepared commit")
        with self._database.transaction(write=True) as connection:
            original = _require_receipt(connection, preparation.advance_id)
            if original.preparation != preparation:
                raise StateConflict("Branch publication differs from the original preparation")
            if original.published_at is not None:
                return original
            published_at = utc_now()
            connection.execute(
                "UPDATE branch_advances SET published_at=? WHERE advance_id=?",
                (published_at.isoformat(), preparation.advance_id),
            )
            return BranchAdvanceReceipt(preparation=preparation, published_at=published_at)

    def bind_next_request(
        self, advance_id: str, *, request_sha256: str, fleet_run_id: str
    ) -> BranchAdvanceReceipt:
        """Reserve one authored successor for a confirmed publication before Fleet admission."""
        if fleet_run_id != branch_round_run_id(advance_id):
            raise StateConflict("Successor Run ID differs from this branch advancement")
        next_run = NextBranchRunRequest(fleet_run_id=fleet_run_id, request_sha256=request_sha256)
        with self._database.transaction(write=True) as connection:
            original = _require_receipt(connection, advance_id)
            if original.published_at is None:
                raise StateConflict("Branch advancement is awaiting publication evidence")
            if original.next_run is not None:
                if (
                    original.next_run.fleet_run_id != fleet_run_id
                    or original.next_run.request_sha256 != request_sha256
                ):
                    raise StateConflict("Branch advancement already binds a different successor")
                return original
            bound = BranchAdvanceReceipt(
                preparation=original.preparation,
                published_at=original.published_at,
                next_run=next_run,
            )
            connection.execute(
                "UPDATE branch_advances SET next_fleet_run_id=?, next_request_sha256=? "
                "WHERE advance_id=?",
                (fleet_run_id, request_sha256, advance_id),
            )
            return bound

    def bind_next_source(
        self,
        advance_id: str,
        *,
        repository_subject_id: str,
        fleet_run_id: str,
        request_sha256: str,
    ) -> BranchAdvanceReceipt:
        """Bind the fresh repository subject after exact checkout synchronization."""
        with self._database.transaction(write=True) as connection:
            original = _require_receipt(connection, advance_id)
            next_run = original.next_run
            if (
                next_run is None
                or next_run.fleet_run_id != fleet_run_id
                or next_run.request_sha256 != request_sha256
            ):
                raise StateConflict("Successor source differs from its original reserved request")
            if next_run.repository_subject_id is not None:
                if next_run.repository_subject_id != repository_subject_id:
                    raise StateConflict("Successor already binds a different repository subject")
                return original
            source_bound = BranchAdvanceReceipt(
                preparation=original.preparation,
                published_at=original.published_at,
                next_run=NextBranchRunRequest(
                    fleet_run_id=fleet_run_id,
                    request_sha256=request_sha256,
                    repository_subject_id=repository_subject_id,
                ),
            )
            connection.execute(
                "UPDATE branch_advances SET next_repository_subject_id=? WHERE advance_id=?",
                (repository_subject_id, advance_id),
            )
            return source_bound

    @contextmanager
    def lock_next_request(self, advance_id: str) -> Generator[None, None, None]:
        """Serialize receiving-checkout changes across successor authoring processes."""
        receipt = self.find(advance_id)
        if receipt is None or receipt.published_at is None:
            raise StateConflict("Branch advancement requires a confirmed publication")
        with serialize_branch_handoff(self._database.path, receipt.preparation.checkout_identity):
            yield
