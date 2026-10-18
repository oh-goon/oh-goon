"""RUN-2: read-only CLI projections from the original Fleet Run control plane.

No separate reducer, journal read, output retention, or outcome classification.
"""
from __future__ import annotations

import argparse
import json

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _status(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # One journal projection from the original owner; never recompute status.
    detail = runtime.runs.get(args.fleet_run_id)
    target = detail.run.target
    plan = getattr(target, "plan", None)
    result = {
        "schema": "oh-goon.plan-run-status/v1",
        "fleet_run_id": detail.run.fleet_run_id,
        "plan_id": plan.plan_id if plan is not None else None,
        "status": detail.run.status.value,
        "outcome": detail.outcome.value,
        "progress": detail.run.progress.model_dump(mode="json"),
    }
    if args.as_json:
        _print_json(result)
    else:
        print(f"Fleet Run ID: {result['fleet_run_id']}")
        print(f"Plan ID: {result['plan_id'] or 'unknown'}")
        print(f"Status: {result['status']}")
        print(f"Outcome: {result['outcome']}")
        print(f"Progress: {detail.run.progress.completed}/{detail.run.progress.total}")
    return 0


def _events(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # Preserve original journal cursor and revision; no second pagination owner.
    batch = runtime.runs.events(args.fleet_run_id, after=args.after, limit=args.limit)
    if args.as_json:
        _print_json(batch.model_dump(mode="json"))
    else:
        print(f"Fleet Run ID: {batch.run.fleet_run_id}")
        for event in batch.events:
            print(f"{event.sequence}: {event.event_type.value}")
        print(f"Next sequence: {batch.next_sequence}")
        print(f"More events: {'yes' if batch.has_more else 'no'}")
        print(f"Journal revision: {batch.journal_revision}")
    return 0


def _logs(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # Live output is transient (ACTIVE/SEALED/UNAVAILABLE), NOT durable journal
    # evidence. The control plane alone determines retention and its cursor.
    batch = runtime.runs.game_tape_live_output(args.fleet_run_id, after=args.after)
    if args.as_json:
        _print_json(batch.model_dump(mode="json"))
    else:
        print(f"Fleet Run ID: {batch.fleet_run_id}")
        print(f"Live output: {batch.state}")
        for chunk in batch.chunks:
            print(f"[{chunk.sequence} {chunk.repository_id}/{chunk.step_id} "
                  f"{chunk.stream}] {chunk.text}")
        print(f"Next sequence: {batch.next_sequence}")
        if not batch.available:
            print("Transient output unavailable; consult durable Run events/Game Tape.")
    return 0


def _tape(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # Use the already-owned FleetRunDetail Game Tape, not a second journal
    # replay, event interpretation, status reducer or output store.
    tape = runtime.runs.get(args.fleet_run_id).game_tape
    if args.as_json:
        _print_json(tape.model_dump(mode="json"))
    else:
        print(f"Fleet Run ID: {tape.fleet_run_id}")
        print(f"Game Tape status: {tape.status.value}")
        for repo in tape.repositories:
            print(f"Repository: {repo.repository.name} ({repo.repository_execution_id})")
            for step in repo.steps:
                print(f"  {step.order}: {step.step_id} [{step.status.value}] "
                      f"({step.step_execution_id})")
        print("For durable output evidence use 'plan outputs'; "
              "for transient bytes use 'plan logs'.")
    return 0


def _steps(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # Transport the *original* step and repository objects, including their
    # stable execution IDs and already projected reason/why/evidence. Do not
    # reconstruct status, progress, or failure links from events.
    tape = runtime.runs.get(args.fleet_run_id).game_tape
    result = {
        "schema": "oh-goon.plan-run-steps/v1",
        "fleet_run_id": tape.fleet_run_id,
        "issue_focus": [focus.model_dump(mode="json") for focus in tape.issue_focus],
        "repositories": [
            {
                "repository_execution_id": repo.repository_execution_id,
                "repository": repo.repository.model_dump(mode="json"),
                "status": repo.status.value,
                "steps": [step.model_dump(mode="json") for step in repo.steps],
            }
            for repo in tape.repositories
        ],
    }
    if args.as_json:
        _print_json(result)
    else:
        print(f"Fleet Run ID: {tape.fleet_run_id}")
        for repo in tape.repositories:
            print(f"Repository: {repo.repository.name} ({repo.repository_execution_id})")
            for step in repo.steps:
                print(f"  {step.order}: {step.step_id} [{step.status.value}] "
                      f"({step.step_execution_id})")
        if tape.issue_focus:
            print("Original issue focus is available with --json or 'plan tape'.")
    return 0


def _outputs(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # All output/artifact/evidence objects come verbatim from the same Game Tape
    # projection. Raw process output is transient and stays with plan logs.
    tape = runtime.runs.get(args.fleet_run_id).game_tape
    repositories = []
    for repo in tape.repositories:
        steps = []
        for step in repo.steps:
            steps.append({
                "step_execution_id": step.step_execution_id,
                "step_id": step.step_id,
                "output_names": list(step.output_names),
                "outputs": [item.model_dump(mode="json") for item in step.outputs],
                "artifacts": [item.model_dump(mode="json") for item in step.artifacts],
                "stdout_evidence": (
                    step.stdout_evidence.model_dump(mode="json")
                    if step.stdout_evidence is not None else None
                ),
                "stderr_evidence": (
                    step.stderr_evidence.model_dump(mode="json")
                    if step.stderr_evidence is not None else None
                ),
            })
        repositories.append({
            "repository_execution_id": repo.repository_execution_id,
            "repository": repo.repository.model_dump(mode="json"),
            "steps": steps,
        })
    result = {
        "schema": "oh-goon.plan-run-outputs/v1",
        "fleet_run_id": tape.fleet_run_id,
        "repositories": repositories,
    }
    if args.as_json:
        _print_json(result)
    else:
        print(f"Fleet Run ID: {tape.fleet_run_id}")
        for repo in repositories:
            print(f"Repository: {repo['repository']['name']} "
                  f"({repo['repository_execution_id']})")
            for step in repo['steps']:
                print(f"  {step['step_id']} ({step['step_execution_id']}): "
                      f"{len(step['outputs'])} published outputs, "
                      f"{len(step['artifacts'])} artifacts")
        print("Evidence metadata only; raw output uses 'plan logs' when available.")
    return 0


# Presentation-only readback inventory for independent agents.  These are
# argv tokens, never shell scripts or instructions to retry/re-execute work.
# All destinations are the existing read-only Plan CLI views; consumers still
# decide which evidence to inspect and the original owner decides its meaning.
_READBACK_EVIDENCE_KINDS = (
    ("status", "durable_run_projection"),
    ("events", "durable_journal"),
    ("tape", "durable_game_tape_projection"),
    ("steps", "durable_game_tape_projection"),
    ("outputs", "durable_game_tape_projection"),
    ("logs", "transient_live_output"),
)


def _readback_references(fleet_run_id: str) -> dict[str, dict[str, object]]:
    """Render navigable original views without classifying the Run or logs."""
    views: dict[str, dict[str, object]] = {}
    for command, evidence_kind in _READBACK_EVIDENCE_KINDS:
        argv = ["oh-goon", "plan", command, fleet_run_id]
        item: dict[str, object] = {"argv": argv, "evidence_kind": evidence_kind}
        if command == "events":
            argv.extend(("--after", "0", "--limit", "100"))
            item["resume_cursor_field"] = "next_sequence"
            item["has_more_field"] = "has_more"
        if command == "logs":
            argv.extend(("--after", "0"))
            item["availability"] = "not_checked"
        argv.append("--json")
        views[command] = item
    return views


def _handoff(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Transport terminal-navigation facts from ONE canonical Fleet read cut.

    No CLI-side terminal classification, diagnosis, prioritization, or replay.
    The bounded issue focus and safe next action already belong to Game Tape.
    Absence of focus or why_not is reported as absent, never filled from logs.
    """
    detail = runtime.runs.get(args.fleet_run_id)
    tape = detail.game_tape
    result = {
        "schema": "oh-goon.plan-run-handoff/v1",
        "fleet_run_id": detail.run.fleet_run_id,
        "plan_id": tape.plan_id,
        "status": detail.run.status.value,
        "outcome": detail.outcome.value,
        "game_tape_status": tape.status.value,
        "progress": detail.run.progress.model_dump(mode="json"),
        "receipt": tape.receipt.model_dump(mode="json"),
        "summary": tape.summary.model_dump(mode="json"),
        "issue_count": tape.issue_count,
        "issue_focus": [item.model_dump(mode="json") for item in tape.issue_focus],
        "hidden_issue_count": tape.hidden_issue_count,
        "why_not": tape.why_not.model_dump(mode="json") if tape.why_not is not None else None,
        "enforcer_receipt_available": tape.enforcer_receipt_available,
        "observability": tape.observability.model_dump(mode="json"),
        # Navigation references carry no recommendation, diagnosis or retry
        # permission.  The Fleet Run ID remains one argv element even if the
        # caller chooses a shell-sensitive literal ID.
        "readback": _readback_references(detail.run.fleet_run_id),
    }
    if args.as_json:
        _print_json(result)
    else:
        print(f"Fleet Run ID: {result['fleet_run_id']}")
        print(f"Plan ID: {result['plan_id'] or 'unknown'}")
        print(f"Status: {result['status']} | Outcome: {result['outcome']}")
        print(f"Game Tape status: {result['game_tape_status']}")
        print(f"Issues: {tape.issue_count} (displayed: {len(tape.issue_focus)}, "
              f"hidden: {tape.hidden_issue_count})")
        for focus in tape.issue_focus:
            identity = focus.step_execution_id or focus.repository_execution_id or "run"
            print(f"  {focus.scope}: {identity} | {focus.code or 'no code'}")
        if tape.why_not is not None:
            print(f"Original guidance: {tape.why_not.safe_next_action}")
        print("Inspect exact evidence using the structured readback argv references "
              "from --json; raw logs may be unavailable.")
    return 0


_READ_COMMANDS = {
    "status": _status, "events": _events, "logs": _logs,
    "tape": _tape, "steps": _steps, "outputs": _outputs,
    "handoff": _handoff,
}


def run_plan_readback(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch presentation only; do not interpret Run truth or start work."""
    try:
        return _READ_COMMANDS[args.plan_command](runtime, args)
    except FleetDomainError as exc:
        if args.as_json:
            _print_json({"schema": "oh-goon.plan-run-read-error/v1",
                         "fleet_run_id": args.fleet_run_id,
                         "status": "error", "message": str(exc)})
        else:
            print(str(exc))
        return 2
