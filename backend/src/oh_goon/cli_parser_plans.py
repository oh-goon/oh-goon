from __future__ import annotations

import argparse
from pathlib import Path

from oh_goon.core.models.scheduling import SchedulingPriority


def add_plan_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    plan = subparsers.add_parser("plan", help="Validate, run, or migrate portable Fleet plans")
    plan_subparsers = plan.add_subparsers(dest="plan_command")
    plan_validate = plan_subparsers.add_parser(
        "validate", help="Resolve YAML/TOML without starting SQLite or executing work"
    )
    plan_validate.add_argument("plan", type=Path)
    plan_validate.add_argument("--extensions", type=Path)
    plan_validate.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_validate.add_argument("--inputs-file", type=Path)
    plan_validate.add_argument("--json", action="store_true", dest="as_json")
    plan_validate.add_argument(
        "--diagnostics",
        action="store_true",
        help="Report safe structured validation failures (with --json)",
    )
    plan_inputs_inspect = plan_subparsers.add_parser(
        "inputs",
        help="Show available inputs, defaults and owning Goons without running work",
    )
    plan_inputs_inspect.add_argument("plan", type=Path)
    plan_inputs_inspect.add_argument("--json", action="store_true", dest="as_json")
    plan_inputs_inspect.add_argument(
        "--source-closure",
        action="store_true",
        help="Inspect repository/Skill closure; may prepare declared Git sources (no Run)",
    )
    plan_inputs_inspect.add_argument(
        "--input", action="append", dest="plan_inputs", metavar="NAME=VALUE"
    )
    plan_inputs_inspect.add_argument("--expect-sha256")
    plan_inputs_inspect.add_argument("--repository-root", type=Path)
    plan_inputs_inspect.add_argument("--skill-root", type=Path)
    plan_inputs_inspect.add_argument("--allow-acceptance", action="append")
    plan_inputs_inspect.add_argument("--skill-alias", action="append", metavar="NAME=LOCAL_REF")
    _add_plan_diagnostic_commands(plan_subparsers)
    plan_prepare = plan_subparsers.add_parser(
        "prepare",
        help="Prepare pinned extension and built-in tool runtimes before validation/execution",
    )
    plan_prepare.add_argument("plan", type=Path)
    plan_prepare.add_argument("--extensions", type=Path)
    plan_prepare.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_prepare.add_argument("--inputs-file", type=Path)
    plan_preview = plan_subparsers.add_parser(
        "preview", help="Preview resolved step conditions for one repository without effects"
    )
    plan_preview.add_argument("plan", type=Path)
    plan_preview.add_argument("repository", type=Path)
    plan_preview.add_argument("--extensions", type=Path)
    plan_preview.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_preview.add_argument("--inputs-file", type=Path)
    plan_preview.add_argument("--json", action="store_true", dest="as_json")
    plan_run = plan_subparsers.add_parser(
        "run", help="Resolve a plan once and execute it through Fleet"
    )
    plan_run.add_argument("plan", type=Path)
    plan_run.add_argument("--extensions", type=Path)
    plan_run.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_run.add_argument("--inputs-file", type=Path)
    plan_run_scope = plan_run.add_mutually_exclusive_group(required=True)
    plan_run_scope.add_argument("--repository-id", action="append", dest="repository_ids")
    plan_run_scope.add_argument("--all-repositories", action="store_true")
    plan_run.add_argument(
        "--fleet-run-id",
        help="Exact deployment-enrolled Run identity (required for production Fleet Plan execution)",
    )
    plan_run.add_argument("--priority", type=SchedulingPriority, default=SchedulingPriority.NORMAL)
    plan_run.add_argument(
        "--lock-file",
        type=Path,
        help="Explicit goon.lock path to verify; defaults to goon.lock beside the Plan",
    )
    plan_run.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit one machine-readable Run result"
    )
    plan_run.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Wait for completion; --no-wait is available for deployed strict execution",
    )
    profile = plan_run.add_mutually_exclusive_group()
    profile.add_argument(
        "--standard-local",
        action="store_const",
        const=True,
        default=None,
        dest="standard_local",
        help="Run a trusted repository on this host (the ordinary default)",
    )
    profile.add_argument(
        "--strict",
        action="store_const",
        const=False,
        dest="standard_local",
        help="Require the configured isolated/deployment execution profile",
    )
    plan_run.add_argument(
        "--agent-repository-root",
        type=Path,
        help="Authorized local root for frozen Agent repository input",
    )
    plan_run.add_argument(
        "--agent-skill-root", type=Path, help="Authorized local root for frozen Agent Skill input"
    )
    plan_run.add_argument(
        "--allow-agent-acceptance",
        action="append",
        help="Explicit accepted Agent profile; standard-local only",
    )
    plan_run.add_argument(
        "--repair-handoff",
        action="store_true",
        help="Print a self-contained repair handoff when a waited Plan run fails",
    )
    plan_run.add_argument(
        "--repair-attempts", type=int, help="Authorize a bounded test/propose/apply/retest loop"
    )
    plan_run.add_argument(
        "--review-passes",
        type=int,
        help="Bound review passes, reserving a fresh review after every repair",
    )
    plan_run.add_argument(
        "--repair-write-root",
        action="append",
        help="Explicit repository-relative repair scope; repeat for multiple roots",
    )
    plan_run.add_argument(
        "--agent-model-profile",
        help="Provider model profile; defaults to the configured default_model",
    )
    plan_run.add_argument(
        "--agent-providers-file", type=Path, help="Explicit provider configuration file"
    )
    from oh_goon.cli_ai import add_native_plan_arguments

    add_native_plan_arguments(plan_run)
    plan_run.add_argument(
        "--repair-timeout",
        type=float,
        help="Total repair deadline in seconds; required with --repair-attempts",
    )
    plan_run.add_argument(
        "--repair-step-timeout",
        type=float,
        help="Per-Step repair deadline in seconds; required with --repair-attempts",
    )
    _add_run_readback_commands(plan_subparsers)
    _add_plan_schedule_commands(plan_subparsers)


