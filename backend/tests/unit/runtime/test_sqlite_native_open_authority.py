from __future__ import annotations

from pathlib import Path


def test_production_native_sqlite_open_sites_are_explicit_and_bounded() -> None:
    """New sqlite3.connect callers must not silently bypass audited SQLite ownership."""

    package_root = Path(__file__).resolve().parents[3] / "src" / "oh_goon"
    actual: set[str] = set()
    for path in package_root.rglob("*.py"):
        if "sqlite3.connect(" in path.read_text(encoding="utf-8"):
            actual.add(path.relative_to(package_root).as_posix())

    assert actual == {
        "runtime/_database_policy.py",  # canonical read-only path/journal inspection
        "runtime/database.py",  # canonical operational DB
        "scout/database.py",  # separate disposable Scout analytical store
        "runtime/storage_health.py",  # startup/sibling filesystem probes only
    }
