"""Read-only CLI presentation of the existing canonical template stores."""

from __future__ import annotations

import argparse
import json
import shlex
import sqlite3
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from oh_goon.cli_result import CliTermination
from oh_goon.core.errors import FleetDomainError, FleetPlanInputRejected
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.template_identity import template_usage
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore

if TYPE_CHECKING:
    from oh_goon.services.fleet_plan_input_inspection import InputInspection
    from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph


def initialization_inputs(args: argparse.Namespace) -> dict[str, PlanInputValue]:
    """Transport literal CLI values; the compiler alone interprets their types."""
    values: dict[str, PlanInputValue] = {}
    for selection in args.input_values:
        if "=" not in selection:
            raise FleetPlanInputRejected(
                "--input requires NAME=VALUE",
                diagnostic_code="INVALID_INPUT_VALUE",
                input_names=(),
            )
        name, value = selection.split("=", 1)
        if name in values:
            raise FleetPlanInputRejected(
                "Each initialization input must be supplied once",
                diagnostic_code="DUPLICATE_INPUT",
                input_names=(name,),
            )
        values[name] = value
    for name in args.input_nulls:
        if name in values:
            raise FleetPlanInputRejected(
                "Each initialization input must be supplied once",
                diagnostic_code="DUPLICATE_INPUT",
                input_names=(name,),
            )
        values[name] = None
    return values


def prompt_initialization_input(item: InputInspection) -> PlanInputValue:
    """Read one bounded value only after the caller selected an interactive TTY."""
    definition = item.definition
    choices = f"; choices: {', '.join(definition.choices)}" if definition.choices else ""
    print(f"{definition.name} ({definition.input_type.value}{choices}): ", end="", file=sys.stderr)
    value = sys.stdin.readline(4097)
    if not value:
        raise FleetPlanInputRejected(
            "Required input was not supplied before EOF",
            diagnostic_code="MISSING_REQUIRED_INPUT",
            input_names=(definition.name,),
        )
    if len(value) > 4096:
        raise FleetPlanInputRejected(
            "Interactive input exceeds its size bound",
            diagnostic_code="INVALID_INPUT_VALUE",
            input_names=(definition.name,),
        )
    return value.removesuffix("\n").removesuffix("\r")


def _verify_initialized_configuration(args: argparse.Namespace) -> None:
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
    from oh_goon.services.fleet_plans import _resolve_locked_execution

    selected = args._run_configuration
    _resolve_locked_execution(
        FleetPlanResolver(goon_templates=GoonTemplateStore()),
        selected.plan_path,
        extensions_path=None,
        inputs=selected.inputs,
        lock_path=selected.snapshot.lock_path,
        external_artifacts=selected.snapshot.external_artifacts(),
        source_graph=args._source_graph,
        repository=selected.resolution_repository,
    )
    selected.snapshot.require_fresh()


