"""RUN-2: read-only CLI projections from the original Fleet Run control plane.

No separate reducer, journal read, output retention, or outcome classification.
"""

from __future__ import annotations

import argparse
import json
import shlex
from itertools import islice
from pathlib import PurePosixPath
from typing import Any

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_summary import configuration_summary
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.game_tape import GameTape, GameTapeStepExecution
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.vocabulary import GameTapeStatus


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, ensure_ascii=False))


def run_completion_facts(detail: FleetRunDetail) -> dict[str, object]:
    """Use the original Game Tape summary and guidance from this exact read cut."""
    tape = detail.game_tape
    summary = tape.summary
    return {
        "steps": {
            "planned": summary.planned_steps,
            "executed": summary.executed_steps,
            "passed": summary.passed_steps,
            "failed": summary.failed_steps,
            "skipped": summary.skipped_steps,
            "not_run": summary.not_run_steps,
        },
        "why_not": tape.why_not.model_dump(mode="json") if tape.why_not is not None else None,
    }


def _artifact_readback_detail(
    runtime: ApplicationRuntime, args: argparse.Namespace
) -> FleetRunDetail:
    observe = getattr(args, "observe_artifacts", False)
    limit = getattr(args, "artifact_limit", None)
    byte_budget = getattr(args, "artifact_byte_budget", None)
    time_budget = getattr(args, "artifact_time_budget_seconds", None)
    if observe or any(value is not None for value in (limit, byte_budget, time_budget)):
        return runtime.runs.get(
            args.fleet_run_id,
            observe_artifacts=observe,
            artifact_limit=limit,
            artifact_byte_budget=byte_budget,
            artifact_time_budget_seconds=time_budget,
        )
    return runtime.runs.get(args.fleet_run_id)


def _print_artifact_observation(tape: GameTape) -> None:
    snapshot = tape.artifact_observation
    if snapshot is None:
        print(
            "Artifact availability: UNOBSERVED (not requested; collection evidence is historical)."
        )
        print(
            "Check availability: "
            + shlex.join(("oh-goon", "runs", "outputs", tape.fleet_run_id, "--observe-artifacts"))
        )
        return
    print(
        f"Artifact availability: {len(snapshot.observations)} entries; "
        f"{snapshot.omitted_artifacts} omitted; journal revision {snapshot.journal_revision}"
    )
    print(
        "Verified availability is point-in-time evidence; export checks its live reference again."
    )
    for item in snapshot.observations:
        print(
            f"  {item.repository_id}/{item.step_id}: {json.dumps(item.path, ensure_ascii=True)} "
            f"[{item.status}] {item.reason} (checked {item.checked_at.isoformat()})"
        )
        if item.cleanup_failure is not None:
            print(
                f"    Observation staging cleanup: {item.staging_cleanup} ({item.cleanup_failure})"
            )
        if item.status == "AVAILABLE":
            print(
                "    Export with recheck: "
                + shlex.join(
                    (
                        "oh-goon",
                        "runs",
                        "export-artifact",
                        snapshot.fleet_run_id,
                        "--repository-id",
                        item.repository_id,
                        "--step",
                        item.step_id,
                        "--path",
                        item.path,
                        "--output",
                        "./" + PurePosixPath(item.path).name,
                    )
                )
            )


def print_run_completion(detail: FleetRunDetail) -> None:
    """Show bounded original completion evidence from the supplied terminal read cut."""
    tape = detail.game_tape
    summary = tape.summary
    print(
        f"Steps: {summary.executed_steps} executed; {summary.passed_steps} passed; "
        f"{summary.failed_steps} failed; {summary.skipped_steps} skipped; {summary.not_run_steps} not run"
    )
    if tape.why_not is not None:
        print(f"Failure: {tape.why_not.code}: {tape.why_not.message}")
        print(f"Next action: {tape.why_not.safe_next_action}")
    failed_steps = (
        (repository.repository.name, step)
        for repository in tape.repositories
        for step in repository.steps
        if step.status is GameTapeStatus.FAILED
    )
    for repository_name, step in islice(failed_steps, 8):
        exit_code = f" (exit {step.exit_code})" if step.exit_code is not None else ""
        print(f"Failed Step: {repository_name}/{step.step_id}{exit_code}")
        if step.reason is not None:
            print(f"  Recorded reason: {step.reason}")
    if next(failed_steps, None) is not None:
        print("More failed Steps are recorded; inspect the Run below.")
    _print_completion_outputs(tape, fleet_run_id=detail.run.fleet_run_id)
    for label, command in (
        ("Inspect Run", "handoff"),
        ("Inspect outputs", "outputs"),
        ("Read logs (while available)", "logs"),
    ):
        print(f"{label}: " + shlex.join(("oh-goon", "runs", command, detail.run.fleet_run_id)))


