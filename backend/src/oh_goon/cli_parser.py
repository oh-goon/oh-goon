from __future__ import annotations

import argparse
from pathlib import Path

from oh_goon import __version__
from oh_goon.cli_parser_goons import add_goon_commands
from oh_goon.cli_parser_plans import add_plan_commands
from oh_goon.core import engine_policy_defaults as policy_defaults
from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.capability_registry import capability_definitions
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.core.vocabulary import CommandSafety, OperationAction
from oh_goon.repository_tooling import _self_certification_plan_units as self_certification_units


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="oh-goon", description="oh-goon local fleet maintenance and strict CI/CD"
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command")

    _add_agent_commands(subparsers)
    _add_local_app_commands(subparsers)
    _add_local_state_commands(subparsers)
    _add_storage_commands(subparsers)
    _add_enforcer_commands(subparsers)
    _add_league_commands(subparsers)
    add_plan_commands(subparsers)
    add_goon_commands(subparsers)
    _add_direct_repository_commands(subparsers)
    _add_developer_commands(subparsers)
    _add_self_commands(subparsers)
    return parser


def _add_agent_commands(subparsers):
    """Agent data operations and separately configured model connections."""
    agent = subparsers.add_parser(
        "agent", help="Agent data operations and model provider connections"
    )
    commands = agent.add_subparsers(dest="agent_command", required=True)
    from oh_goon.cli_agent_providers import add_provider_commands

    add_provider_commands(commands)
    apply = commands.add_parser(
        "apply", help="Apply one frozen source-bound replacement to the selected workspace"
    )
    from oh_goon.integrations.agent_patch_apply import add_apply_arguments

    add_apply_arguments(apply)


def _add_local_app_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    subparsers.add_parser(
        "start",
        help="Start the complete local app from the bundled UI; no Node/npm runtime required",
    )
    status = subparsers.add_parser(
        "status", help="Show the exact current launch bound to this workspace"
    )
    status.add_argument("--json", action="store_true", dest="as_json")
    subparsers.add_parser(
        "open", help="Open the exact observed ready endpoint for this workspace launch"
    )
    logs = subparsers.add_parser(
        "logs", help="Show safe structured Launch Events for this workspace launch"
    )
    logs.add_argument("--json", action="store_true", dest="as_json")
    subparsers.add_parser(
        "stop", help="Request stop from the exact birth-proven owner of this workspace launch"
    )
    serve = subparsers.add_parser("serve", help="Start the local FastAPI server")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        choices=("127.0.0.1", "localhost", "::1"),
        help="Loopback host only; oh-goon never binds to a public interface.",
    )
    serve.add_argument("--port", type=int, default=8787)
    subparsers.add_parser("doctor", help="Check required and optional local tools")


def _add_local_state_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    local_state = subparsers.add_parser(
        "local-state", help="Inspect or reset the disposable pre-live local database"
    )
    commands = local_state.add_subparsers(dest="local_state_command", required=True)
    commands.add_parser("path", help="Print the exact configured local database path")
    commands.add_parser("diagnostics-path", help="Print the exact passive diagnostic JSONL path")
    commands.add_parser("check", help="Check whether local state matches this build")
    commands.add_parser(
        "prelaunch-check",
        help="Check schema/integrity plus SQLite write-lock/storage semantics before app launch",
    )
    reset = commands.add_parser(
        "reset", help="Delete only the configured local database and its SQLite sidecars"
    )
    reset.add_argument(
        "--yes", action="store_true", help="Confirm deletion without an interactive prompt"
    )


def _add_storage_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    storage = subparsers.add_parser(
        "storage", help="Inspect local storage ownership, retention, and safe cleanup"
    )
    commands = storage.add_subparsers(dest="storage_command", required=True)
    status = commands.add_parser(
        "status", help="Show canonical local storage owners and retention policy"
    )
    status.add_argument(
        "--measure", action="store_true", help="Walk declared paths and report current byte usage"
    )
    status.add_argument("--json", action="store_true", dest="as_json")
    prune = commands.add_parser(
        "prune", help="Prune only storage whose canonical owner proves disposal is safe"
    )
    prune.add_argument(
        "--yes", action="store_true", help="Confirm the bounded owner-driven cleanup"
    )
    prune.add_argument("--json", action="store_true", dest="as_json")


