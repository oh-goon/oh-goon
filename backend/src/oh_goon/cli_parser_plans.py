from __future__ import annotations

import argparse
from pathlib import Path

from oh_goon.core.models.game_tape import (
    MAX_ARTIFACT_OBSERVATION_BYTES,
    MAX_ARTIFACT_OBSERVATION_COUNT,
    MAX_ARTIFACT_OBSERVATION_SECONDS,
)


def _artifact_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number") from None
    if not 0 <= parsed <= MAX_ARTIFACT_OBSERVATION_COUNT:
        raise argparse.ArgumentTypeError("artifact count is outside the supported bounds")
    return parsed


def _artifact_bytes(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number of bytes") from None
    if not 0 <= parsed <= MAX_ARTIFACT_OBSERVATION_BYTES:
        raise argparse.ArgumentTypeError("artifact byte budget is outside the supported bounds")
    return parsed


def _artifact_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a number of seconds") from None
    if not 0 < parsed <= MAX_ARTIFACT_OBSERVATION_SECONDS:
        raise argparse.ArgumentTypeError("artifact time budget is outside the supported bounds")
    return parsed


def add_workflow_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    workflow = subparsers.add_parser(
        "workflow", help="Inspect, validate or explicitly prepare one workflow"
    )
    commands = workflow.add_subparsers(dest="workflow_command", required=True)
    for name in ("inspect", "validate"):
        command = commands.add_parser(
            name,
            help="Read selected sources without execution; network only with --acquire",
            description="Read selected source contracts and existing scope without initializing runtime state or writing the workflow. --acquire explicitly permits native Git acquisition into its owned cache.",
        )
        command.add_argument("configuration", type=Path)
        command.add_argument("--json", action="store_true", dest="as_json")
        command.add_argument(
            "--acquire",
            action="store_true",
            help="Explicitly acquire selected Git source to inspect its contract without locking or execution",
        )
    prepare = commands.add_parser(
        "prepare",
        help="Acquire selected sources and write the lock; registration only with --register-target",
        description="Acquire the explicitly selected sources and write goon.lock through its original owner. Existing pins stay fixed unless --refresh selects their kind. --register-target may persist only the exact selected repository.",
    )
    prepare.add_argument("configuration", type=Path)
    prepare.add_argument("--json", action="store_true", dest="as_json")
    prepare.add_argument("--no-input", action="store_true")
    prepare.add_argument(
        "--register-target",
        action="store_true",
        help="Explicitly register only the selected repository before preparation",
    )
    prepare.add_argument(
        "--refresh",
        action="append",
        default=[],
        choices=("goons", "applications", "skills"),
        help="Explicitly refresh only the selected source pin kind; repeat to combine",
    )
    run = subparsers.add_parser("run", help="Execute one locked run.yml through Fleet")
    run.add_argument("configuration", type=Path)
    run.add_argument("--json", action="store_true", dest="as_json")
    run.add_argument("--no-input", action="store_true")
    run.add_argument(
        "--yes",
        action="store_true",
        help="Accept presentation only; grants no execution or repair authority",
    )
    run.add_argument("--_resource-handoff", help=argparse.SUPPRESS)
    run.add_argument(
        "--fleet-run-id",
        help="Exact fresh Run identity enrolled by the original in-process deployment owner",
    )
    run.add_argument(
        "--authorize-repair",
        action="store_true",
        help="Authorize this bounded repair Run using the selected explicit scope",
    )
    runs = subparsers.add_parser(
        "runs", help="Read retained Run and Game Tape evidence by exact Run ID"
    )
    _add_run_readback_commands(runs.add_subparsers(dest="runs_command", required=True))


def add_plan_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    plan = subparsers.add_parser(
        "plan", help="Compare authored sources or explicitly schedule work"
    )
    plan_commands = plan.add_subparsers(dest="plan_command", required=True)
    check = plan_commands.add_parser(
        "check", help="Statically check source packages without running work"
    )
    check.add_argument("root", type=Path)
    diff = plan_commands.add_parser("diff", help="Compare two authored source graphs")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--exit-code", action="store_true")
    for command in (check, diff):
        command.add_argument("--json", action="store_true", dest="as_json")
        command.set_defaults(extensions=None, plan_inputs=None, inputs_file=None)
    for name in ("schedule", "reschedule"):
        command = plan_commands.add_parser(
            name, help="Schedule the selected run.yml target with the original scheduler"
        )
        command.add_argument("configuration", type=Path)
        command.add_argument("--schedule-id", required=True)
        command.add_argument("--name")
        command.add_argument("--interval-minutes", type=int, required=True)


def _nonnegative_cursor(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--after requires a nonnegative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("--after requires a nonnegative integer")
    return parsed


def _event_page_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--limit requires an integer from 1 to 100") from exc
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("--limit requires an integer from 1 to 100")
    return parsed


def _add_run_readback_commands(
    plan_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """One exact Run identity; original Fleet owner supplies all projected facts."""
    status = plan_subparsers.add_parser(
        "show", help="Read one durable Fleet Run projection by exact Run ID"
    )
    status.add_argument("fleet_run_id")
    status.add_argument("--json", action="store_true", dest="as_json")
    events = plan_subparsers.add_parser(
        "events", help="Read original journal events using the original sequence cursor"
    )
    events.add_argument("fleet_run_id")
    events.add_argument("--after", type=_nonnegative_cursor, default=0)
    events.add_argument(
        "--limit",
        type=_event_page_limit,
        default=100,
        help="Maximum durable events per page (1–100; default 100)",
    )
    events.add_argument("--json", action="store_true", dest="as_json")
    logs = plan_subparsers.add_parser(
        "logs", help="Read retained transient Game Tape output (not durable Run truth)"
    )
    logs.add_argument("fleet_run_id")
    logs.add_argument("--after", type=_nonnegative_cursor, default=0)
    logs.add_argument("--json", action="store_true", dest="as_json")
    export = plan_subparsers.add_parser(
        "export-artifact", help="Export one declared retained file from a recorded Run"
    )
    export.add_argument("fleet_run_id")
    export.add_argument("--step", dest="step_id", required=True, help="Exact recorded Step ID")
    export.add_argument(
        "--path", dest="artifact_path", required=True, help="Exact declared artifact path"
    )
    export.add_argument("--output", type=Path, required=True, help="New local file to create")
    export.add_argument(
        "--repository-id", help="Select one recorded repository when the Step ID is ambiguous"
    )
    export.add_argument("--json", action="store_true", dest="as_json")
    for command, help_text in (
        ("tape", "Read the original archived Game Tape projection for one Fleet Run"),
        ("steps", "Navigate original Game Tape repository and Step identities"),
        ("outputs", "Inspect original Game Tape Step output and artifact evidence"),
        ("handoff", "Read bounded original Run and Game Tape completion/failure facts"),
        ("campaign", "Inspect retained intent and results by campaign ID or linked Run ID"),
    ):
        viewer = plan_subparsers.add_parser(command, help=help_text)
        viewer.add_argument(
            "fleet_run_id", metavar="CAMPAIGN_OR_RUN_ID" if command == "campaign" else None
        )
        viewer.add_argument("--json", action="store_true", dest="as_json")
        if command in {"outputs", "tape", "handoff"}:
            viewer.add_argument(
                "--observe-artifacts",
                action="store_true",
                help="Explicitly verify retained bytes under bounded read leases; history stays unchanged",
            )
            viewer.add_argument("--artifact-limit", type=_artifact_limit)
            viewer.add_argument("--artifact-byte-budget", type=_artifact_bytes)
            viewer.add_argument("--artifact-time-budget-seconds", type=_artifact_seconds)
