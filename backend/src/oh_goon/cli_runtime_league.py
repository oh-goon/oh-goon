from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.core.errors import FleetDomainError


def run_league_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    if args.command == "league":
        if args.league_command == "add-folder":
            try:
                folders = runtime.locations.add(args.path)
            except (FleetDomainError, ValueError) as exc:
                print(str(exc))
                return 2
            print(f"League folder ready: {args.path.expanduser().resolve()}")
            print(f"Configured League folders: {len(folders)}")
            return 0
        if args.league_command != "scan":
            return 2
        league = runtime.league.map()
        if args.as_json:
            print(league.model_dump_json(indent=2))
            return 0
        print(f"League folders: {league.summary.folders}")
        print(f"Git teams: {league.summary.git_repositories}")
        print(f"Runnable goon.yml teams: {league.summary.configured_teams}")
        print(f"Missing goon.yml: {league.summary.missing_goon_yml}")
        print(f"Invalid goon.yml: {league.summary.invalid_goon_yml}")
        for team in league.teams:
            suffix = (
                f" sha256={team.pipeline.sha256}"
                if team.pipeline.state == "CONFIGURED"
                else f" reason={team.pipeline.reason}"
                if team.pipeline.state == "INVALID"
                else ""
            )
            print(f"  {team.repository.name}: {team.pipeline.state}{suffix}")
        return 0

    if args.command == "shared-plan":
        return _run_shared_plan_command(runtime, args)

    if args.command == "lineup":
        return _run_lineup_command(runtime, args)

    if args.command != "team":
        return None
    if args.team_command != "validate":
        return 2
    try:
        inspection = runtime.goon.inspect(args.repository, inputs=plan_inputs(args))
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    if args.as_json:
        print(inspection.preview.model_dump_json(indent=2))
        return 0
    print(f"Team: {inspection.team.repository.path}")
    print("Entrypoint: goon.yml")
    print(f"Plan ID: {inspection.preview.plan_id}")
    print("Enforcer: valid")
    for step in inspection.preview.steps:
        suffix = f" — {step.reason}" if step.reason else ""
        print(f"  {step.step_id}: {step.disposition.value}{suffix}")
    return 0


def _run_shared_plan_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        if args.shared_plan_command in {"list", "templates", "history", "from-template", "save"}:
            return _shared_plan_catalog_command(runtime, args)
        return 2
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc))
        return 2


def _shared_plan_catalog_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    command = args.shared_plan_command
    if command == "list":
        catalog = runtime.shared_plan.catalog()
        if args.as_json:
            print(catalog.model_dump_json(indent=2))
        else:
            print(f"Shared Plans directory: {runtime.shared_plan.root}")
            for plan in catalog.plans:
                last = (
                    "never run"
                    if plan.last_run is None
                    else (
                        f"last={plan.last_run.accepted_at.isoformat()} status={plan.last_run.status.value}"
                    )
                )
                changed = " changed-since-last-run" if plan.changed_since_last_run else ""
                print(f"  {plan.plan_key}: sha256={plan.sha256} {last}{changed}")
        return 0
    if command == "templates":
        catalog = runtime.shared_plan.templates()
        if args.as_json:
            print(catalog.model_dump_json(indent=2))
        else:
            for template in catalog.templates:
                print(f"  {template.template_key}")
                print(f"    path: {template.template_path}")
                print(f"    {template.description}")
        return 0
    if command == "history":
        history = runtime.shared_plan.history(args.plan_key, limit=args.limit)
        if args.as_json:
            print(history.model_dump_json(indent=2))
        else:
            print(f"Shared Plan: {history.plan_key}")
            print(f"Current SHA-256: {history.current_sha256}")
            if not history.runs:
                print("No FleetRun history for this Shared Plan.")
            for run in history.runs:
                marker = (
                    "CURRENT" if run.source_sha256 == history.current_sha256 else "OLDER REVISION"
                )
                print(
                    f"  {run.accepted_at.isoformat()} {run.status.value} teams={run.team_count} "
                    f"run={run.fleet_run_id} sha256={run.source_sha256} {marker}"
                )
        return 0
    if command == "from-template":
        catalog = runtime.shared_plan.templates()
        template = next(
            (item for item in catalog.templates if item.template_key == args.template_key),
            None,
        )
        if template is None:
            raise ValueError(f"Unknown Plan template: {args.template_key}")
        document = runtime.shared_plan.create_from_template(
            args.template_key, template.sha256, plan_key=args.plan_key
        )
        print(f"Saved: {document.plan_key}/plan.yml")
        print(f"SHA-256: {document.sha256}")
        return 0
    document = runtime.shared_plan.save(
        args.plan_key,
        args.source.read_text(encoding="utf-8"),
        expected_revision=args.expected_revision,
    )
    print(f"Saved: {document.plan_key}/plan.yml")
    print(f"SHA-256: {document.sha256}")
    return 0


def _run_lineup_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        if args.lineup_command == "list":
            catalog = runtime.lineups.catalog()
            if args.as_json:
                print(catalog.model_dump_json(indent=2))
            else:
                print(f"Lineup directory: {runtime.lineups.root}")
                for lineup in catalog.lineups:
                    print(f"  {lineup.lineup_key}: {lineup.name} teams={lineup.team_count}")
            return 0
        if args.lineup_command == "save":
            document = runtime.lineups.save(
                args.lineup_key,
                name=args.name,
                repositories=tuple(args.teams),
                expected_revision=args.expected_revision,
            )
            if args.as_json:
                print(document.model_dump_json(indent=2))
            else:
                print(f"Saved Lineup: {document.lineup_key}")
                print(f"Teams: {document.team_count}")
                print(f"Revision: {document.revision}")
            return 0
        if args.lineup_command != "inspect":
            return 2
        inspection = runtime.lineups.inspect(args.lineup_key)
        if args.as_json:
            print(inspection.model_dump_json(indent=2))
        else:
            print(f"Lineup: {inspection.name}")
            print(f"Ready Teams: {inspection.ready_count}")
            print(f"Unavailable Teams: {inspection.unavailable_count}")
            print(f"Revision: {inspection.revision}")
            for team in inspection.teams:
                if team.state == "READY":
                    print(f"  READY       {team.repository.name}")
                else:
                    print(f"  UNAVAILABLE {team.configured_path}")
        return 0 if inspection.unavailable_count == 0 else 1
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc))
        return 2