def _print_completion_outputs(tape: GameTape, *, fleet_run_id: str) -> None:
    outputs = (
        (repository.repository.name, step.step_id, output)
        for repository in tape.repositories
        for step in repository.steps
        for output in step.outputs
    )
    for repository_name, step_id, output in islice(outputs, 8):
        print(
            f"Output evidence: {repository_name}/{step_id}/{output.name} "
            f"({output.byte_count} bytes; sha256:{output.sha256})"
        )
    if next(outputs, None) is not None:
        print("More output evidence is recorded; inspect outputs below.")
    artifacts = (
        (repository.repository, step.step_id, artifact)
        for repository in tape.repositories
        for step in repository.steps
        for artifact in step.artifacts
    )
    for repository, step_id, artifact in islice(artifacts, 8):
        print(f"Artifact: {repository.name}/{step_id}: {artifact.path} [{artifact.status}]")
        if artifact.status == "PRESENT":
            print(f"  Evidence: {artifact.byte_count} bytes; sha256:{artifact.sha256}")
            if artifact.retained_object is not None:
                print(f"  Retained reference: {artifact.retained_object.reference_id}")
                print(
                    "  Export artifact: "
                    + shlex.join(
                        (
                            "oh-goon",
                            "runs",
                            "export-artifact",
                            fleet_run_id,
                            "--repository-id",
                            repository.repository_id,
                            "--step",
                            step_id,
                            "--path",
                            artifact.path,
                            "--output",
                            "./" + PurePosixPath(artifact.path).name,
                        )
                    )
                )
                print("  Recorded selection hint; current byte availability has not been checked.")
    if next(artifacts, None) is not None:
        print("More artifacts are recorded; inspect outputs below.")


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
            print(
                f"[{chunk.sequence} {chunk.repository_id}/{chunk.step_id} "
                f"{chunk.stream}] {chunk.text}"
            )
        print(f"Next sequence: {batch.next_sequence}")
        if not batch.available:
            print("Transient output unavailable; consult durable Run events/Game Tape.")
    return 0


