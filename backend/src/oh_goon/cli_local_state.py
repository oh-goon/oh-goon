from __future__ import annotations

import argparse
import sqlite3

from oh_goon.core.errors import FleetDomainError
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, inspect_local_database
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.runtime.local_state_maintenance import (
    BUILT_RELEASE_RESET_COMMAND,
    SOURCE_RESET_COMMAND,
    LocalStateInspection,
    reset_local_state,
    sqlite_state_files,
)
from oh_goon.runtime.storage_health import probe_sqlite_prelaunch


def _local_state_inspection(action: str) -> LocalStateInspection:
    checks = action in {"check", "prelaunch-check"}
    return inspect_local_database(
        expected_schema_version=DATABASE_SCHEMA_VERSION,
        quick_check=checks,
        foreign_key_check=checks,
    )


def _local_state_path_command(inspection: LocalStateInspection, _args: argparse.Namespace) -> int:
    print(inspection.path)
    return 0


def _local_state_diagnostics_path_command(
    inspection: LocalStateInspection, _args: argparse.Namespace
) -> int:
    print(ApplicationStatePathLayout.from_database(inspection.path).diagnostics_log)
    return 0


def _local_state_check_command(inspection: LocalStateInspection, _args: argparse.Namespace) -> int:
    _print_inspection(inspection)
    if inspection.compatible:
        return 0
    print()
    _print_reset_recovery(inspection=inspection)
    return 2


def _local_state_prelaunch_command(
    inspection: LocalStateInspection, _args: argparse.Namespace
) -> int:
    _print_inspection(inspection)
    if not inspection.compatible:
        print()
        _print_reset_recovery(inspection=inspection)
        return 2
    try:
        probe_sqlite_prelaunch(inspection.path)
    except (OSError, sqlite3.Error) as exc:
        print("Prelaunch SQLite write/lock proof: FAILED")
        print(f"Reason:      {exc}")
        print("Startup stopped before opening the application.")
        print("Fix local storage permissions/free space/filesystem semantics and retry.")
        return 2
    print("Prelaunch SQLite write/lock proof: READY")
    return 0


def _local_state_reset_command(inspection: LocalStateInspection, args: argparse.Namespace) -> int:
    league_folders = LeagueFolderStore()
    if not args.yes:
        print("This deletes oh-goon's disposable local database and run history.")
        print(f"Database: {inspection.path}")
        print(f"League folders are preserved separately in: {league_folders.path}")
        print("Files owned by this local database:")
        for path in sqlite_state_files(inspection.path):
            print(f"  {path}")
        print()
        print("Nothing was deleted.")
        print("To continue:")
        _print_reset_recovery(indent="  ")
        return 2

    try:
        deleted = reset_local_state(inspection.path)
    except (FleetDomainError, OSError) as exc:
        print(f"Local state reset failed: {exc}")
        return 2
    print(f"Local state reset: {inspection.path}")
    print(f"League folders remain in: {league_folders.path}")
    if deleted:
        print(f"Deleted {len(deleted)} SQLite file(s).")
    else:
        print("No local database files existed; startup is already clean.")
    print("Start again with the same path: make dev (source) or oh-goon start (built release)")
    return 0


def run_local_state_command(args: argparse.Namespace) -> int | None:
    if args.command != "local-state":
        return None
    action = args.local_state_command
    inspection = _local_state_inspection(action)
    handlers = {
        "path": _local_state_path_command,
        "diagnostics-path": _local_state_diagnostics_path_command,
        "check": _local_state_check_command,
        "prelaunch-check": _local_state_prelaunch_command,
        "reset": _local_state_reset_command,
    }
    handler = handlers.get(action)
    return handler(inspection, args) if handler is not None else None


def check_local_state_for_startup() -> int:
    inspection = inspect_local_database(
        expected_schema_version=DATABASE_SCHEMA_VERSION,
        quick_check=True,
        foreign_key_check=True,
    )
    if not inspection.compatible:
        _print_inspection(inspection)
        print()
        print("Startup stopped before opening the application.")
        _print_reset_recovery(inspection=inspection)
        return 2
    try:
        probe_sqlite_prelaunch(inspection.path)
    except (OSError, sqlite3.Error) as exc:
        _print_inspection(inspection)
        print()
        print("Startup stopped before opening the application.")
        print(f"SQLite prelaunch write/lock proof failed: {exc}")
        print("Fix local storage permissions/free space/filesystem semantics and retry.")
        return 2
    return 0


def _print_inspection(inspection: LocalStateInspection) -> None:
    print(f"Local state: {inspection.status.value}")
    print(f"Database:    {inspection.path}")
    if inspection.found_schema_version is not None:
        print(
            f"Schema:      {inspection.found_schema_version} (expected {inspection.expected_schema_version})"
        )
    else:
        print(f"Schema:      not created (expected {inspection.expected_schema_version})")
    print(inspection.detail)


def _print_reset_recovery(
    *, indent: str = "", inspection: LocalStateInspection | None = None
) -> None:
    if inspection is not None and not inspection.compatible:
        print(
            f"{indent}Preserve this database and its SQLite sidecars. Use its matching original runtime to settle Runs, effects and workspace writers before archival or replacement."
        )
        print(f"{indent}Reset commands apply only to compatible, fully settled local state.")
    print(f"{indent}Source checkout: {SOURCE_RESET_COMMAND}")
    print(f"{indent}Built release:   {BUILT_RELEASE_RESET_COMMAND}")
    print(
        f"{indent}Use the command from the same checkout/release that reported the mismatch; do not use an older global binary."
    )
