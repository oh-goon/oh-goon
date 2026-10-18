"""Cross-layer vocabulary for the SQLite journal-mode control plane.

This module owns names only. Runtime/database owns admission and safety policy.
"""

SQLITE_JOURNAL_MODE_ENV = "OH_GOON_SQLITE_JOURNAL_MODE"
SQLITE_JOURNAL_AUTO = "AUTO"
SQLITE_JOURNAL_DELETE = "DELETE"
SQLITE_JOURNAL_WAL = "WAL"
