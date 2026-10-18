from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from oh_goon.runtime.storage_health import (
    observe_storage_capacity,
    resolve_storage_capacity_policy,
)
from oh_goon.runtime.storage_lifecycle import (
    measure_storage,
    prune_generic_storage,
    storage_census,
)


def run_storage_command(args: argparse.Namespace) -> int | None:
    if args.command != "storage":
        return None
    if args.storage_command == "status":
        return _run_storage_status(args)
    if args.storage_command == "prune":
        return _run_storage_prune(args)
    return None


def _run_storage_prune(args: argparse.Namespace) -> int:
    if not args.yes:
        print("OH-GOON STORAGE PRUNE")
        print("Only storage classes with canonical positive disposal proof are eligible.")
        print("Durable state and unclassified bytes are refused.")
        print("Nothing was deleted.")
        print("To continue from this checkout: make storage-prune")
        return 2
    try:
        environment = dict(os.environ)
        report = prune_generic_storage(environment)
        from oh_goon.services.certification_bootstrap import reconcile_certification_run_workspaces

        state_value = environment.get("XDG_STATE_HOME", "").strip()
        state_home = Path(state_value).expanduser() if state_value else Path.home() / ".local/state"
        certification = reconcile_certification_run_workspaces(state_home=state_home)
    except Exception as exc:
        print("OH-GOON STORAGE PRUNE REFUSED")
        print(str(exc))
        return 2
    payload = report.to_payload()
    payload["removed_certification_run_workspaces"] = list(certification.removed)
    payload["refused_certification_run_workspaces"] = list(certification.refused)
    payload["cleanup_incomplete_certification_run_workspaces"] = list(
        certification.cleanup_incomplete
    )
    if args.as_json:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    _print_storage_prune_report(report, certification)
    return 0


def _print_storage_prune_report(report, certification) -> None:
    print("OH-GOON STORAGE PRUNE")
    print(f"Removed run logs: {report.removed_run_logs}")
    print(f"Removed run workspaces: {len(report.removed_run_workspaces)}")
    print(f"Refused run workspaces: {len(report.refused_run_workspaces)}")
    print(
        "Cleanup-incomplete run workspaces: "
        f"{len(report.cleanup_incomplete_run_workspaces)}"
    )
    print(f"Removed certification run workspaces: {len(certification.removed)}")
    print(f"Refused certification run workspaces: {len(certification.refused)}")
    print(
        "Cleanup-incomplete certification run workspaces: "
        f"{len(certification.cleanup_incomplete)}"
    )
    for workspace in certification.refused:
        print(f"  Refused certification workspace: {workspace}")
    for workspace in certification.cleanup_incomplete:
        print(f"  Cleanup-incomplete certification workspace: {workspace}")
    print(f"Removed content staging: {len(report.removed_content_staging)}")
    print(f"Refused content staging: {len(report.refused_content_staging)}")
    print(f"Removed retained objects: {len(report.removed_content_objects)}")
    print(f"Removed terminal launch capabilities: {len(report.removed_launch_access)}")
    print(f"Refused launch capabilities: {len(report.refused_launch_access)}")



def _measurement_payload(path, *, enabled: bool) -> dict[str, int] | None:
    if not enabled:
        return None
    measured = measure_storage(path)
    if measured is None:
        return None
    return {"entries": measured.entries, "bytes": measured.bytes}


def _storage_record(item, *, measure: bool) -> dict[str, object]:
    record: dict[str, object] = {
        "storage_id": item.storage_id,
        "path": str(item.path) if item.path is not None else None,
        "owner": item.owner,
        "durability": item.durability,
        "retention": item.retention,
        "cleanup_authority": item.cleanup_authority,
        "generic_prune": item.generic_prune,
    }
    if measure:
        record["measurement"] = _measurement_payload(item.path, enabled=True)
    return record


def _print_storage_record(record: dict[str, object], *, measure: bool) -> None:
    print()
    print(record["storage_id"])
    print(f"  Owner:      {record['owner']}")
    print(f"  Durability: {record['durability']}")
    print(f"  Retention:  {record['retention']}")
    print(f"  Cleanup:    {record['cleanup_authority']}")
    print(f"  Generic:    {'yes' if record['generic_prune'] else 'no'}")
    if record["path"] is not None:
        print(f"  Path:       {record['path']}")
    if not measure:
        return
    measurement = record.get("measurement")
    if measurement is None:
        print("  Usage:      absent")
        return
    assert isinstance(measurement, dict)
    print(f"  Usage:      {measurement['entries']} entries / {measurement['bytes']} bytes")


def _unclassified_payload(census, *, measure: bool) -> list[dict[str, object]]:
    return [
        {
            "path": str(item.path),
            "root_kind": item.root_kind,
            "reason": item.reason,
            "measurement": _measurement_payload(item.path, enabled=measure),
        }
        for item in census.unclassified
    ]


def _print_unclassified(census, *, measure: bool) -> None:
    if not census.unclassified:
        return
    print("\nUNCLASSIFIED OH-GOON STORAGE")
    print("  Reporting only: these paths have no automatic cleanup authority.")
    for item in census.unclassified:
        measured = measure_storage(item.path) if measure else None
        suffix = "" if measured is None else f" — {measured.entries} entries / {measured.bytes} bytes"
        print(f"  {item.path} [{item.root_kind}]{suffix}")


def _capacity_status(environment: dict[str, str]) -> dict[str, object]:
    state_value = environment.get("XDG_STATE_HOME", "").strip()
    state_home = (
        Path(state_value).expanduser() if state_value else Path.home() / ".local/state"
    )
    probe = state_home
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    policy = resolve_storage_capacity_policy(environment)
    observed = observe_storage_capacity(probe, policy)
    return {
        "pressure": observed.pressure,
        "free_bytes": observed.free_bytes,
        "soft_free_bytes": policy.soft_free_bytes,
        "hard_free_bytes": policy.hard_free_bytes,
    }


def _run_storage_status(args: argparse.Namespace) -> int:
    environment = dict(os.environ)
    census = storage_census(environment)
    capacity = _capacity_status(environment)
    payload = [_storage_record(item, measure=args.measure) for item in census.inventory]
    if args.as_json:
        result = {
            "capacity": capacity,
            "storage": payload,
            "unclassified": _unclassified_payload(census, measure=args.measure),
        }
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    print("OH-GOON LOCAL STORAGE")
    print(
        "Capacity: "
        f"{capacity['pressure']} — free {capacity['free_bytes']} bytes; "
        f"soft {capacity['soft_free_bytes']}; hard {capacity['hard_free_bytes']}"
    )
    for record in payload:
        _print_storage_record(record, measure=args.measure)
    _print_unclassified(census, measure=args.measure)
    return 0