def _add_plan_diagnostic_commands(
    plan_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    plan_explain = plan_subparsers.add_parser(
        "explain", help="Explain how ordered Goons compile into the immutable resolved plan"
    )
    plan_explain.add_argument("plan", type=Path)
    plan_explain.add_argument("--extensions", type=Path)
    plan_explain.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_explain.add_argument("--inputs-file", type=Path)
    plan_explain.add_argument("--json", action="store_true", dest="as_json")
    plan_explain.add_argument(
        "--diagnostics",
        action="store_true",
        help="Report safe structured validation failures (with --json)",
    )
    plan_explain.add_argument(
        "--routes",
        action="store_true",
        help="Show exact resolved input recipient occurrence routes (v1)",
    )

    plan_check = plan_subparsers.add_parser(
        "check", help="Statically check Plan/Goon contracts and fully resolve a single root Plan"
    )
    plan_check.add_argument("root", type=Path)
    plan_check.add_argument("--extensions", type=Path)
    plan_check.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_check.add_argument("--inputs-file", type=Path)
    plan_check.add_argument("--json", action="store_true", dest="as_json")

    plan_diff = plan_subparsers.add_parser(
        "diff", help="Compare execution meaning after both YAML plans are fully resolved"
    )
    plan_diff.add_argument("before", type=Path)
    plan_diff.add_argument("after", type=Path)
    plan_diff.add_argument("--extensions", type=Path)
    plan_diff.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_diff.add_argument("--inputs-file", type=Path)
    plan_diff.add_argument("--json", action="store_true", dest="as_json")
    plan_diff.add_argument(
        "--exit-code",
        action="store_true",
        help="Exit 1 if canonical resolved execution meaning changed, 0 if unchanged, 2 if invalid",
    )


def _add_plan_schedule_commands(
    plan_subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    plan_schedule = plan_subparsers.add_parser(
        "schedule", help="Schedule one resolved Plan for one or more explicit repositories"
    )
    plan_schedule.add_argument("plan", type=Path)
    plan_schedule.add_argument("--extensions", type=Path)
    plan_schedule.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    plan_schedule.add_argument("--inputs-file", type=Path)
    plan_schedule.add_argument("--schedule-id", required=True)
    plan_schedule.add_argument("--name")
    plan_schedule.add_argument(
        "--repository-id", action="append", dest="repository_ids", required=True
    )
    plan_schedule.add_argument("--interval-minutes", type=int, required=True)
    plan_reschedule = plan_subparsers.add_parser(
        "reschedule", help="Update a Schedule target set onto a newly resolved Plan"
    )
    plan_reschedule.add_argument("plan", type=Path)
    plan_reschedule.add_argument("--extensions", type=Path)
    plan_reschedule.add_argument(
        "--input", action="append", dest="plan_inputs", metavar="NAME=VALUE"
    )
    plan_reschedule.add_argument("--inputs-file", type=Path)
    plan_reschedule.add_argument("--schedule-id", required=True)
    plan_reschedule.add_argument("--name")
    plan_reschedule.add_argument(
        "--repository-id", action="append", dest="repository_ids", required=True
    )
    plan_reschedule.add_argument("--interval-minutes", type=int, required=True)


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
    ):
        viewer = plan_subparsers.add_parser(command, help=help_text)
        viewer.add_argument("fleet_run_id")
        viewer.add_argument("--json", action="store_true", dest="as_json")
