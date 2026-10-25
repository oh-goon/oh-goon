from __future__ import annotations

import argparse
from pathlib import Path


def add_plan_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    config = subparsers.add_parser("config", help="Inspect or validate one selected run.yml")
    commands = config.add_subparsers(dest="config_command", required=True)
    for name in ("inspect", "validate"):
        command = commands.add_parser(
            name, help="Read the canonical configuration without execution"
        )
        command.add_argument("configuration", type=Path)
        command.add_argument("--json", action="store_true", dest="as_json")
    run = subparsers.add_parser("run", help="Execute one locked run.yml through Fleet")
    run.add_argument("configuration", type=Path)
    run.add_argument("--json", action="store_true", dest="as_json")
    run.add_argument(
        "--fleet-run-id",
        help="Exact fresh Run identity enrolled by the original in-process deployment owner",
    )
    run.add_argument(
        "--authorize-repair",
        action="store_true",
        help="Authorize this bounded repair Run using the selected explicit scope",
    )
    lock = subparsers.add_parser(
        "lock", help="Freeze one run.yml with the original execution-lock owner"
    )
    lock.add_argument("configuration", type=Path)
    lock.add_argument(
        "--update-skills", action="store_true", help="Explicitly refresh declared Skill pins"
    )
    plan = subparsers.add_parser("plan", help="Inspect recorded Runs or compare authored sources")
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
    _add_run_readback_commands(plan_commands)


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
        "status", help="Read one durable Fleet Run projection by exact Run ID"
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