def _tape(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # Use the already-owned FleetRunDetail Game Tape, not a second journal
    # replay, event interpretation, status reducer or output store.
    tape = _artifact_readback_detail(runtime, args).game_tape
    if args.as_json:
        _print_json(tape.model_dump(mode="json"))
    else:
        print(f"Fleet Run ID: {tape.fleet_run_id}")
        print(f"Game Tape status: {tape.status.value}")
        print(f"Journal revision: {tape.journal_revision}")
        for line in configuration_summary(tape.configuration):
            print(line)
        if tape.why_not is not None:
            print(f"Failure: {tape.why_not.code} stage={tape.why_not.stage}")
            print(tape.why_not.message)
            print(f"Next action: {tape.why_not.safe_next_action}")
        for repo in tape.repositories:
            print(f"Repository: {repo.repository.name} ({repo.repository_execution_id})")
            for step in repo.steps:
                print(
                    f"  {step.order}: {step.step_id} [{step.status.value}] "
                    f"({step.step_execution_id})"
                )
                if step.reason is not None:
                    print(f"    Recorded reason: {step.reason}")
        print(
            "For durable output evidence use 'runs outputs'; for transient bytes use 'runs logs'."
        )
        _print_artifact_observation(tape)
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
                print(
                    f"  {step.order}: {step.step_id} [{step.status.value}] "
                    f"({step.step_execution_id})"
                )
        if tape.issue_focus:
            print("Original issue focus is available with --json or 'runs tape'.")
    return 0


def _outputs(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    # All output/artifact/evidence objects come verbatim from the same Game Tape
    # projection. Raw process output is transient and stays with runs logs.
    tape = _artifact_readback_detail(runtime, args).game_tape
    repositories: list[dict[str, Any]] = []
    for repo in tape.repositories:
        steps: list[dict[str, Any]] = []
        for step in repo.steps:
            steps.append(
                {
                    "step_execution_id": step.step_execution_id,
                    "step_id": step.step_id,
                    "output_names": list(step.output_names),
                    "outputs": [item.model_dump(mode="json") for item in step.outputs],
                    "artifacts": [item.model_dump(mode="json") for item in step.artifacts],
                    "stdout_evidence": (
                        step.stdout_evidence.model_dump(mode="json")
                        if step.stdout_evidence is not None
                        else None
                    ),
                    "stderr_evidence": (
                        step.stderr_evidence.model_dump(mode="json")
                        if step.stderr_evidence is not None
                        else None
                    ),
                }
            )
        repositories.append(
            {
                "repository_execution_id": repo.repository_execution_id,
                "repository": repo.repository.model_dump(mode="json"),
                "steps": steps,
            }
        )
    result = {
        "schema": "oh-goon.plan-run-outputs/v1",
        "fleet_run_id": tape.fleet_run_id,
        "repositories": repositories,
        "artifact_observation": (
            tape.artifact_observation.model_dump(mode="json")
            if tape.artifact_observation is not None
            else None
        ),
    }
    if args.as_json:
        _print_json(result)
    else:
        print(f"Fleet Run ID: {tape.fleet_run_id}")
        for repository_output in repositories:
            print(
                f"Repository: {repository_output['repository']['name']} ({repository_output['repository_execution_id']})"
            )
            for step_output in repository_output["steps"]:
                print(
                    f"  {step_output['step_id']} ({step_output['step_execution_id']}): "
                    f"{len(step_output['outputs'])} published outputs, "
                    f"{len(step_output['artifacts'])} artifacts"
                )
        print("Evidence metadata only; raw output uses 'runs logs' when available.")
        _print_artifact_observation(tape)
    return 0


# Presentation-only readback inventory for independent agents.  These are
# argv tokens, never shell scripts or instructions to retry/re-execute work.
# All destinations are the existing read-only Plan CLI views; consumers still
# decide which evidence to inspect and the original owner decides its meaning.
_READBACK_EVIDENCE_KINDS = (
    ("show", "durable_run_projection"),
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
        argv = ["oh-goon", "runs", command, fleet_run_id]
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


_MAX_HANDOFF_STEPS = 32


def _handoff_claim_excerpt(summary: str) -> dict[str, object]:
    """Preserve visible leading context and trailing cause within one display bound."""
    if len(summary) <= 512:
        return {"summary": summary, "omitted_summary_characters": 0}
    marker = "\n[... summary characters omitted ...]\n"
    retained = 512 - len(marker)
    head = (retained + 1) // 2
    tail = retained // 2
    return {
        "summary": summary[:head] + marker + summary[-tail:],
        "omitted_summary_characters": len(summary) - retained,
    }


def _handoff_step(step: GameTapeStepExecution, repository_execution_id: str) -> dict[str, Any]:
    observations = step.native_agent_observations
    return {
        "repository_execution_id": repository_execution_id,
        "step_execution_id": step.step_execution_id,
        "step_id": step.step_id,
        "kind": step.kind,
        "status": step.status.value,
        "exit_code": step.exit_code,
        "settlement": step.settlement.model_dump(mode="json"),
        "project_source": step.project_source.model_dump(mode="json")
        if step.project_source is not None
        else None,
        "uv": step.uv.model_dump(mode="json") if step.uv is not None else None,
        "reason": step.reason,
        "native_claims": [
            {
                "authority": item.authority,
                "agent": item.agent,
                "profile": item.profile,
                "input_identity": item.input_identity,
                "source_identity": item.source_identity,
                "occurrence": item.occurrence,
                "role": item.role,
                "settled_claim": item.settled,
                "failed_claim": item.failed,
                "events": [
                    {
                        "kind": event.kind,
                        **_handoff_claim_excerpt(event.summary),
                    }
                    for event in item.events[-2:]
                ],
                "omitted_events": max(0, len(item.events) - 2) + item.omitted_observations,
            }
            for item in observations[-1:]
        ],
        "omitted_native_observations": max(0, len(observations) - 1),
        "native_claim_availability": "recorded" if observations else "unavailable",
        "verification_authority": "Canonical Step status/exit only; native claims do not establish verification success",
    }


def _handoff_steps(tape: GameTape) -> dict[str, Any]:
    count = sum(len(repository.steps) for repository in tape.repositories)
    rows: list[dict[str, Any]] = []
    for repository in tape.repositories:
        for step in repository.steps:
            if len(rows) == _MAX_HANDOFF_STEPS:
                break
            rows.append(_handoff_step(step, repository.repository_execution_id))
    return {
        "steps": rows,
        "omitted_steps": max(0, count - len(rows)),
        "scope": "bounded original projection; omissions and absent claims do not establish completion",
    }


def run_handoff_projection(detail: FleetRunDetail) -> dict[str, Any]:
    """Project ONE supplied canonical read cut; retain uncertainty and omission facts."""
    tape = detail.game_tape
    return {
        "schema": "oh-goon.plan-run-handoff/v1",
        "fleet_run_id": detail.run.fleet_run_id,
        "journal_revision": detail.journal_revision,
        "artifact_observation": (
            tape.artifact_observation.model_dump(mode="json")
            if tape.artifact_observation is not None
            else None
        ),
        "campaign_journal": detail.campaign_journal.model_dump(mode="json")
        if detail.campaign_journal is not None
        else None,
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
        "source_admissions": [
            {
                "repository_id": item.repository_id,
                "admission_id": item.admission_id,
                "source_authority_id": item.source_authority.authority_id,
                "source_subject_instance_id": item.source_authority.subject_instance_id,
                "source_snapshot": item.source_authority.source_snapshot.model_dump(mode="json"),
            }
            for item in detail.repository_admissions[:8]
        ],
        "omitted_source_admissions": max(0, len(detail.repository_admissions) - 8),
        "source_admission_availability": "recorded"
        if detail.repository_admissions
        else "unavailable",
        "recorded_steps": _handoff_steps(tape),
        "supporting_diagnostics": {
            "path_readback_argv": ["oh-goon", "local-state", "diagnostics-path"],
            "correlate_fleet_run_id": detail.run.fleet_run_id,
            "availability": "not_checked; bounded rotation or storage failure may remove supporting output",
            "authority": "supporting controller diagnostics; no Run outcome, permission or cleanup authority",
        },
        "native_payload_retention": "Only native observations from accepted Step results are projected; cancelled/interrupted work without such a result has unavailable journal payload. Cancellation receipts retain physical settlement and cleanup evidence. Supporting diagnostics may retain earlier visible output.",
        "record_authority": "Original journal/Game Tape facts; native summaries remain untrusted claims",
        "remaining_work": "Not independently inferred; inspect canonical Step statuses, why_not and explicitly unverified claims",
    }


def _handoff(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Read the original Run once; render its reusable non-authorizing handoff."""
    detail = _artifact_readback_detail(runtime, args)
    result = run_handoff_projection(detail)
    if args.as_json:
        _print_json(result)
    else:
        print(f"Fleet Run ID: {result['fleet_run_id']}")
        print(f"Plan ID: {result['plan_id'] or 'unknown'}")
        print(f"Status: {result['status']} | Outcome: {result['outcome']}")
        print(f"Game Tape status: {result['game_tape_status']}")
        print(
            f"Issues: {result['issue_count']} (displayed: {len(result['issue_focus'])}, "
            f"hidden: {result['hidden_issue_count']})"
        )
        for focus in result["issue_focus"]:
            identity = focus["step_execution_id"] or focus["repository_execution_id"] or "run"
            print(f"  {focus['scope']}: {identity} | {focus['code'] or 'no code'}")
        if result["why_not"] is not None:
            print(f"Original guidance: {result['why_not']['safe_next_action']}")
        print(
            "Inspect exact evidence using the structured readback argv references "
            "from --json; raw logs may be unavailable."
        )
        _print_artifact_observation(detail.game_tape)
    return 0


def _campaign(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    result = runtime.runs.agent_campaign(args.fleet_run_id)
    if args.as_json:
        _print_json(result)
    else:
        print(f"Campaign: {result['campaign_id']}")
        print(f"Result: {result['result']}")
        for run in result["runs"]:
            print(
                f"  {run['phase']} pass {run['review_pass']}: "
                f"{run['fleet_run_id']} | {run['status']} / {run['outcome']}"
            )
        print(f"Reviews: {len(result['reviews'])} | Findings: {len(result['findings'])}")
        for reason in result["incomplete_reasons"]:
            print(f"Incomplete: {reason}")
        print(
            "Use --json for source-bound findings, adjudications and native verification results."
        )
    return 0


_READ_COMMANDS = {
    "show": _status,
    "status": _status,
    "events": _events,
    "logs": _logs,
    "tape": _tape,
    "steps": _steps,
    "outputs": _outputs,
    "handoff": _handoff,
    "campaign": _campaign,
}


def run_plan_readback(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Dispatch presentation only; do not interpret Run truth or start work."""
    try:
        command = args.runs_command if args.command == "runs" else args.plan_command
        return _READ_COMMANDS[command](runtime, args)
    except FleetDomainError as exc:
        if args.as_json:
            _print_json(
                {
                    "schema": "oh-goon.plan-run-read-error/v1",
                    "fleet_run_id": args.fleet_run_id,
                    "status": "error",
                    "message": str(exc),
                }
            )
        else:
            print(str(exc))
        return 2
