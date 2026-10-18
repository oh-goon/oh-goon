"""B12D2C3B7: stop legacy lease reclamation ahead of original LACE custody.

The generic Fleet startup/cancellation writers are not LACE recovery owners.
Their SQLite BEGIN IMMEDIATE cuts must refuse to delete an orphaned execution
lease or terminalize its Run while a local resource intent is durably present.
This is a *negative* guard, not a cleanup grant or a new authority channel.
"""
from __future__ import annotations

import sqlite3

from oh_goon.core.errors import StateConflict


def require_no_original_lace_intent_before_reclamation_in(
    connection: sqlite3.Connection, run_id: str,
) -> None:
    """Fail before generic dead-lease reclamation, in its original write cut.

    Old Direct/Fleet databases may have neither LACE table. A partially present
    schema, an original signed admission, or orphan local LACE events for this
    Run is unsafe for a generic scheduler to reclaim. Even with *zero* intents,
    the generic owner cannot authenticate absence from the signed journal.
    The caller must roll back on any denial.
    """
    if (not isinstance(connection, sqlite3.Connection)
            or not connection.in_transaction
            or type(run_id) is not str or not run_id):
        raise StateConflict("original SQLite writer and Fleet Run required for LACE reclamation fence")
    try:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('lace_run_authority','lace_run_events')"
        )}
        if not tables:
            return  # pre-LACE database; no hidden bootstrap of a second authority
        if tables != {"lace_run_authority", "lace_run_events"}:
            raise StateConflict("partial original LACE schema blocks legacy lease reclamation")
        authority = connection.execute(
            "SELECT 1 FROM lace_run_authority WHERE run_id=?", (run_id,)
        ).fetchone()
        events = connection.execute(
            "SELECT 1 FROM lace_run_events WHERE run_id=? AND event_kind='RESOURCE_INTENT' LIMIT 1",
            (run_id,),
        ).fetchone()
        if authority is not None:
            if events is not None:
                raise StateConflict(
                    "original local LACE intent requires signed recovery custody before lease reclamation"
                )
            raise StateConflict(
                "original LACE admission requires authenticated recovery before lease reclamation"
            )
        if connection.execute(
            "SELECT 1 FROM lace_run_events WHERE run_id=? LIMIT 1", (run_id,)
        ).fetchone() is not None:
            raise StateConflict("orphan original LACE events block legacy lease reclamation")
    except sqlite3.Error as exc:
        raise StateConflict("original LACE authority unreadable; lease reclamation denied") from exc
