from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.core.errors import FleetDomainError
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService


def _check_with_owner(args: argparse.Namespace, owner: FleetPlanResolver | FleetPlanService) -> int:
    try:
        report = owner.check(args.root, extensions_path=args.extensions, inputs=plan_inputs(args))
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    if args.as_json:
        print(report.model_dump_json(indent=2))
    else:
        print(f"Checked files: {report.checked_files}")
        if report.valid:
            print("Plan contracts: valid")
        else:
            for issue in report.issues:
                print(f"  {issue.path}: {issue.message}")
    return 0 if report.valid else 2


def _check(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    return _check_with_owner(args, runtime.plans)


def _check_pre_runtime(args: argparse.Namespace) -> int:
    return _check_with_owner(args, FleetPlanResolver(goon_templates=GoonTemplateStore()))


def _diff_with_owner(args: argparse.Namespace, owner: FleetPlanResolver | FleetPlanService) -> int:
    """One semantic-diff presentation using the existing resolver's comparison."""
    try:
        diff = owner.diff(
            args.before,
            args.after,
            extensions_path=args.extensions,
            inputs=plan_inputs(args),
        )
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    # CI/agent exit codes follow the existing semantic comparison owner;
    # presentation must never independently interpret plan IDs or source files.
    result_code = 1 if getattr(args, "exit_code", False) and diff.changed else 0
    if args.as_json:
        print(diff.model_dump_json(indent=2))
        return result_code
    print(f"Before: {diff.before_plan_id}")
    print(f"After:  {diff.after_plan_id}")
    if not diff.changed:
        print("Execution meaning: unchanged")
        return result_code
    for field in diff.plan_fields:
        print(f"  plan.{field.field}: {field.before!r} -> {field.after!r}")
    for step in diff.steps:
        print(f"  {step.kind.value}: {step.step_id}")
        for field in step.fields:
            print(f"    {field.field}: {field.before!r} -> {field.after!r}")
    return result_code


def _diff(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    return _diff_with_owner(args, runtime.plans)


def _diff_pre_runtime(args: argparse.Namespace) -> int:
    """Compare fully resolved meaning without constructing application state."""
    return _diff_with_owner(args, FleetPlanResolver(goon_templates=GoonTemplateStore()))