def _run_initialization(args: argparse.Namespace) -> int:
    from oh_goon.cli_configuration import (
        prepare_configuration,
        register_prepared_configuration_target,
    )
    from oh_goon.cli_lock import generate_stateless_lock, prepare_stateless_lock
    from oh_goon.core.plan_validation_diagnostics import safe_plan_diagnostic
    from oh_goon.integrations.exact_file_write import ExactPublicationDurabilityError
    from oh_goon.runtime.database import DatabasePathIdentityLost
    from oh_goon.runtime.storage_health import storage_unavailable_reason
    from oh_goon.services.run_configuration import load_run_configuration
    from oh_goon.services.template_plan_drafts import initialize_run_configuration

    lock_args = argparse.Namespace()
    published = False

    def prepare(path: Path, graph: FleetPlanSourceGraph, final: Path) -> str:
        nonlocal lock_args
        from oh_goon.application import ApplicationRuntime

        snapshot = load_run_configuration(path)
        lock_args = argparse.Namespace(
            command="workflow",
            workflow_command="prepare",
            configuration=path,
            register_target=True,
            update_goons=False,
            update_applications=False,
            update_skills=False,
            as_json=args.as_json,
        )
        prepare_configuration(lock_args, target_root=final, snapshot=snapshot, source_graph=graph)
        lock_owner = prepare_stateless_lock(lock_args)
        if not lock_args._run_configuration.registered:
            locations = ApplicationRuntime.build_registration_service()
            register_prepared_configuration_target(lock_args, locations, target_root=final)
        lock = generate_stateless_lock(lock_args, lock_owner=lock_owner)
        _verify_initialized_configuration(lock_args)
        return lock.digest

    try:
        reviewers = []
        for value in args.review:
            if "=" not in value:
                raise ValueError("--review requires ALIAS=SKILL")
            alias, skill = value.split("=", 1)
            reviewers.append((alias, skill))
        supplied = initialization_inputs(args)
        interactive = not args.no_input and not args.as_json and sys.stdin.isatty()
        result = initialize_run_configuration(
            args.key,
            args.output,
            kind=args.kind,
            reviewers=reviewers,
            repositories=args.repository or (),
            inputs=supplied,
            draft=args.draft,
            prompt=prompt_initialization_input if interactive else None,
            prepare=prepare,
        )
        published = True
        if not args.draft:
            verified = argparse.Namespace(
                command="workflow",
                workflow_command="validate",
                configuration=result.path,
                update_goons=False,
                update_applications=False,
            )
            prepare_configuration(verified)
            _verify_initialized_configuration(verified)
        report = {
            "schema": "oh-goon.workflow-init/v1",
            "status": "not_ready" if args.draft else "ready",
            "configuration": str(result.path),
            "execution_digest": result.execution_digest,
            "required_fields": list(result.required_fields),
            "target_registration": getattr(lock_args, "_target_registration", None),
            "next_action": shlex.join(
                (
                    "oh-goon",
                    "workflow",
                    "prepare",
                    str(result.path.parent),
                    "--register-target",
                    "--no-input",
                )
            )
            if args.draft
            else shlex.join(("oh-goon", "run", str(result.path.parent))),
        }
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"{'NOT READY' if args.draft else 'READY'}: {result.path}")
            if result.required_fields:
                print("Required fields: " + ", ".join(result.required_fields))
            if args.draft:
                print("Review and complete run.yml, then explicitly prepare it.")
            print("Next: " + str(report["next_action"]))
        return 0
    except (
        FleetDomainError,
        OSError,
        UnicodeError,
        ValueError,
        sqlite3.Error,
        KeyboardInterrupt,
    ) as exc:
        identity_lost = isinstance(exc, DatabasePathIdentityLost)
        storage_failure = identity_lost or storage_unavailable_reason(exc) is not None
        if isinstance(exc, sqlite3.Error) and not storage_failure:
            raise
        diagnostic = (
            exc if isinstance(exc, Exception) else InterruptedError("Initialization interrupted")
        )
        report = dict(safe_plan_diagnostic(diagnostic))
        if isinstance(exc, KeyboardInterrupt):
            report["status"] = "interrupted"
            report["code"] = "CLI_INTERRUPTED"
            report["safe_next_action"] = (
                "Inspect published authoring and retained target-registration facts before retrying; interruption does not authorize repair or rollback of committed state."
            )
        if storage_failure:
            report["code"] = (
                "RUNTIME_DATABASE_IDENTITY_LOST" if identity_lost else "RUNTIME_STORAGE_UNAVAILABLE"
            )
            report["safe_next_action"] = (
                "Preserve existing state and inspect local-state compatibility and host storage before retrying. This refusal does not authorize repair, reset or replay."
            )
        committed = published or isinstance(exc, ExactPublicationDurabilityError)
        draft_action = (
            "For incomplete authoring, use 'oh-goon init --draft' with the same template "
            "and a fresh output directory; complete its required run.yml fields before preparation."
        )
        suggest_draft = not args.draft and not committed and isinstance(exc, FleetDomainError)
        if suggest_draft:
            report["safe_next_action"] = str(report["safe_next_action"]) + " " + draft_action
        report["publication"] = (
            "visible_durability_unverified"
            if isinstance(exc, ExactPublicationDurabilityError)
            else "committed_before_failure"
            if published
            else "unverified_after_interruption"
            if isinstance(exc, KeyboardInterrupt)
            else "not_published"
        )
        registration = getattr(lock_args, "_target_registration", None)
        if registration is not None:
            report["target_registration"] = registration
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic

            if storage_failure:
                print(f"{report['code']}: {report['safe_next_action']}")
            else:
                print_safe_plan_diagnostic(diagnostic, as_json=False)
            if suggest_draft:
                print(draft_action)
            print("Publication: " + str(report["publication"]))
            if registration is not None:
                print("Target registration retained: " + json.dumps(registration))
        return (
            (143 if isinstance(exc, CliTermination) else 130)
            if isinstance(exc, KeyboardInterrupt)
            else 2
        )


def run_template_command(args: argparse.Namespace) -> int:
    if args.template_command == "init":
        return _run_initialization(args)
    stores = {"plan": PlanTemplateStore, "goon": GoonTemplateStore}
    try:
        if args.template_command == "show":
            print(stores[args.kind]().read(args.key).content, end="")
            return 0
        entries = [
            {"kind": kind, **item.model_dump(mode="json"), **template_usage(item.classification)}
            for kind, factory in stores.items()
            if args.kind in {kind, "all"}
            for item in factory().catalog().templates
        ]
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic

        print_safe_plan_diagnostic(exc, as_json=bool(getattr(args, "as_json", False)))
        return 2
    if args.as_json:
        print(json.dumps({"schema": "oh-goon.template-catalog/v1", "templates": entries}, indent=2))
    else:
        for item in entries:
            print(
                f"{item['kind']:4}  {item['template_key']} — {item['description']} | "
                f"{item['usage']} | readiness={item['readiness']} | "
                f"prerequisites={item['prerequisites']}"
            )
    return 0
