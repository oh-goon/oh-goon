"""Thin CLI translation for the single run.yml selection contract."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic
from oh_goon.core.errors import (
    FleetDomainError,
    FleetPlanInputRejected,
    FleetPlanInvalid,
    RunConfigurationInvalid,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plan_source_graph import FleetPlanSourceGraph
from oh_goon.services.locations import LocationService
from oh_goon.services.run_configuration import (
    RunConfigurationSnapshot,
    RunConfigurationTarget,
    capture_run_configuration_source,
    load_run_configuration,
    register_run_configuration_target,
    require_run_configuration_registration_target,
    resolve_run_configuration_targets,
)
from oh_goon.services.run_configuration_inspection import inspect_run_configuration


def prepare_configuration(
    args: argparse.Namespace,
    *,
    target_root: Path | None = None,
    locations: LocationService | None = None,
    snapshot: RunConfigurationSnapshot | None = None,
    targets: tuple[RunConfigurationTarget, ...] | None = None,
    source_graph: FleetPlanSourceGraph | None = None,
) -> None:
    """Capture the selected file once, then transport its existing invocation facts."""
    snapshot = snapshot or load_run_configuration(args.configuration)
    repair = snapshot.selection.repair
    if args.command == "run":
        if repair is None and args.authorize_repair:
            raise FleetPlanInvalid(
                "--authorize-repair requires an explicit repair selection in run.yml"
            )
        if repair is not None and not args.authorize_repair:
            raise FleetPlanInvalid(
                "Bounded repair needs fresh per-Run authorization: run run.yml --authorize-repair"
            )
    targets = targets or resolve_run_configuration_targets(snapshot, target_root=target_root)
    read_only = args.command == "workflow" and args.workflow_command in {"inspect", "validate"}
    register = bool(getattr(args, "register_target", False))
    if register:
        for target in targets:
            require_run_configuration_registration_target(target)
    registered = all(target.registered for target in targets)
    if not read_only and not registered and not register:
        raise RunConfigurationInvalid(
            "Every target must select an included registered repository; prepare with --register-target",
            diagnostic_code="CONFIG_TARGET_UNAVAILABLE",
            fields=("target.repository",),
        )
    # Roots and repair constraints are knowable before any source acquisition.
    agent_repository_root, agent_skill_root = snapshot.validate_authoring_paths()
    prepare = args.command == "lock" or (
        args.command == "workflow" and args.workflow_command == "prepare"
    )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    selected, graph = capture_run_configuration_source(
        snapshot,
        (),
        resolver,
        acquire=prepare or bool(getattr(args, "acquire", False)),
        allow_network=prepare or args.command == "run" or bool(getattr(args, "acquire", False)),
        update=bool(getattr(args, "update_goons", False)),
        update_applications=bool(getattr(args, "update_applications", False)),
        targets=targets,
        source_graph=source_graph,
    )
    args._run_configuration = selected
    agent = snapshot.selection.agent
    agent_source = graph.has_agent_source_contract()
    if len(selected.repositories) > 1 and agent_source:
        raise FleetPlanInvalid("Agent execution requires a single repository")
    if (agent is not None) != agent_source:
        raise FleetPlanInvalid("agent selection must match the selected source's Agent contract")
    if agent_source:
        graph.validate_agent_selection(
            native_selected=agent is not None and agent.implementation is not None,
            require_review_phases=repair is not None,
            model_selected=agent is not None and agent.model_profile is not None,
        )
    args._source_graph = graph
    args._configuration_targets = targets
    args._selected_inputs = selected.inputs
    args._caller_input_file_snapshot = snapshot
    args._caller_input_origins = dict.fromkeys(selected.inputs, "caller_file")
    defaults: dict[str, Any] = {
        "plan": selected.plan_path,
        "plan_inputs": None,
        "inputs_file": None,
        "extensions": None,
        "repository_ids": [repository.repository_id for repository in selected.repositories],
        "all_repositories": False,
        "fleet_run_id": getattr(args, "fleet_run_id", None),
        "priority": SchedulingPriority.NORMAL,
        "lock_file": snapshot.lock_path,
        "output": snapshot.lock_path,
        "standard_local": False
        if isinstance(graph.root.compiled.execution, CicdExecutionPolicy)
        else None,
        "wait": True,
        "agent_repository_root": agent_repository_root,
        "agent_skill_root": agent_skill_root,
        "allow_agent_acceptance": [agent.acceptance] if agent is not None else [],
        "agent_model_profile": agent.model_profile if agent is not None else None,
        "agent_providers_file": None,
        "native_agent": agent.implementation if agent is not None else None,
        "native_profile": agent.profile if agent is not None else None,
        "native_workspace": selected.repository.path
        if agent is not None and agent.implementation
        else None,
        "repair_attempts": None,
        "review_passes": repair.review_passes if repair else None,
        "repair_write_root": list(repair.write_roots) if repair else None,
        "repair_timeout": repair.timeout_seconds if repair else None,
        "repair_step_timeout": repair.step_timeout_seconds if repair else None,
        "repair_handoff": False,
    }
    for name, value in defaults.items():
        setattr(args, name, value)
    if register:
        if registered:
            receipts = [
                {
                    "path": str(target.repository.path),
                    "repository_id": target.repository.repository_id,
                    "status": "already-registered",
                }
                for target in targets
            ]
            args._target_registration = (
                receipts[0] if len(receipts) == 1 else {"repositories": receipts}
            )
        else:
            # Validate known contracts before opening runtime state or persisting
            # registration. Environment-backed values remain with their owner.
            report = inspect_run_configuration(selected, resolver, source_graph=graph)
            issue = report["diagnostic"]
            if issue is not None and issue["code"] != "EXTERNAL_INPUT_NOT_OBSERVED":
                raise FleetPlanInputRejected(
                    "The selected source/input contract is invalid",
                    diagnostic_code=issue["code"],
                    input_names=tuple(issue["inputs"]),
                )
            if locations is not None:
                register_prepared_configuration_target(args, locations, target_root=target_root)


def register_prepared_configuration_target(
    args: argparse.Namespace,
    locations: LocationService,
    *,
    target_root: Path | None = None,
) -> None:
    """Finish an explicitly requested registration after stateless validation."""
    if not bool(getattr(args, "register_target", False)):
        raise FleetPlanInvalid("Target registration requires explicit authorization")
    selected = args._run_configuration
    targets = args._configuration_targets
    for target in targets:
        require_run_configuration_registration_target(target)
    receipts: list[dict[str, str]] = []
    if len(targets) > 1:
        # Keep each confirmed durable effect visible if a later member fails.
        args._target_registration = {"repositories": receipts}
    for target in targets:
        receipt = register_run_configuration_target(
            selected.snapshot, target, locations, targets=targets, target_root=target_root
        )
        receipts.append(receipt)
        if len(targets) == 1:
            args._target_registration = receipt
    args._configuration_targets = tuple(replace(target, registered=True) for target in targets)
    args._run_configuration = replace(
        selected, registrations=tuple(True for _ in selected.repositories)
    )


def run_config_command(args: argparse.Namespace) -> int:
    try:
        prepare_configuration(args)
        resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
        report = inspect_run_configuration(
            args._run_configuration, resolver, source_graph=args._source_graph
        )
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print_safe_plan_diagnostic(exc, as_json=args.as_json)
        return 2
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(
            f"Configuration: {report['configuration']['path']} ({report['configuration']['sha256']})"
        )
        print(f"Source: {report['source']['selection']}\n  {report['source']['path']}")
        for selection in report["source"]["dependency_selections"]:
            print(
                f"  Dependency group: {selection['group']!r} | {selection['group_state']} | declared={selection['declared_group']!r}"
            )
        for source in report["source"]["git_sources"]:
            print(f"  Git: {source['git']} | ref={source['ref']} | commit={source['commit']}")
            print(f"    path: {source['path']}")
        targets = report["target"].get("repositories", (report["target"],))
        for target in targets:
            print(f"Target: {target['name']} ({target['repository_id']})")
        print(f"Status: {report['status']}")
        print("Inputs:")
        for item in report["inputs"]:
            contract = item["contract"]
            owners = ", ".join(" → ".join(path) for path in item["owners"]) or "root source"
            print(
                f"  {item['name']} = {item['value']!r} | {contract['input_type']} | origin={item['origin']}"
            )
            if item["value_state"] != "defined":
                print(f"    value: {item['value_state']}")
            print(f"    owner: {owners}")
            limits = {
                name: contract[name]
                for name in ("choices", "minimum", "maximum", "pattern", "non_empty")
                if contract.get(name) is not None
                and (name in {"minimum", "maximum"} or bool(contract[name]))
            }
            if limits:
                print(f"    constraints: {limits}")
        if report["unrouted_inputs"]:
            print(f"Shared names requiring explicit Plan routing: {report['unrouted_inputs']}")
        print("Ordered Goons:")
        for item in report["occurrences"]:
            phase = f" | phase={item['phase']}" if item["phase"] else ""
            print(f"  {' → '.join(item['occurrence'])}: {item['goon']}{phase}")
            print(
                f"    source: {item['source']['path']} | YAML sha256={item['source']['yaml_sha256']}"
            )
            if item["source"]["project"] is not None:
                print(f"    Python project: {item['source']['project']}")
            for selection in item["source"]["dependency_selections"]:
                print(
                    f"    Dependency group: {selection['group']!r} | {selection['group_state']} | declared={selection['declared_group']!r}"
                )
            for binding in item["bindings"]:
                print(f"    {binding['name']}: {binding['origin']}")
                if binding.get("skill") is not None:
                    print(f"      Skill: {binding['skill']}")
            for requirement in item["requirements"]:
                print(f"    requires: {requirement}")
        print("Steps and declared capabilities:")
        for step in report["steps"]:
            print(f"  {step['id']}: {step['kind']} | {step['proof']} | {step['capabilities']}")
            for kind in ("workspace", "artifacts", "outputs", "requested_effects"):
                if step[kind]:
                    print(f"    {kind}: {step[kind]}")
        for group in ("agent", "repair"):
            if report[group] is not None:
                print(f"{group}: {report[group]}")
        print(f"Skill pin records: {report['skills']}")
        print(f"Source acquisition: {report['proof']['acquisition']}")
        if report["diagnostic"] is not None:
            issue = report["diagnostic"]
            print(f"Unresolved: {issue['code']} | {issue['safe_next_action']}")
        print("Proof:")
        for name, state in report["proof"].items():
            print(f"  {name}: {state}")
    return 2 if args.workflow_command == "validate" and report["status"] != "inputs-valid" else 0
