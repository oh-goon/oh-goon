from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from oh_goon.runtime.journal_facts import JournalEntry
from oh_goon.runtime.journal_view import JournalReadView


@dataclass(frozen=True, slots=True)
class JournalFileSignature:
    database_mtime_ns: int
    database_size: int
    wal_mtime_ns: int
    wal_size: int

    @classmethod
    def capture(cls, database_path: Path) -> JournalFileSignature:
        database_stat = database_path.stat()
        wal_path = Path(f"{database_path}-wal")
        try:
            wal_stat = wal_path.stat()
            wal_size = wal_stat.st_size
            wal_mtime_ns = wal_stat.st_mtime_ns if wal_size else 0
        except FileNotFoundError:
            wal_mtime_ns = 0
            wal_size = 0
        return cls(
            database_mtime_ns=database_stat.st_mtime_ns,
            database_size=database_stat.st_size,
            wal_mtime_ns=wal_mtime_ns,
            wal_size=wal_size,
        )


@dataclass(frozen=True, slots=True)
class JournalCacheKey:
    revision: int
    fact_hash: str
    files: JournalFileSignature


@dataclass(frozen=True, slots=True)
class _JournalCacheEntry:
    key: JournalCacheKey
    view: JournalReadView
    facts: Mapping[tuple[int, str], JournalEntry]


class JournalReadCache:
    """One-process cache for already integrity-verified immutable Journal read cuts.

    Key and view are published as one immutable snapshot.  Concurrent readers must
    never validate one cache key and then observe a view installed for another
    Journal revision. The same snapshot retains that cut's decoded facts; reuse
    in a new cut follows full current-row hash and predecessor verification.
    """

    def __init__(self) -> None:
        self._entry: _JournalCacheEntry | None = None

    def lookup(self, key: JournalCacheKey) -> JournalReadView | None:
        entry = self._entry
        if entry is not None and key == entry.key:
            return entry.view
        return None

    def latest(self) -> _JournalCacheEntry | None:
        """Retain one immutable cut for reuse after the next full integrity check."""
        return self._entry

    def store(
        self,
        key: JournalCacheKey,
        view: JournalReadView,
        facts: Mapping[tuple[int, str], JournalEntry] | None = None,
    ) -> None:
        self._entry = _JournalCacheEntry(
            key=key, view=view, facts=MappingProxyType(dict(facts) if facts is not None else {})
        )

    def invalidate(self) -> None:
        self._entry = None
