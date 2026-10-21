from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import TypedDict

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_agent_providers import agent_connection_from_args
from oh_goon.cli_plan_definition_commands import (
    _check,
    _diff,
    _explain,
    _inputs,
    _prepare,
    _preview,
    _validate,
)
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.cli_plan_run_readback import run_plan_readback
from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid
from oh_goon.core.models.agent_providers import AgentConnection, ResolvedNativeAgent
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.models.fleet_plan_evidence import (
    FailedPlanStepExecution,
    FailedPlanStepTeardownEvidence,
)
from oh_goon.core.models.game_tape_live_output import GameTapeLiveOutputBatch
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FailedFleetRun, FleetRunDetail
from oh_goon.core.vocabulary import FleetRunOutcome, GameTapeStatus
from oh_goon.services.fleet_plan_resolution_context import AgentConnectionSelection

PlanHandler = Callable[[ApplicationRuntime, argparse.Namespace], int]


class _AgentReviewRepairOptions(TypedDict, total=False):
    review_write_roots: tuple[str, ...]
    agent_connection: AgentConnection
    native_agent: ResolvedNativeAgent
    native_workspace: Path


class _AgentRunSelection(TypedDict, total=False):
    agent_connection: AgentConnectionSelection
    native_agent: ResolvedNativeAgent
    native_workspace: Path


def run_plan_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    if args.command != "plan":
        return None
    handlers: dict[str, PlanHandler] = {
        "validate": _validate,
        "inputs": _inputs,
        "explain": _explain,
        "check": _check,
        "diff": _diff,
        "prepare": _prepare,
        "preview": _preview,
        "run": _run,
        "status": run_plan_readback,
        "events": run_plan_readback,
        "logs": run_plan_readback,
        "tape": run_plan_readback,
        "steps": run_plan_readback,
        "outputs": run_plan_readback,
        "handoff": run_plan_readback,
        "schedule": _schedule,
        "reschedule": _reschedule,
    }
    handler = handlers.get(args.plan_command)
    return handler(runtime, args) if handler is not None else 2


_RUN_RESULT_SCHEMA = "oh-goon.plan-run-result/v1"
_RUN_JSON_PROGRESS: ContextVar[bool] = ContextVar("oh_goon_run_json_progress", default=False)


def _run_result(
    *, fleet_run_id: str, plan_id: str | None, status: str, outcome: str | None = None
) -> dict[str, str | None]:
    """Presentation of original Fleet facts; never a second terminal classifier."""
    return {
        "schema": _RUN_RESULT_SCHEMA,
        "fleet_run_id": fleet_run_id,
        "plan_id": plan_id,
        "status": status,
        "outcome": outcome,
    }


