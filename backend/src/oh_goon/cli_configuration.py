"""Thin CLI translation for the single run.yml selection contract."""

from __future__ import annotations

import argparse
import json
from typing import Any

from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic
from oh_goon.core.errors import FleetDomainError, FleetPlanInvalid
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.scheduling import SchedulingPriority
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.repository_discovery import RepositoryDiscoveryService
from oh_goon.services.run_configuration import (
    RunConfigurationSnapshot,
    capture_run_configuration_source,
    load_run_configuration,
)
from oh_goon.services.run_configuration_inspection import inspect_run_configuration


def prepare_configuration(
    args: argparse.Namespace, *, snapshot: RunConfigurationSnapshot | None = None
) -> None:
    """Capture the selected file once, then transport its existing invocation facts."""
    snapshot = snapshot if snapshot is not None else load_run_configuration(args.configuration)
    locations = LeagueFolderStore().list()
    discovery = RepositoryDiscoveryService()
    requested = snapshot.selection.target.selected_ids()
    repositories = discovery.explicit_configured_roots(locations, repository_ids=requested)
    missing = set(requested) - {repository.repository_id for repository in repositories}
    if missing:
        discovered = (
            RepositoryIdentity.model_validate({"path": path})
            for path in discovery.git_roots(locations, require_pyproject=False)
        )
        repositories += tuple(
            repository for repository in discovered if repository.repository_id in missing
        )
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    selected, graph = capture_run_configuration_source(
        snapshot,
        repositories,
        resolver,
        acquire=args.command == "lock" or bool(getattr(args, "acquire", False)),
        allow_network=args.command in {"lock", "run"} or bool(getattr(args, "acquire", False)),
        update=bool(getattr(args, "update_goons", False)),
        update_applications=bool(getattr(args, "update_applications", False)),
    )
    args._run_configuration = selected
    agent = snapshot.selection.agent
    repair = snapshot.selection.repair
    agent_source = graph.has_agent_source_contract()
    if snapshot.selection.resources is not None and (
        agent_source or isinstance(graph.root.compiled.execution, CicdExecutionPolicy)
    ):
        raise FleetPlanInvalid(
            "Whole-Run resources require ordinary local execution without Agent Steps"
        )
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
    args._selected_inputs = selected.inputs
    args._caller_input_file_snapshot = snapshot
    args._caller_input_origins = dict.fromkeys(selected.inputs, "caller_file")
    if repair is None and args.command == "run" and args.authorize_repair:
        raise FleetPlanInvalid(
            "--authorize-repair requires an explicit repair selection in run.yml"
        )
    if repair is not None and args.command == "run" and not args.authorize_repair:
        raise FleetPlanInvalid(
            "Bounded repair needs fresh per-Run authorization: run run.yml --authorize-repair"
        )
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
        "agent_repository_root": snapshot.agent_repository_root(),
        "agent_skill_root": snapshot.agent_skill_root(),
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
    return 2 if args.config_command == "validate" and report["status"] != "inputs-valid" else 0