def _add_direct_repository_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    lock = subparsers.add_parser(
        "lock", help="Generate deterministic goon.lock for one resolved plan.yml or goon.yml"
    )
    lock.add_argument("plan", type=Path, help="Plan/Goon entrypoint to resolve")
    lock.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    lock.add_argument("--inputs-file", type=Path)
    lock.add_argument("--agent-repository-root", type=Path)
    lock.add_argument("--agent-skill-root", type=Path)
    lock.add_argument("--allow-agent-acceptance", action="append")
    lock.add_argument("--agent-model-profile")
    lock.add_argument("--agent-providers-file", type=Path)
    lock.add_argument(
        "--update-skills",
        action="store_true",
        help="Resolve declared Skill branches/tags again and replace their locked commits",
    )
    lock.add_argument(
        "--output", type=Path, help="Output path; defaults to goon.lock beside the entrypoint"
    )

    run = subparsers.add_parser(
        "run", help="Run one canonical operation directly on one repository"
    )
    run.add_argument(
        "action",
        choices=[
            definition.action.value
            for definition in capability_definitions()
            if definition.direct_supported
        ],
    )
    run.add_argument(
        "repository",
        type=Path,
        help="Repository path; effectful direct commands never infer the current directory",
    )
    run.add_argument("--json", action="store_true", dest="as_json")
    run.add_argument("--operation-id", help="Stable Direct command identity for safe retry")

    exclude = subparsers.add_parser(
        "exclude", help="Persistently exclude a repository from fleet runs"
    )
    exclude.add_argument("repository", type=Path)
    exclude.add_argument("--reason", default=policy_defaults.default_exclusion_reason())

    include = subparsers.add_parser(
        "include", help="Include a previously excluded repository again"
    )
    include.add_argument("repository", type=Path)
    subparsers.add_parser("excluded", help="List persistently excluded repositories")


def _add_league_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    league = subparsers.add_parser(
        "league", help="Scan saved local folders and map Git teams to their goon.yml entrypoint"
    )
    league_subparsers = league.add_subparsers(dest="league_command", required=True)
    league_scan = league_subparsers.add_parser(
        "scan", help="Read-only scan of every Git team beneath the saved League folders"
    )
    league_scan.add_argument("--json", action="store_true", dest="as_json")
    league_add = league_subparsers.add_parser(
        "add-folder", help="Add one explicit folder to the local League"
    )
    league_add.add_argument("path", type=Path)
    _add_shared_plan_commands(subparsers)
    _add_lineup_commands(subparsers)
    _add_team_commands(subparsers)


def _add_shared_plan_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    shared_plan = subparsers.add_parser(
        "shared-plan", help="Manage oh-goon-owned plan.yml files applied to explicit repositories"
    )
    commands = shared_plan.add_subparsers(dest="shared_plan_command", required=True)
    list_command = commands.add_parser("list", help="List reusable shared Plans")
    list_command.add_argument("--json", action="store_true", dest="as_json")
    templates = commands.add_parser(
        "templates", help="List copyable Plan templates and their paths"
    )
    templates.add_argument("--json", action="store_true", dest="as_json")
    history = commands.add_parser(
        "history", help="Show canonical FleetRun history for one saved Plan"
    )
    history.add_argument("plan_key")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true", dest="as_json")
    from_template = commands.add_parser(
        "from-template", help="Create a Shared Plan from one shipped template"
    )
    from_template.add_argument("template_key")
    from_template.add_argument("plan_key")
    save = commands.add_parser(
        "save", help="Save one ordinary plan.yml inside oh-goon local configuration"
    )
    save.add_argument("plan_key")
    save.add_argument("source", type=Path)
    save.add_argument(
        "--expected-revision",
        help="Exact SHA-256 from shared-plan list --json when replacing an existing Shared Plan",
    )
    preview = commands.add_parser(
        "preview", help="Run the read-only Enforcer preview for one shared Plan and explicit Teams"
    )
    preview.add_argument("plan_key")
    _add_shared_plan_repository_selection(preview)
    preview.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    preview.add_argument("--json", action="store_true", dest="as_json")
    run = commands.add_parser("run", help="Run one Shared Plan on explicitly selected League Teams")
    run.add_argument("plan_key")
    _add_shared_plan_repository_selection(run)
    run.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    run.add_argument("--fleet-run-id")
    run.add_argument("--priority", type=SchedulingPriority, default=SchedulingPriority.NORMAL)
    run.add_argument("--json", action="store_true", dest="as_json")


