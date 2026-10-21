from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_summary import configuration_summary
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.run import FailedFleetRun
from oh_goon.services.plan_success_evidence import has_exact_plan_success


def _projection_profile_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        print(runtime.journal_diagnostics.profile_reads(samples=args.samples).render())
    except FleetDomainError as exc:
        print(str(exc))
        return 2
    return 0


def _require_plan_success_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        ok = has_exact_plan_success(
            runtime,
            plan=args.plan,
            repository_id=args.repository_id,
            source_root=args.source_root,
            lock_path=args.lock_file,
            **({"fleet_run_id": args.fleet_run_id} if args.fleet_run_id is not None else {}),
        )
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc))
        return 2
    return 0 if ok else 3


def _print_explain_run_detail(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        detail = runtime.runs.get(args.fleet_run_id)
    except FleetDomainError:
        print(f"Unknown fleet run: {args.fleet_run_id}")
        return 2
    tape = detail.game_tape
    if getattr(args, "as_json", False):
        print(tape.model_dump_json(indent=2))
        return 0
    print(f"Run: {detail.run.fleet_run_id}")
    print(f"Capability: {detail.run.capability.capability_id}")
    print(f"Safety: {detail.run.capability.safety.value}")
    print(f"Action: {detail.run.action.value}")
    print(
        f"Target: {tape.execution_reference.model_dump_json() if tape.execution_reference else 'None'}"
    )
    for line in configuration_summary(tape.configuration):
        print(line)
    print(f"Status: {detail.run.status.value}")
    print(f"Progress: {detail.run.progress.completed}/{detail.run.progress.total}")
    _print_explain_run_lifecycle(detail)
    _print_explain_run_operations(detail)
    return 0


def _print_explain_run_lifecycle(detail) -> None:
    if isinstance(detail.run, FailedFleetRun):
        print(
            f"Failure: {detail.run.failure.code} "
            f"stage={detail.run.failure.stage.value} retryable={str(detail.run.failure.retryable).lower()}"
        )
        print(f"Failure message: {detail.run.failure.message}")
    print("Lifecycle:")
    for event in detail.lifecycle_events:
        suffix = f" operation={event.operation_id}" if hasattr(event, "operation_id") else ""
        print(f"  {event.sequence:>3}  {event.event_type.value}{suffix}")


def _print_explain_run_operations(detail) -> None:
    print("Operations:")
    if not detail.operations:
        print("  none committed")
    for report in detail.operations:
        print(
            f"  {report.repository.name}: {report.outcome.verdict.value} "
            f"({report.outcome.reason_code.value}) operation={report.provenance.operation_id}"
        )


def run_observability_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    if args.command != "dev":
        return None
    handlers = {
        "projection-profile": _projection_profile_command,
        "require-plan-success": _require_plan_success_command,
        "explain-run": _print_explain_run_detail,
    }
    handler = handlers.get(args.dev_command)
    return handler(runtime, args) if handler is not None else None
