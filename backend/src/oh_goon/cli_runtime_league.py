from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid
from oh_goon.core.models.run import FailedFleetRun
from oh_goon.core.vocabulary import FleetRunOutcome


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
    if args.team_command == "run":
        return _run_team(runtime, args)
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


def _run_team(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    admitted = None
    try:
        team = runtime.league.configured_team(args.repository)
        admitted = runtime.goon.run(
            args.repository,
            expected_pipeline_sha256=team.pipeline.sha256,
            inputs=plan_inputs(args),
            fleet_run_id=args.fleet_run_id,
            requested_priority=args.priority,
        )
        if not args.as_json:
            print(f"Fleet Run ID: {admitted.fleet_run_id}")
            print("Status: admitted — waiting for Final Whistle")
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
    except KeyboardInterrupt:
        if admitted is None:
            print("Local run interrupted before admission.")
            return 130
        try:
            runtime.runs.cancel(admitted.fleet_run_id)
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
            print(f"Cancellation requested: {detail.run.fleet_run_id}")
            print(f"Status: {detail.run.status.value}")
        except FleetDomainError as exc:
            print(f"Cancellation could not be confirmed: {exc}")
        return 130
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    if args.as_json:
        print(detail.model_dump_json(indent=2))
    else:
        print(f"Requested from: {args.repository.expanduser().resolve()}")
        print("Entrypoint: goon.yml")
        print(f"Status: {detail.run.status.value}")
        print(f"Outcome: {detail.outcome.value}")
        if isinstance(detail.run, FailedFleetRun):
            failure = detail.run.failure
            print(f"Failure: {failure.code} stage={failure.stage.value}")
            print(f"Reason: {failure.message}")
            print(f"Inspect: oh-goon dev explain-run {detail.run.fleet_run_id}")
    return 0 if detail.outcome is FleetRunOutcome.SUCCESS else 1


def _run_shared_plan_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        if args.shared_plan_command in {"list", "templates", "history", "from-template", "save"}:
            return _shared_plan_catalog_command(runtime, args)
        repository_ids = _shared_plan_repository_ids(runtime, args)
        if args.shared_plan_command == "preview":
            return _preview_shared_plan(runtime, args, repository_ids)
        if args.shared_plan_command != "run":
            return 2
        return _run_shared_plan(runtime, args, repository_ids)
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


def _preview_shared_plan(
    runtime: ApplicationRuntime,
    args: argparse.Namespace,
    repository_ids: set[str],
) -> int:
    preview = runtime.shared_plan.preview(
        args.plan_key,
        repository_ids=repository_ids,
        inputs=plan_inputs(args),
    )
    if args.as_json:
        print(preview.model_dump_json(indent=2))
        return 0 if preview.blocked_count == 0 else 1
    print(f"Shared Plan plan.yml: {preview.plan_key}")
    print(f"Plan SHA-256: {preview.sha256}")
    print(f"Ready Teams: {preview.ready_count}")
    print(f"Blocked Teams: {preview.blocked_count}")
    for team in preview.teams:
        if team.state == "READY":
            print(f"  READY   {team.repository.name} plan_id={team.preview.plan_id}")
        else:
            print(f"  BLOCKED {team.repository.name}: {team.reason}")
    return 0 if preview.blocked_count == 0 else 1


def _run_shared_plan(
    runtime: ApplicationRuntime,
    args: argparse.Namespace,
    repository_ids: set[str],
) -> int:
    admitted = None
    try:
        inputs = plan_inputs(args)
        preview = runtime.shared_plan.preview(args.plan_key, repository_ids=repository_ids, inputs=inputs)
        if preview.blocked_count:
            raise FleetPlanInvalid(f"Shared Plan preview blocked {preview.blocked_count} selected Team(s); resolve the reported blockers before execution")
        admitted = runtime.shared_plan.run(
            args.plan_key, repository_ids=repository_ids, inputs=inputs, fleet_run_id=args.fleet_run_id, requested_priority=args.priority,
            expected_plan_id=preview.plan_id, expected_execution_digest=preview.execution_digest, expected_plan_revision=preview.sha256,
            repository_instance_ids={team.repository.repository_id: team.repository_instance_id for team in preview.teams},
        )
        if not args.as_json:
            print(f"Fleet Run ID: {admitted.fleet_run_id}")
            print(f"Shared Plan plan.yml: {args.plan_key}")
            print(f"Selected Teams: {len(repository_ids)}")
            print("Repository goon.yml files: omitted for this run")
        detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
    except KeyboardInterrupt:
        if admitted is None:
            print("Shared Run interrupted before admission.")
            return 130
        try:
            runtime.runs.cancel(admitted.fleet_run_id)
            detail = runtime.runs.wait_for_terminal(admitted.fleet_run_id)
            print(f"Cancellation requested: {detail.run.fleet_run_id}")
            print(f"Status: {detail.run.status.value}")
        except FleetDomainError as exc:
            print(f"Cancellation could not be confirmed: {exc}")
        return 130
    if args.as_json:
        print(detail.model_dump_json(indent=2))
    else:
        print(f"Status: {detail.run.status.value}")
        print(f"Outcome: {detail.outcome.value}")
    return 0 if detail.outcome is FleetRunOutcome.SUCCESS else 1


def _shared_plan_repository_ids(runtime: ApplicationRuntime, args: argparse.Namespace) -> set[str]:
    if getattr(args, "all_teams", False):
        return runtime.shared_plan.all_repository_ids()
    lineup_key = getattr(args, "lineup_key", None)
    if lineup_key:
        return runtime.lineups.repository_ids(lineup_key)
    teams = tuple(getattr(args, "teams", None) or ())
    return runtime.shared_plan.repository_ids(teams)


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