def _add_shared_plan_repository_selection(parser: argparse.ArgumentParser) -> None:
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--team", action="append", type=Path, dest="teams")
    selection.add_argument("--lineup", dest="lineup_key")
    selection.add_argument(
        "--all-teams",
        action="store_true",
        help="Use every current managed Git Team in the saved League",
    )


def _add_lineup_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    lineup = subparsers.add_parser("lineup", help="Save and inspect reusable local Team selections")
    commands = lineup.add_subparsers(dest="lineup_command", required=True)
    list_command = commands.add_parser("list", help="List saved League Lineups")
    list_command.add_argument("--json", action="store_true", dest="as_json")
    save = commands.add_parser("save", help="Save an explicit set of managed Team roots")
    save.add_argument("lineup_key")
    save.add_argument("--name", required=True)
    save.add_argument("--team", action="append", type=Path, required=True, dest="teams")
    save.add_argument("--expected-revision")
    save.add_argument("--json", action="store_true", dest="as_json")
    inspect = commands.add_parser("inspect", help="Check a saved Lineup against the current League")
    inspect.add_argument("lineup_key")
    inspect.add_argument("--json", action="store_true", dest="as_json")


def _add_team_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    team = subparsers.add_parser(
        "team", help="Inspect one League team through its canonical goon.yml entrypoint"
    )
    commands = team.add_subparsers(dest="team_command", required=True)
    validate = commands.add_parser(
        "validate", help="Resolve the repository-root goon.yml without executing it"
    )
    validate.add_argument("repository", type=Path, nargs="?", default=Path())
    validate.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    validate.add_argument("--json", action="store_true", dest="as_json")
    run = commands.add_parser(
        "run",
        help="Run exactly one Team through its repository-root goon.yml and wait for completion",
    )
    run.add_argument("repository", type=Path, nargs="?", default=Path())
    run.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    run.add_argument("--fleet-run-id")
    run.add_argument("--priority", type=SchedulingPriority, default=SchedulingPriority.NORMAL)
    run.add_argument("--json", action="store_true", dest="as_json")


def _add_enforcer_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    enforcer = subparsers.add_parser(
        "enforcer",
        help="Run the same repository baseline check used by strict CI/CD admission",
    )
    enforcer_subparsers = enforcer.add_subparsers(dest="enforcer_command")
    enforcer_run = enforcer_subparsers.add_parser(
        "run",
        help="Inspect one Team through the same repository-root goon.yml admission used by CI/CD",
    )
    enforcer_run.add_argument("repository", type=Path, nargs="?", default=Path.cwd())
    enforcer_run.add_argument("--input", action="append", dest="plan_inputs", metavar="NAME=VALUE")
    enforcer_run.add_argument("--json", action="store_true", dest="as_json")
    enforcer_compare = enforcer_subparsers.add_parser(
        "compare",
        help="Compare two canonical enforcer.yml run receipts",
    )
    enforcer_compare.add_argument("before", type=Path)
    enforcer_compare.add_argument("after", type=Path)


def _add_developer_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    dev = subparsers.add_parser("dev", help="Contributor debugging helpers")
    commands = dev.add_subparsers(dest="dev_command")
    _add_developer_observation_commands(commands)
    _add_developer_certification_commands(commands)
    _add_developer_capability_commands(commands)
    _add_developer_effect_commands(commands)


def _add_developer_observation_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    explain_run = commands.add_parser("explain-run", help="Trace one durable fleet run")
    explain_run.add_argument("fleet_run_id")
    explain_run.add_argument("--json", action="store_true", dest="as_json")
    shards = commands.add_parser(
        "shards", help="Show the canonical deterministic shard plan for a validation surface"
    )
    shards.add_argument("surface", choices=[self_certification_units.SCRIPTS_SURFACE])
    shards.add_argument("--json", action="store_true", dest="as_json")
    projection_profile = commands.add_parser(
        "projection-profile", help="Measure cold verified and warm cached Journal reads"
    )
    projection_profile.add_argument("--samples", type=int, default=5)


