"""Trusted, transaction-bound pre-commit Run candidate for native issuer migration.

The original operational Journal validates and re-constructs the event on every
read. No caller-selected event, synthetic committed Run, or fallback read exists.
This does not itself authorize admission or physical execution.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from oh_goon.core.enforcer_lace_trust import TrustProtocolError
from oh_goon.core.models.run_admission import AllRepositoryScope, ExplicitRepositoryScope, FleetRepositoryScope, FleetRunAdmissionIntent
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_events import RunAdmittedEvent
from oh_goon.runtime.journal import Journal


def _stable(event: RunAdmittedEvent) -> dict:
    return event.model_dump(mode="python", exclude={"generated_at"}, exclude_computed_fields=True)




def _require_original_database(journal: Journal, connection: sqlite3.Connection) -> None:
    """Prove the handle belongs to the original Journal database, never an alias."""
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
        main = [row[2] for row in rows if row[1] == "main"]
        if (len(main) != 1 or not main[0] or
                not os.path.samefile(main[0], journal._database.path)):
            raise TrustProtocolError("pre-work connection is not the original operational database")
        journal._database._require_path_identity()
        journal._database._prove_admitted_sqlite_identity(
            connection, context="pre-work native issuer")
    except TrustProtocolError:
        raise
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise TrustProtocolError("original pre-work SQLite database authority unavailable") from exc

@dataclass(frozen=True, slots=True)
class PreworkRunCapture:
    """No persistence: owned Journal candidate revalidated in one live transaction."""
    journal: Journal
    connection: sqlite3.Connection
    run_id: str
    intent: FleetRunAdmissionIntent
    scope: FleetRepositoryScope
    targets: tuple[RepositoryIdentity, ...]
    admitted: RunAdmittedEvent
    revision: int
    total_changes: int

    @classmethod
    def capture(cls, *, journal: Journal, connection: sqlite3.Connection,
                run_id: str, intent: FleetRunAdmissionIntent,
                repository_scope: FleetRepositoryScope,
                targets: list[RepositoryIdentity]) -> "PreworkRunCapture":
        if (not isinstance(journal, Journal) or not isinstance(connection, sqlite3.Connection)
                or not connection.in_transaction):
            raise TrustProtocolError("original Journal and active pre-work SQLite transaction required")
        if not isinstance(intent, FleetRunAdmissionIntent) or not isinstance(repository_scope, (AllRepositoryScope, ExplicitRepositoryScope)):
            raise TrustProtocolError("canonical pre-work Fleet intent and repository scope required")
        _require_original_database(journal, connection)
        captured_intent = intent.model_copy(deep=True)
        captured_scope = repository_scope.model_copy(deep=True)
        captured_targets = tuple(t.model_copy(deep=True) for t in targets)
        try:
            from oh_goon.services.fleet_admission import FleetAdmissionPlanner
            candidate = FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=journal, connection=connection, run_id=run_id,
                intent=captured_intent, repository_scope=captured_scope,
                targets=list(captured_targets))
            view = journal.read_view_from_connection(connection)
            revision = view.revision
            if view.run_detail(run_id) is not None:
                raise TrustProtocolError("pre-work Run already has an operational fact")
        except TrustProtocolError:
            # Preserve the native producer's fail-before-capture cause; this is
            # not missing infrastructure and must not look retryable.
            raise
        except (ValueError, TypeError, sqlite3.Error) as exc:
            raise TrustProtocolError("original pre-work Fleet admission is unavailable") from exc
        return cls(journal, connection, run_id, captured_intent, captured_scope,
                   captured_targets, candidate, revision, connection.total_changes)

    def read(self, *, journal: Journal, run_id: str) -> tuple[RunAdmittedEvent, int]:
        try:
            active = self.connection.in_transaction
            unchanged = self.connection.total_changes == self.total_changes
        except sqlite3.Error as exc:
            raise TrustProtocolError("pre-work Run transaction is no longer available") from exc
        if (type(self) is not PreworkRunCapture or journal is not self.journal
                or run_id != self.run_id or not active or not unchanged):
            raise TrustProtocolError("pre-work Run transaction or owner is no longer current")
        _require_original_database(self.journal, self.connection)
        try:
            from oh_goon.services.fleet_admission import FleetAdmissionPlanner
            candidate = FleetAdmissionPlanner.prepare_fleet_prework_capture_in(
                journal=self.journal, connection=self.connection, run_id=self.run_id,
                intent=self.intent, repository_scope=self.scope, targets=list(self.targets))
            view = self.journal.read_view_from_connection(self.connection)
            if (view.revision != self.revision or view.run_detail(self.run_id) is not None
                    or self.connection.total_changes != self.total_changes
                    or _stable(candidate) != _stable(self.admitted)):
                raise TrustProtocolError("original pre-work Run capture changed before issuance")
        except TrustProtocolError:
            raise
        except (ValueError, TypeError, sqlite3.Error) as exc:
            raise TrustProtocolError("pre-work Run authority became unavailable") from exc
        return self.admitted, self.revision