def _run(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    if getattr(args, "as_json", False) and args.repair_handoff:
        print(
            json.dumps(
                {
                    "schema": _RUN_RESULT_SCHEMA,
                    "status": "error",
                    "fleet_run_id": None,
                    "message": "--json and --repair-handoff cannot be combined",
                },
                sort_keys=True,
            )
        )
        return 2
    admitted = None
    as_json = bool(getattr(args, "as_json", False))
    try:
        # Production CLI execution must have an explicitly enrolled Run before
        # reading any Plan, parsing inputs, or invoking native Plan preparation.
        # The Fleet lifecycle owns this fence; it is not a second compiler.
        runtime.plans.require_cli_plan_preparation(args.fleet_run_id)
        # Only after the exact enrolled-Run preflight may caller bytes be read.
        # The frozen target transports the identity; the original Fleet owners
        # reprove it before admission and again on the worker before any work.
        repair_budget = _selected_repair_budget(args)
        native = getattr(args, "_native_agent", None)
        selected_inputs = plan_inputs(args)
        snapshot = getattr(args, "_caller_input_file_snapshot", None)
        source_identity = (
            {"caller_input_file": snapshot.authority()} if snapshot is not None else {}
        )
        if repair_budget is not None:
            review = getattr(args, "review_passes", None) is not None
            review_options: _AgentReviewRepairOptions = {}
            if review:
                review_options = dict(review_write_roots=tuple(args.repair_write_root))
                if native is not None:
                    review_options.update(
                        native_agent=native, native_workspace=args.native_workspace
                    )
                else:
                    review_options["agent_connection"] = agent_connection_from_args(args)
            elif native is not None:
                raise FleetPlanInvalid(
                    "Native repair requires the complete --review-passes campaign"
                )
            result = runtime.plans.repair(
                args.plan,
                budget=repair_budget,
                repository_id=args.repository_ids[0],
                inputs=selected_inputs,
                agent_repository_root=args.agent_repository_root,
                agent_skill_root=args.agent_skill_root,
                allowed_agent_acceptance=tuple(args.allow_agent_acceptance),
                fleet_run_id=args.fleet_run_id,
                lock_path=args.lock_file,
                extensions_path=args.extensions,
                requested_priority=args.priority,
                **review_options,
            )
            return _print_agent_repair_result(result, as_json=as_json)
        native_options: _AgentRunSelection = (
            dict(native_agent=native, native_workspace=args.native_workspace)
            if native is not None
            else dict(agent_connection=lambda: agent_connection_from_args(args))
        )
        admitted = runtime.plans.run(
            args.plan,
            extensions_path=args.extensions,
            repository_ids=set(args.repository_ids) if args.repository_ids else None,
            all_repositories=args.all_repositories,
            fleet_run_id=args.fleet_run_id,
            requested_priority=args.priority,
            inputs=selected_inputs,
            caller_input_origins=getattr(args, "_caller_input_origins", {}),
            **source_identity,
            lock_path=args.lock_file,
            agent_repository_root=args.agent_repository_root,
            agent_skill_root=args.agent_skill_root,
            allowed_agent_acceptance=tuple(args.allow_agent_acceptance or ()),
            **native_options,
        )
        plan_id = admitted.target.plan.plan_id if admitted.target is not None else None
        if not args.wait:
            if as_json:
                print(
                    json.dumps(
                        _run_result(
                            fleet_run_id=admitted.fleet_run_id,
                            plan_id=plan_id,
                            status=admitted.status.value,
                        ),
                        sort_keys=True,
                    )
                )
            else:
                print(f"Fleet Run ID: {admitted.fleet_run_id}")
                print(f"Plan ID: {plan_id or 'unknown'}")
                print(f"Status: {admitted.status.value}")
            return 0
        if not as_json:
            print(f"Fleet Run ID: {admitted.fleet_run_id}", flush=True)
            print("Status: admitted", flush=True)
        # The original observation/waiting owner remains authoritative. JSON
        # mode sends transient progress to stderr so stdout is exactly one
        # machine-readable result, even for a waited failed Run.
        if as_json:
            token = _RUN_JSON_PROGRESS.set(True)
            try:
                detail = _wait_for_terminal_with_progress(
                    runtime, admitted.fleet_run_id, admitted_run=admitted
                )
            finally:
                _RUN_JSON_PROGRESS.reset(token)
        else:
            detail = _wait_for_terminal_with_progress(
                runtime, admitted.fleet_run_id, admitted_run=admitted
            )
    except KeyboardInterrupt:
        if admitted is not None:
            try:
                runtime.runs.cancel(admitted.fleet_run_id)
            except FleetDomainError:
                pass
        if as_json:
            print(
                json.dumps(
                    {
                        "schema": _RUN_RESULT_SCHEMA,
                        "status": "interrupted",
                        "fleet_run_id": admitted.fleet_run_id if admitted is not None else None,
                    },
                    sort_keys=True,
                )
            )
        else:
            print("Plan run interrupted.")
        return 130
    except (FleetDomainError, ValueError) as exc:
        if as_json:
            # Do not assert no effects: a failure can occur after Run creation.
            print(
                json.dumps(
                    {
                        "schema": _RUN_RESULT_SCHEMA,
                        "status": "error",
                        "fleet_run_id": admitted.fleet_run_id if admitted is not None else None,
                        "message": str(exc),
                    },
                    sort_keys=True,
                )
            )
        else:
            print(str(exc))
        return 2
    if as_json:
        print(
            json.dumps(
                _run_result(
                    fleet_run_id=admitted.fleet_run_id,
                    plan_id=admitted.target.plan.plan_id if admitted.target is not None else None,
                    status=detail.run.status.value,
                    outcome=detail.outcome.value,
                ),
                sort_keys=True,
            )
        )
    else:
        print(f"Execution status: {detail.run.status.value}")
        print(f"Outcome: {detail.outcome.value}")
    if detail.outcome is FleetRunOutcome.SUCCESS:
        return 0
    if args.repair_handoff:
        _print_repair_handoff(runtime, detail)
    return 1


def _selected_repair_budget(args) -> AgentRepairBudget | None:
    review_passes = getattr(args, "review_passes", None)
    if review_passes is not None and getattr(args, "repair_attempts", None) is not None:
        raise FleetPlanInvalid("choose --review-passes or --repair-attempts")
    if review_passes is not None and not getattr(args, "repair_write_root", None):
        raise FleetPlanInvalid("review-repair requires at least one explicit --repair-write-root")
    if review_passes is not None:
        from oh_goon.integrations.agent_patch_apply import validate_write_roots

        validate_write_roots(tuple(args.repair_write_root))
    if review_passes is None and getattr(args, "repair_write_root", None):
        raise FleetPlanInvalid("repair write roots require --review-passes")
    if getattr(args, "agent_model_profile", None) or getattr(args, "agent_providers_file", None):
        if (
            not args.standard_local
            or not args.wait
            or args.agent_repository_root is None
            or args.agent_skill_root is None
            or not args.allow_agent_acceptance
            or args.repair_attempts is not None
        ):
            raise FleetPlanInvalid("provider selection requires a standard-local Agent invocation")
    limits = (
        review_passes if review_passes is not None else getattr(args, "repair_attempts", None),
        getattr(args, "repair_timeout", None),
        getattr(args, "repair_step_timeout", None),
    )
    if all(value is None for value in limits):
        return None
    if (
        any(value is None for value in limits)
        or not args.standard_local
        or not args.wait
        or args.all_repositories
        or len(args.repository_ids or ()) != 1
        or getattr(args, "inputs_file", None) is not None
        or args.repair_handoff
        or args.agent_repository_root is None
        or args.agent_skill_root is None
        or not args.allow_agent_acceptance
    ):
        raise FleetPlanInvalid(
            "repair requires all three limits, --standard-local --wait, one repository, "
            "Agent roots and acceptance; inputs-file and repair-handoff are unavailable"
        )
    return AgentRepairBudget(
        max_attempts=limits[0], timeout_seconds=limits[1], step_timeout_seconds=limits[2]
    )


def _print_agent_repair_result(result, *, as_json: bool) -> int:
    receipt = {
        "schema": "oh-goon.agent-repair-result/v1",
        "repair_id": result.repair_id,
        "stop_reason": result.stop_reason.value,
        "test_passed": result.test_passed,
        "converged": result.converged,
        "budget": result.budget.model_dump(mode="json"),
        "message": result.message,
        "admitted_run_ids": list(result.admitted_run_ids),
        "unresolved_run_ids": list(result.unresolved_run_ids),
        "attempts": [
            {
                "fleet_run_id": detail.run.fleet_run_id,
                "status": detail.run.status.value,
                "outcome": detail.outcome.value,
                "game_tape_status": detail.game_tape.status.value,
                "execution_digest": detail.run.target.execution_digest,
            }
            for detail in result.attempts
        ],
    }
    if as_json:
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(f"Repair: {result.repair_id}")
        print(f"Stop reason: {result.stop_reason.value}")
        for attempt in receipt["attempts"]:
            print(f"Run {attempt['fleet_run_id']}: {attempt['status']} / {attempt['outcome']}")
    return (
        0
        if result.test_passed or result.converged
        else 130
        if result.stop_reason.value == "cancelled"
        else 1
    )


_PROGRESS_WAIT_SLICE_SECONDS = 1.0
_PROGRESS_HEARTBEAT_SECONDS = 5.0
_PROGRESS_SPINNER = ("|", "/", "-", "\\")
_PROGRESS_CLEAR_LINE = "\r\x1b[2K"


def _progress_descriptor() -> tuple[int | None, bool]:
    """Return the presentation descriptor and whether it is an interactive terminal."""

    configured = os.environ.get("OH_GOON_PROGRESS_FD")
    if configured is not None:
        try:
            fd = int(configured)
            if fd < 0:
                raise ValueError
            return fd, os.isatty(fd)
        except (OSError, ValueError):
            pass
    try:
        return None, (sys.stderr if _RUN_JSON_PROGRESS.get() else sys.stdout).isatty()
    except (AttributeError, OSError):
        return None, False


def _progress_write(payload: str) -> None:
    fd, _interactive = _progress_descriptor()
    if fd is not None:
        try:
            os.write(fd, payload.encode("utf-8", errors="replace"))
            return
        except OSError:
            pass
    stream = sys.stderr if _RUN_JSON_PROGRESS.get() else sys.stdout
    stream.write(payload)
    stream.flush()


def _progress_print(message: str) -> None:
    """Emit a state-change milestone outside the durable run-log pipeline.

    Milestones are useful in both terminals and captured CI output.  If an interactive
    heartbeat is currently occupying the terminal line, clear it before printing the
    milestone so progress remains readable instead of accumulating spinner frames.
    """

    _fd, interactive = _progress_descriptor()
    prefix = _PROGRESS_CLEAR_LINE if interactive else ""
    _progress_write(f"{prefix}{message}\n")


def _progress_heartbeat(message: str) -> None:
    """Refresh one live terminal line; never serialize time-only pulses to pipes/logs."""

    _fd, interactive = _progress_descriptor()
    if not interactive:
        return
    _progress_write(f"{_PROGRESS_CLEAR_LINE}{message}")


def _progress_clear() -> None:
    """Remove a live spinner line before returning to ordinary terminal output."""

    _fd, interactive = _progress_descriptor()
    if interactive:
        _progress_write(_PROGRESS_CLEAR_LINE)


def _format_progress_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _plan_step_owner_labels(detail) -> dict[str, tuple[int, int, str]]:
    run = getattr(detail, "run", detail)
    target = getattr(run, "target", None)
    plan = target.plan if target is not None and hasattr(target, "plan") else None
    if plan is None:
        return {}
    total = len(plan.imports)
    return {
        step_id: (index, total, item.name)
        for index, item in enumerate(plan.imports, start=1)
        for step_id in item.step_ids
    }


def _activity_markers(text: str) -> tuple[str, ...]:
    prefix = "OH-GOON ACTIVITY:"
    return tuple(
        line[len(prefix) :].strip()
        for line in text.splitlines()
        if line.startswith(prefix) and line[len(prefix) :].strip()
    )


def _emit_observed_step_activity(
    runtime: ApplicationRuntime,
    fleet_run_id: str,
    *,
    after: int,
    owners: dict[str, tuple[int, int, str]],
    seen_steps: set[tuple[str, int]],
    seen_markers: set[tuple[str, int, str]],
) -> tuple[int, bool, str | None]:
    """Render transient activity without turning live output into durable run truth."""

    try:
        batch = runtime.runs.game_tape_live_output(fleet_run_id, after=after)
    except (FleetDomainError, ValueError):
        return after, False, None
    if not batch.available:
        return max(after, batch.next_sequence), False, None
    emitted = False
    current_activity: str | None = None
    for chunk in batch.chunks:
        key = (chunk.step_id, chunk.attempt)
        owner = owners.get(chunk.step_id)
        attempt = f" | attempt {chunk.attempt}" if chunk.attempt > 1 else ""
        if owner is None:
            current_activity = f"Step {chunk.step_id}{attempt}"
        else:
            index, total, goon_name = owner
            current_activity = f"Goon {index}/{total} — {goon_name}{attempt}"
        if key not in seen_steps:
            seen_steps.add(key)
            if owner is None:
                _progress_print(f"Observed activity: Step {chunk.step_id}{attempt}")
            else:
                index, total, goon_name = owner
                _progress_print(
                    f"Observed activity: Goon {index}/{total} — {goon_name} | "
                    f"Step: {chunk.step_id}{attempt}"
                )
            emitted = True
        for marker in _activity_markers(getattr(chunk, "text", "")):
            marker_key = (chunk.step_id, chunk.attempt, marker)
            if marker_key in seen_markers:
                continue
            seen_markers.add(marker_key)
            if owner is None:
                _progress_print(f"Activity detail: Step {chunk.step_id} — {marker}{attempt}")
            else:
                index, total, goon_name = owner
                _progress_print(
                    f"Activity detail: Goon {index}/{total} — {goon_name} | {marker}{attempt}"
                )
            emitted = True
    return max(after, batch.next_sequence), emitted, current_activity


def _emit_transient_run_activity(
    runtime: ApplicationRuntime,
    fleet_run_id: str,
    *,
    after: int,
) -> tuple[int, bool, str | None]:
    """Render bounded preparation hints before Step stdout/stderr exists."""

    try:
        reader = getattr(runtime.runs, "transient_run_activity", None)
        if reader is None:
            return after, False, None
        next_sequence, activities = reader(fleet_run_id, after=after)
    except (FleetDomainError, ValueError):
        return after, False, None
    current: str | None = None
    for _sequence, message in activities:
        current = message
        _progress_print(f"Activity: {message}")
    return max(after, next_sequence), bool(activities), current


def _wait_for_terminal_with_progress(
    runtime: ApplicationRuntime,
    fleet_run_id: str,
    *,
    admitted_run=None,
) -> FleetRunDetail:
    """Wait for terminal ownership without polling SQLite while the worker is active."""

    if admitted_run is not None:
        owners = _plan_step_owner_labels(admitted_run)
    else:
        try:
            owners = _plan_step_owner_labels(runtime.runs.get(fleet_run_id))
        except (AttributeError, FleetDomainError, ValueError):
            owners = {}
    started = time.monotonic()
    last_heartbeat = started
    heartbeat_index = 0
    current_activity: str | None = None
    _progress_heartbeat("[|] Certification working · 00:00:00 · waiting for first activity")
    live_sequence = 0
    activity_sequence = 0
    seen_steps: set[tuple[str, int]] = set()
    seen_markers: set[tuple[str, int, str]] = set()
    while True:
        try:
            detail = runtime.runs.wait_for_terminal(
                fleet_run_id, timeout=_PROGRESS_WAIT_SLICE_SECONDS
            )
            _progress_clear()
            return detail
        except TimeoutError:
            activity_sequence, activity_emitted, run_activity = _emit_transient_run_activity(
                runtime,
                fleet_run_id,
                after=activity_sequence,
            )
            if run_activity is not None:
                current_activity = run_activity
            live_sequence, emitted, observed_activity = _emit_observed_step_activity(
                runtime,
                fleet_run_id,
                after=live_sequence,
                owners=owners,
                seen_steps=seen_steps,
                seen_markers=seen_markers,
            )
            if observed_activity is not None:
                current_activity = observed_activity
            now = time.monotonic()
            if emitted or activity_emitted:
                last_heartbeat = now
            elif now - last_heartbeat >= _PROGRESS_HEARTBEAT_SECONDS:
                heartbeat_index += 1
                spinner = _PROGRESS_SPINNER[heartbeat_index % len(_PROGRESS_SPINNER)]
                activity = current_activity or "waiting for first activity"
                _progress_heartbeat(
                    f"[{spinner}] Certification working · "
                    f"{_format_progress_elapsed(now - started)} · {activity}"
                )
                last_heartbeat = now


_REPAIR_OUTPUT_MAX_LINES = 40
_REPAIR_OUTPUT_MAX_CHARS = 8_000


def _repair_output_tail(
    batch: GameTapeLiveOutputBatch, step_id: str
) -> tuple[tuple[str, ...], bool]:
    """Return a bounded tail from the failed Step's latest retained attempt.

    Game Tape live output is process-local/transient and has already passed the
    execution secret redactor.  Keep the repair handoff bounded rather than
    turning raw output into a second durable evidence store.
    """

    chunks = [chunk for chunk in batch.chunks if chunk.step_id == step_id]
    if not chunks:
        return (), False
    latest_attempt = max(chunk.attempt for chunk in chunks)
    chunks = [chunk for chunk in chunks if chunk.attempt == latest_attempt]
    lines: list[str] = []
    for chunk in chunks:
        values = chunk.text.splitlines() or ([chunk.text] if chunk.text else [])
        lines.extend(f"[{chunk.stream}] {line}" for line in values if line)
    truncated = len(lines) > _REPAIR_OUTPUT_MAX_LINES
    lines = lines[-_REPAIR_OUTPUT_MAX_LINES:]
    while lines and sum(len(line) + 1 for line in lines) > _REPAIR_OUTPUT_MAX_CHARS:
        lines.pop(0)
        truncated = True
    return tuple(lines), truncated


def _game_tape_failed_step(detail: FleetRunDetail):
    """Best durable fallback when direct operation evidence is incomplete."""

    try:
        tape = detail.game_tape
    except (FleetDomainError, ValueError):
        return None
    for repository in tape.repositories:
        for step in repository.steps:
            if step.status is GameTapeStatus.FAILED:
                return step
    return None


def _repair_failed_steps(detail: FleetRunDetail):
    failed_step = None
    teardown_step = None
    for report in detail.operations:
        evidence = report.evidence
        if not isinstance(evidence, FleetPlanOperationEvidence):
            continue
        failed_step = next(
            (step for step in evidence.plan.steps if isinstance(step, FailedPlanStepExecution)),
            None,
        )
        teardown_step = next(
            (
                step
                for step in evidence.plan.steps
                if isinstance(step.teardown, FailedPlanStepTeardownEvidence)
            ),
            None,
        )
        if failed_step is not None or teardown_step is not None:
            break
    return failed_step, teardown_step


def _repair_plan(detail: FleetRunDetail):
    target = detail.run.target
    return target.plan if target is not None and hasattr(target, "plan") else None


def _print_repair_run_header(detail: FleetRunDetail, plan) -> None:
    run = detail.run
    print("\nCERTIFICATION PLAN FAILED")
    print(f"Fleet Run ID: {run.fleet_run_id}")
    if isinstance(run, FailedFleetRun):
        print(f"Failure: {run.failure.code} stage={run.failure.stage.value}")
        print(f"Reason: {run.failure.message}")
    if plan is not None:
        print(f"Plan ID: {plan.plan_id}")


def _print_incomplete_repair_handoff(run_id: str) -> None:
    print("Failure evidence: INCOMPLETE")
    print("The terminal run is non-successful but no durable failed Step could be projected.")
    print("Do not treat this as a complete repair handoff or guess a command from transient logs.")
    print(f"Inspect evidence: make certification-explain OH_GOON_RUN_ID={run_id}")


def _repair_step_owner(plan, step_id: str):
    if plan is None:
        return "unknown", None
    owner = next((item for item in plan.imports if step_id in item.step_ids), None)
    if owner is None:
        return "unknown", None
    index = next(
        (position for position, item in enumerate(plan.imports, start=1) if item is owner),
        None,
    )
    return owner.name, index


def _repair_step_details(plan, failed_step, teardown_step, projected_step):
    durable_step = failed_step if failed_step is not None else teardown_step
    step_id = durable_step.step_id if durable_step is not None else projected_step.step_id
    goon_name, goon_index = _repair_step_owner(plan, step_id)
    argv = durable_step.argv if durable_step is not None else projected_step.command
    exit_code = (
        getattr(durable_step, "returncode", None)
        if durable_step is not None
        else projected_step.exit_code
    )
    teardown_failure = (
        durable_step.teardown
        if durable_step is not None
        and isinstance(durable_step.teardown, FailedPlanStepTeardownEvidence)
        else None
    )
    command = " ".join(argv) if argv else "unknown"
    total = len(plan.imports) if plan is not None else None
    ordinal = f"{goon_index}/{total}" if goon_index is not None and total is not None else "unknown"
    return step_id, goon_name, ordinal, command, exit_code, teardown_failure


def _print_repair_step_failure(
    *,
    step_id: str,
    goon_name: str,
    ordinal: str,
    command: str,
    exit_code,
    teardown_failure,
    failed_step,
) -> None:
    print(f"Goon: {ordinal} — {goon_name}")
    print(f"Step: {step_id}")
    if teardown_failure is not None and failed_step is None:
        print("Failure evidence: COMPLETE (durable step teardown)")
        print("Failure boundary: step_teardown")
        print(f"Teardown reason: {teardown_failure.reason_code}")
        print(f"Command exit code: {exit_code if exit_code is not None else 'unknown'}")
        print(f"Canonical step command: {command}")
        return
    print(f"Exit code: {exit_code if exit_code is not None else 'unknown'}")
    print(f"Canonical repair command: {command}")


def _print_repair_live_output(
    runtime: ApplicationRuntime,
    *,
    run_id: str,
    step_id: str,
    teardown_failure,
) -> None:
    if teardown_failure is not None:
        return
    try:
        live_output = runtime.runs.game_tape_live_output(run_id)
    except (FleetDomainError, ValueError):
        return
    if live_output is None or not live_output.available:
        return
    lines, truncated = _repair_output_tail(live_output, step_id)
    if not lines:
        return
    print("\nFailure output (secret-redacted transient tail):")
    for line in lines:
        print(f"  {line}")
    if truncated:
        print("  ... earlier failure output omitted ...")


def _print_repair_workflow(
    *,
    command: str,
    teardown_only: bool,
) -> None:
    print("\nRepair workflow:")
    print("1. Give this failure block to your coding agent.")
    print("2. Fix only the observed failure; do not weaken tests, locks, coverage, or timeouts.")
    if teardown_only:
        print(
            "3. Inspect the durable teardown reason above; do not treat the successful step command as the failing operation."
        )
        print("4. Normal recovery and final proof: make certify")
        return
    print(f"3. Focused diagnostic command: {command}")
    print("4. Normal recovery and final proof: make certify")


def _print_repair_handoff(runtime: ApplicationRuntime, detail: FleetRunDetail) -> None:
    run = detail.run
    plan = _repair_plan(detail)
    _print_repair_run_header(detail, plan)
    failed_step, teardown_step = _repair_failed_steps(detail)
    projected_step = None
    if failed_step is None and teardown_step is None:
        projected_step = _game_tape_failed_step(detail)
    if failed_step is None and teardown_step is None and projected_step is None:
        _print_incomplete_repair_handoff(run.fleet_run_id)
        return
    step_id, goon_name, ordinal, command, exit_code, teardown_failure = _repair_step_details(
        plan, failed_step, teardown_step, projected_step
    )
    _print_repair_step_failure(
        step_id=step_id,
        goon_name=goon_name,
        ordinal=ordinal,
        command=command,
        exit_code=exit_code,
        teardown_failure=teardown_failure,
        failed_step=failed_step,
    )
    _print_repair_live_output(
        runtime,
        run_id=run.fleet_run_id,
        step_id=step_id,
        teardown_failure=teardown_failure,
    )
    print(f"Inspect evidence: make certification-explain OH_GOON_RUN_ID={run.fleet_run_id}")
    _print_repair_workflow(
        command=command,
        teardown_only=teardown_failure is not None and failed_step is None,
    )


def _schedule(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        selected_inputs = plan_inputs(args)
        snapshot = getattr(args, "_caller_input_file_snapshot", None)
        source_identity = (
            {"caller_input_file": snapshot.authority()} if snapshot is not None else {}
        )
        definition = runtime.plans.schedule(
            args.plan,
            schedule_id=args.schedule_id,
            extensions_path=args.extensions,
            repository_ids=set(args.repository_ids),
            name=args.name,
            interval_minutes=args.interval_minutes,
            inputs=selected_inputs,
            caller_input_origins=getattr(args, "_caller_input_origins", {}),
            **source_identity,
        )
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    print(f"Schedule ID: {definition.schedule_id}")
    print(f"Plan ID: {definition.target.plan.plan_id}")
    print(f"Repositories: {len(definition.repository_ids)}")
    print("Trigger: interval schedule")
    return 0


def _reschedule(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        selected_inputs = plan_inputs(args)
        snapshot = getattr(args, "_caller_input_file_snapshot", None)
        source_identity = (
            {"caller_input_file": snapshot.authority()} if snapshot is not None else {}
        )
        definition = runtime.plans.reschedule(
            args.plan,
            schedule_id=args.schedule_id,
            extensions_path=args.extensions,
            repository_ids=set(args.repository_ids),
            name=args.name,
            interval_minutes=args.interval_minutes,
            inputs=selected_inputs,
            caller_input_origins=getattr(args, "_caller_input_origins", {}),
            **source_identity,
        )
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    print(f"Schedule ID: {definition.schedule_id}")
    print(f"Record version: {definition.record_version}")
    print(f"Plan ID: {definition.target.plan.plan_id}")
    return 0
