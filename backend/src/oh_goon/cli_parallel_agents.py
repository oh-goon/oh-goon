"""Launch and inspect the selected local two-agent candidate workflow."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid, StateConflict
from oh_goon.core.models.parallel_agent_work import (
    ParallelAgentWorkReadback,
    ParallelAgentWorkRequest,
)
from oh_goon.core.vocabulary import FleetRunOutcome
from oh_goon.runtime.database import LocalDatabase, default_database_path
from oh_goon.runtime.parallel_agent_work import (
    cancel_parallel_work,
    read_parallel_work,
    replay_parallel_work,
)
from oh_goon.services.fleet_plan_parallel_agents import parallel_request_hash

_REQUEST_BYTES = 512 * 1024


def _read_request(path: Path) -> ParallelAgentWorkRequest:
    with path.open("rb") as stream:
        data = stream.read(_REQUEST_BYTES + 1)
    if len(data) > _REQUEST_BYTES:
        raise FleetPlanInvalid("Parallel work request exceeds its bounded input")
    try:
        return ParallelAgentWorkRequest.model_validate_json(data)
    except (ValidationError, UnicodeError):
        raise FleetPlanInvalid("Parallel work request has an invalid field or value") from None


def _existing_database(*, write: bool = False) -> LocalDatabase:
    from oh_goon.integrations.hashmarks import require_loaded_hashmarks_runtime

    require_loaded_hashmarks_runtime()
    path = default_database_path()
    if not path.is_file():
        raise StateConflict("Parallel work requires the existing selected runtime state")
    return LocalDatabase(path, read_only=not write)


def _print_work(result: ParallelAgentWorkReadback, *, as_json: bool) -> None:
    if as_json:
        print(result.model_dump_json())
        return
    print(f"Parallel work {result.intent.work_id} at {result.intent.base_revision}")
    for child in result.children:
        state = child.outcome.value if child.outcome is not None else child.dispatch
        reason = f" ({child.refusal})" if child.refusal else ""
        cancellation = " · cancellation requested" if child.cancellation_requested else ""
        print(f"{child.fleet_run_id} · {state}{reason}{cancellation}")


def _observe_to_completion(
    runtime: ApplicationRuntime,
    database: LocalDatabase,
    result: ParallelAgentWorkReadback,
) -> ParallelAgentWorkReadback:
    for child in result.children:
        if child.status is not None and not child.status.terminal:
            runtime.runs.wait_for_terminal(child.fleet_run_id)
    return read_parallel_work(database, result.intent.work_id)


def _work_exit_status(result: ParallelAgentWorkReadback) -> int:
    if not result.dispatch_closed or any(
        child.status is not None and not child.status.terminal for child in result.children
    ):
        return 0
    return 0 if all(child.outcome is FleetRunOutcome.SUCCESS for child in result.children) else 1


def _watch_work(args: argparse.Namespace) -> int:
    seconds = args.wait_seconds
    if not 1 <= seconds <= 86_400:
        raise FleetPlanInvalid("Parallel work wait requires 1 to 86400 seconds")
    database = _existing_database()
    deadline = time.monotonic() + seconds
    while True:
        result = read_parallel_work(database, args.work_id)
        terminal = result.dispatch_closed and all(
            child.dispatch == "never_started"
            or (child.status is not None and child.status.terminal)
            for child in result.children
        )
        remaining = deadline - time.monotonic()
        if terminal or remaining <= 0:
            break
        time.sleep(min(remaining, 0.25))

    if args.as_json:
        import json

        print(
            json.dumps(
                {
                    "schema": "oh-goon.parallel-work-wait/v1",
                    "status": "terminal" if terminal else "timed_out",
                    "work": result.model_dump(mode="json"),
                }
            )
        )
    else:
        _print_work(result, as_json=False)
        if not terminal:
            print("Wait elapsed before terminal readback. Original work may still be active.")
    return _work_exit_status(result) if terminal else 3


def _start(args: argparse.Namespace) -> int:
    from oh_goon.cli_ai import resolve_native_cli_selection
    from oh_goon.services.fleet_plan_candidate import workspace_control_git

    request = _read_request(args.request)
    path = default_database_path()
    if path.is_file():
        replay = replay_parallel_work(
            _existing_database(), request.work_id, parallel_request_hash(request)
        )
        if replay is not None:
            _print_work(replay, as_json=args.as_json)
            return _work_exit_status(replay)
    selected_a, isolation, git = resolve_native_cli_selection(
        agent="pi",
        profile=request.native_profiles[0],
        workspace=request.workspace,
        config_file=request.provider_catalog,
    )
    if request.native_profiles[1] == request.native_profiles[0]:
        selected_b, other_isolation = selected_a, isolation
    else:
        selected_b, other_isolation, _ = resolve_native_cli_selection(
            agent="pi",
            profile=request.native_profiles[1],
            workspace=request.workspace,
            config_file=request.provider_catalog,
        )
    if (
        isolation is None
        or other_isolation is None
        or isolation.configuration_identity() != other_isolation.configuration_identity()
    ):
        raise FleetPlanInvalid("Both profiles require the same selected Bubblewrap backend")

    runtime = ApplicationRuntime.build(
        standard_local=True,
        run_concurrency=request.capacity,
        standard_process_isolation=isolation,
        git_service=workspace_control_git(git),
    )
    database = _existing_database()
    try:
        try:
            result = runtime.plans.start_parallel_agent_work(
                request, selected=(selected_a, selected_b)
            )
            result = _observe_to_completion(runtime, database, result)
        except KeyboardInterrupt:
            write_database = _existing_database(write=True)
            try:
                result = cancel_parallel_work(write_database, request.work_id)
            except StateConflict:
                return 130
            result = _observe_to_completion(runtime, database, result)
            _print_work(result, as_json=args.as_json)
            return 130
        _print_work(result, as_json=args.as_json)
        return _work_exit_status(result)
    finally:
        runtime.shutdown()


def run_parallel_agent_command(args: argparse.Namespace) -> int:
    from oh_goon.core.models.agent_notes import AgentNotesError

    try:
        if args.runs_command == "start-parallel":
            return _start(args)
        if args.runs_command in {"advance-candidate", "branch-advance", "start-round"}:
            return _branch_command(args)
        if args.runs_command == "cancel-parallel":
            result = cancel_parallel_work(
                _existing_database(write=True), args.work_id, fleet_run_id=args.fleet_run_id
            )
        elif args.runs_command == "parallel-notes":
            return _notes_command(args)
        elif args.wait_seconds is not None:
            return _watch_work(args)
        else:
            result = read_parallel_work(_existing_database(), args.work_id)
        _print_work(result, as_json=args.as_json)
        return 0
    except AgentNotesError as exc:
        print(exc.code, file=sys.stderr)
        return 2
    except (FleetDomainError, OSError, ValueError, sqlite3.Error) as exc:
        from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic

        print_safe_plan_diagnostic(exc, as_json=args.as_json)
        return 2


def _branch_command(args: argparse.Namespace) -> int:
    from oh_goon.runtime.branch_advances import BranchAdvanceStore
    from oh_goon.services.branch_advance import BranchAdvanceRequest, advance_verified_candidate

    if args.runs_command == "advance-candidate":
        request = BranchAdvanceRequest(
            advance_id=args.advance_id,
            fleet_run_id=args.fleet_run_id,
            checkpoint_id=args.checkpoint_id,
            branch_owner=args.branch_owner,
            workspace=args.workspace,
            branch_ref=args.branch_ref,
            expected_tip=args.expected_tip,
        )
        database = _existing_database(write=True)
        runtime = ApplicationRuntime.build(standard_local=True, startup_purpose="workflow")
        try:
            receipt = advance_verified_candidate(
                request, database=database, repositories=runtime.repositories
            )
        finally:
            runtime.shutdown()
    else:
        observed = BranchAdvanceStore(_existing_database()).find(args.advance_id)
        if observed is None:
            raise StateConflict("Unknown branch advancement")
        receipt = observed
        if args.runs_command == "start-round":
            if receipt.published_at is None:
                raise StateConflict("Next Run requires a confirmed branch advancement")
            return _start_round(args, receipt.preparation.repository_path)
    if args.as_json:
        print(receipt.model_dump_json())
    else:
        prepared = receipt.preparation
        state = "Published" if receipt.published_at is not None else "Prepared"
        print(f"{state} {prepared.advance_id} · {prepared.branch_ref} · {prepared.commit}")
        if receipt.next_run is not None:
            print(f"Next Fleet Run ID: {receipt.next_run.fleet_run_id}")
    return 0


def _start_round(args: argparse.Namespace, workspace: Path) -> int:
    import json

    from oh_goon.cli_ai import _task, resolve_native_cli_selection
    from oh_goon.cli_result import CliTermination
    from oh_goon.core.models.agent_providers import PiSessionIntent

    task = _task(args)
    skill = args.skill.expanduser().absolute() if args.skill is not None else None
    selected, isolation, git = resolve_native_cli_selection(
        agent="pi",
        profile=args.profile,
        workspace=workspace,
        config_file=args.config,
        session=PiSessionIntent(mode="ephemeral"),
    )
    runtime = ApplicationRuntime.build(
        standard_local=True, standard_process_isolation=isolation, git_service=git
    )
    try:
        run = runtime.plans.start_writable_round(
            advance_id=args.advance_id,
            selected=selected,
            workspace=workspace,
            skill=skill,
            task=task,
        )
        print(f"Fleet Run ID: {run.fleet_run_id}", file=sys.stderr)
        try:
            detail = runtime.runs.wait_for_terminal(run.fleet_run_id)
        except KeyboardInterrupt as interruption:
            runtime.runs.cancel(run.fleet_run_id)
            runtime.runs.wait_for_terminal(run.fleet_run_id)
            return 143 if isinstance(interruption, CliTermination) else 130
        result = {
            "advance_id": args.advance_id,
            "fleet_run_id": run.fleet_run_id,
            "status": detail.run.status.value,
            "outcome": detail.outcome.value,
        }
        if args.as_json:
            print(json.dumps(result))
        else:
            print(f"{detail.run.status.value} · {detail.outcome.value}")
        return 0 if detail.outcome is FleetRunOutcome.SUCCESS else 1
    finally:
        runtime.shutdown()


def _notes_command(args: argparse.Namespace) -> int:
    import json

    from oh_goon.runtime.agent_notes import AgentNotesStore
    from oh_goon.runtime.journal import Journal
    from oh_goon.services.parallel_note_export import export_parallel_notes

    if (args.export_note is None) != (args.output is None):
        raise FleetPlanInvalid("Note export requires --export-note and --output")
    if args.export_note is not None:
        if any(value is not None for value in (args.after, args.receipt_run, args.request_id)):
            raise FleetPlanInvalid("Note export cannot use paging or receipt selectors")
        result = export_parallel_notes(
            _existing_database(), args.work_id, tuple(args.export_note), args.output
        )
        if args.as_json:
            print(json.dumps(result))
        else:
            print(f"Exported selected notes to {json.dumps(result['destination'])}")
        return 0
    database = _existing_database()
    read_parallel_work(database, args.work_id)
    notes = AgentNotesStore(database, Journal(database))
    if (args.receipt_run is None) != (args.request_id is None):
        raise FleetPlanInvalid("Note receipt lookup requires both --receipt-run and --request-id")
    if args.receipt_run is not None:
        receipt = notes.receipt(args.work_id, args.receipt_run, args.request_id)
        if receipt is None:
            raise StateConflict("Exact note publication receipt is unavailable")
        print(receipt.model_dump_json())
        return 0
    page = notes.read_page(args.work_id, args.after if args.after is not None else 0)
    if args.as_json:
        print(
            json.dumps(
                {
                    "work_id": args.work_id,
                    "page": page.model_dump(mode="json"),
                    "deliveries": [
                        delivery.model_dump(mode="json")
                        for delivery in notes.deliveries(args.work_id)
                    ],
                }
            )
        )
    else:
        print(f"Parallel work {args.work_id} · {page.authority}")
        for note in page.notes:
            print(
                f"{note.receipt.note_id} · {note.receipt.author.fleet_run_id} · {note.receipt.kind}"
            )
            print(_terminal_note_body(note.body))
        if page.next_cursor is not None:
            print(f"Continue after {page.next_cursor}")
    return 0


def _terminal_note_body(body: str) -> str:
    visible = "".join(
        character if character.isprintable() or character in "\n\t" else ascii(character)[1:-1]
        for character in body
    )
    return "\n".join(f"  {line}" for line in visible.split("\n"))
