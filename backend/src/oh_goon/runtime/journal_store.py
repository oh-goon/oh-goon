from __future__ import annotations

import sqlite3

from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.reconciliation import (
    RepositoryEffectAcknowledgementEvidence,
    RepositoryEffectReconciliationEvidence,
)
from oh_goon.core.models.run_events import FleetRunEvent
from oh_goon.core.time import utc_now
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal_cache import JournalCacheKey, JournalFileSignature, JournalReadCache
from oh_goon.runtime.journal_codec import GENESIS_HASH, EncodedJournalFact, JournalFactCodec
from oh_goon.runtime.journal_facts import JournalEntry
from oh_goon.runtime.journal_view import JournalReadView


class JournalFactStore:
    """Owns Journal SQLite rows, hash-chain integrity and disposable read-view caching."""

    def __init__(self, database: LocalDatabase) -> None:
        self._database = database
        self._read_cache = JournalReadCache()

    def read_view(self, connection: sqlite3.Connection) -> JournalReadView:
        key = self._cache_key(connection)
        cached = self._read_cache.lookup(key)
        if cached is not None:
            return cached
        view = JournalReadView.from_entries(
            tuple(self.load_entries(connection)), self._database.session_id
        )
        self._read_cache.store(key, view)
        return view

    def current_revision(self, connection: sqlite3.Connection) -> int:
        """Return the latest canonical Journal revision without building a read view."""
        row = connection.execute(
            "SELECT revision FROM journal_entries ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        return int(row["revision"]) if row is not None else 0

    def entries_after(self, revision: int) -> tuple[int, tuple[JournalEntry, ...]]:
        """Return one consistent incremental Journal cut for rebuildable read projections."""
        with self._database.transaction() as connection:
            current = self.current_revision(connection)
            if current <= revision:
                return current, ()
            return current, tuple(self.load_entries_after(connection, revision))

    def load_entries(self, connection: sqlite3.Connection) -> list[JournalEntry]:
        rows = connection.execute(
            """
            SELECT revision, session_id, kind, report_json, event_json, effect_json,
                   committed_at, previous_hash, fact_hash
            FROM journal_entries
            ORDER BY revision
            """
        ).fetchall()
        result: list[JournalEntry] = []
        expected_previous_hash = GENESIS_HASH
        for row in rows:
            fact_hash = str(row["fact_hash"])
            result.append(
                JournalFactCodec.decode(
                    revision=int(row["revision"]),
                    session_id=str(row["session_id"]),
                    kind=str(row["kind"]),
                    report_json=str(row["report_json"]) if row["report_json"] is not None else None,
                    event_json=str(row["event_json"]) if row["event_json"] is not None else None,
                    effect_json=str(row["effect_json"]) if row["effect_json"] is not None else None,
                    committed_at=str(row["committed_at"]),
                    previous_hash=str(row["previous_hash"]),
                    fact_hash=fact_hash,
                    expected_previous_hash=expected_previous_hash,
                )
            )
            expected_previous_hash = fact_hash
        return result

    def load_entries_after(
        self, connection: sqlite3.Connection, revision: int
    ) -> list[JournalEntry]:
        rows = connection.execute(
            """
            SELECT revision, session_id, kind, report_json, event_json, effect_json,
                   committed_at, previous_hash, fact_hash
            FROM journal_entries
            WHERE revision > ?
            ORDER BY revision
            """,
            (revision,),
        ).fetchall()
        if not rows:
            return []
        previous_row = connection.execute(
            "SELECT fact_hash FROM journal_entries WHERE revision <= ? ORDER BY revision DESC LIMIT 1",
            (revision,),
        ).fetchone()
        expected_previous_hash = (
            str(previous_row["fact_hash"]) if previous_row is not None else GENESIS_HASH
        )
        result: list[JournalEntry] = []
        for row in rows:
            fact_hash = str(row["fact_hash"])
            result.append(
                JournalFactCodec.decode(
                    revision=int(row["revision"]),
                    session_id=str(row["session_id"]),
                    kind=str(row["kind"]),
                    report_json=str(row["report_json"]) if row["report_json"] is not None else None,
                    event_json=str(row["event_json"]) if row["event_json"] is not None else None,
                    effect_json=str(row["effect_json"]) if row["effect_json"] is not None else None,
                    committed_at=str(row["committed_at"]),
                    previous_hash=str(row["previous_hash"]),
                    fact_hash=fact_hash,
                    expected_previous_hash=expected_previous_hash,
                )
            )
            expected_previous_hash = fact_hash
        return result

    def append_operation(self, connection: sqlite3.Connection, report: OperationReport) -> None:
        self._append_encoded(connection, JournalFactCodec.encode_operation(report))

    def append_run_event(self, connection: sqlite3.Connection, event: FleetRunEvent) -> None:
        self._append_encoded(connection, JournalFactCodec.encode_run_event(event))

    def append_fleet_operation(
        self,
        connection: sqlite3.Connection,
        report: OperationReport,
        event: FleetRunEvent,
    ) -> None:
        self._append_encoded(connection, JournalFactCodec.encode_fleet_operation(report, event))

    def append_effect_reconciliation(
        self,
        connection: sqlite3.Connection,
        evidence: RepositoryEffectReconciliationEvidence,
    ) -> None:
        self._append_encoded(connection, JournalFactCodec.encode_effect_reconciliation(evidence))

    def append_effect_acknowledgement(
        self,
        connection: sqlite3.Connection,
        evidence: RepositoryEffectAcknowledgementEvidence,
    ) -> None:
        self._append_encoded(connection, JournalFactCodec.encode_effect_acknowledgement(evidence))

    def _append_encoded(self, connection: sqlite3.Connection, encoded: EncodedJournalFact) -> None:
        row = connection.execute(
            "SELECT fact_hash FROM journal_entries ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        previous_hash = str(row["fact_hash"]) if row is not None else GENESIS_HASH
        committed_at = utc_now().isoformat()
        fact_hash = JournalFactCodec.hash_fact(
            previous_hash=previous_hash,
            session_id=self._database.session_id,
            encoded=encoded,
            committed_at=committed_at,
        )
        connection.execute(
            """
            INSERT INTO journal_entries(
                session_id, kind, report_json, event_json, effect_json,
                committed_at, previous_hash, fact_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._database.session_id,
                encoded.kind.value,
                encoded.report_json,
                encoded.event_json,
                encoded.effect_json,
                committed_at,
                previous_hash,
                fact_hash,
            ),
        )
        self._read_cache.invalidate()

    def _cache_key(self, connection: sqlite3.Connection) -> JournalCacheKey:
        row = connection.execute(
            "SELECT revision, fact_hash FROM journal_entries ORDER BY revision DESC LIMIT 1"
        ).fetchone()
        return JournalCacheKey(
            revision=int(row["revision"]) if row is not None else 0,
            fact_hash=str(row["fact_hash"]) if row is not None else "",
            files=JournalFileSignature.capture(self._database.path),
        )
