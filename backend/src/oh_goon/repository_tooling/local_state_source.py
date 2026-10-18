"""Dependency-free source-checkout local-state inspection/reset implementation.

This script is intentionally an implementation detail behind Make targets. It imports
only the canonical runtime database-path/inspection/reset owners and can run with
its named pyproject entrypoint through ``uv run`` before the APP workspace dependency graph is materialized.
"""

from __future__ import annotations

import argparse
import sqlite3

from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.runtime.database import (
    DATABASE_SCHEMA_VERSION,
    default_database_path,
    inspect_local_database,
)
from oh_goon.runtime.local_state_maintenance import reset_local_state
from oh_goon.runtime.storage_health import probe_sqlite_prelaunch


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "action", choices=("check", "prelaunch-check", "path", "diagnostics-path", "reset")
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of source-checkout disposable SQLite state",
    )
    args = parser.parse_args()

    if args.action == "path":
        print(default_database_path())
        return 0

    if args.action == "diagnostics-path":
        layout = ApplicationStatePathLayout.from_database(default_database_path())
        print(layout.diagnostics_log)
        return 0

    inspection = inspect_local_database(
        expected_schema_version=DATABASE_SCHEMA_VERSION,
        quick_check=args.action in {"check", "prelaunch-check"},
        foreign_key_check=args.action in {"check", "prelaunch-check"},
    )
    if args.action in {"check", "prelaunch-check"}:
        print(f"Local state: {inspection.status.value}")
        print(f"Database:    {inspection.path}")
        if inspection.found_schema_version is None:
            print(f"Schema:      not created (expected {inspection.expected_schema_version})")
        else:
            print(
                f"Schema:      {inspection.found_schema_version} (expected {inspection.expected_schema_version})"
            )
        print(inspection.detail)
        if not inspection.compatible:
            print("Source checkout recovery: make local-state-reset")
            return 2
        if args.action == "check":
            return 0
        try:
            probe_sqlite_prelaunch(inspection.path)
        except (OSError, sqlite3.Error) as exc:
            print("Prelaunch SQLite write/lock proof: FAILED")
            print(f"Reason:      {exc}")
            print("Fix local storage permissions/free space/filesystem semantics and retry.")
            return 2
        print("Prelaunch SQLite write/lock proof: READY")
        return 0

    if not args.yes:
        print("Nothing was deleted.")
        print("Confirmed source checkout recovery: make local-state-reset")
        return 2

    deleted = reset_local_state(inspection.path)
    print(f"Local state reset: {inspection.path}")
    if deleted:
        print(f"Deleted {len(deleted)} SQLite file(s).")
    else:
        print("No local database files existed; startup is already clean.")
    print("Continue through the source checkout with: make dev")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