def _add_developer_certification_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    for name in (
        "bootstrap-certification",
        "preflight-certification-database",
        "reset-certification-state",
    ):
        command = commands.add_parser(name, help=argparse.SUPPRESS)
        command.add_argument("--state-root", required=True, type=Path)
        if name == "bootstrap-certification":
            command.add_argument("--require-strict-executor", action="store_true")
    require_success = commands.add_parser("require-plan-success", help=argparse.SUPPRESS)
    require_success.add_argument("--plan", required=True, type=Path)
    require_success.add_argument("--repository-id", required=True)
    require_success.add_argument("--source-root", required=True, type=Path)
    require_success.add_argument("--lock-file", required=True, type=Path)
    # Internal verification only; not a Fleet admission or enrollment command.
    require_success.add_argument("--fleet-run-id")


def _add_developer_capability_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    capability = commands.add_parser(
        "capability", help="Trace or scaffold the capability golden path"
    )
    actions = capability.add_subparsers(dest="capability_action")
    show = actions.add_parser("show", help="Show every owner for one registered capability")
    show.add_argument("capability_id")
    scaffold = actions.add_parser("scaffold", help="Generate a capability implementation packet")
    scaffold.add_argument("--id", required=True, dest="capability_id")
    scaffold.add_argument(
        "--action", required=True, choices=[action.value for action in OperationAction]
    )
    scaffold.add_argument(
        "--runner", required=True, choices=[kind.value for kind in CapabilityRunnerKind]
    )
    scaffold.add_argument(
        "--target-kind",
        default=CapabilityTargetKind.NONE.value,
        choices=[kind.value for kind in CapabilityTargetKind],
    )
    scaffold.add_argument("--label", required=True)
    scaffold.add_argument("--description", required=True)
    scaffold.add_argument(
        "--safety",
        required=True,
        choices=[item.value for item in CommandSafety],
        help="Explicit repository/environment effect class; never inferred.",
    )
    scaffold.add_argument("--output", type=Path)


def _add_developer_effect_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    commands.add_parser(
        "effect-quarantines", help="List repository effects that require recovery inspection"
    )
    acknowledge = commands.add_parser(
        "acknowledge-effect",
        help="Acknowledge one inspected partial or indeterminate repository effect",
    )
    acknowledge.add_argument("operation_id")
    acknowledge.add_argument("--note", default="Inspected manually")
    reconcile = commands.add_parser(
        "reconcile-effect",
        help="Re-run evidence-based reconciliation for one quarantined repository effect",
    )
    reconcile.add_argument("operation_id")
    invariants = commands.add_parser(
        "invariants", help="Audit cross-store invariants or print an explicit repair plan"
    )
    invariants.add_argument(
        "invariant_action", nargs="?", choices=("check", "repair-plan"), default="check"
    )


def _add_self_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    self_parser = subparsers.add_parser("self", help="Verify and certify this Oh-Goon source")
    commands = self_parser.add_subparsers(dest="self_command", required=True)
    setup = commands.add_parser("setup", help="Prepare self-verification prerequisites")
    setup.add_argument(
        "--parent-zip",
        type=Path,
        help="Stage the exact canonical parent checkpoint ZIP required for cumulative patch proof",
    )
    verify = commands.add_parser(
        "verify", help="Run or inspect the canonical fast self-verification Plan"
    )
    verify.add_argument(
        "--plan",
        action="store_true",
        help="Show the canonical bounded verification/checkpoint Plan without executing it",
    )
    verify.add_argument("--json", action="store_true", dest="as_json")
    verify.add_argument(
        "--verbose",
        action="store_true",
        help="Include commands, identities, evidence and blocked transitions",
    )
    certify = commands.add_parser("certify", help="Run the canonical self-certification Plan")
    certify.add_argument("--plan", action="store_true", help=argparse.SUPPRESS)
    certify.add_argument("--json", action="store_true", dest="as_json", help=argparse.SUPPRESS)
    certify.add_argument("--verbose", action="store_true", help=argparse.SUPPRESS)
    commands.add_parser("version", help="Display the Oh-Goon source version")
