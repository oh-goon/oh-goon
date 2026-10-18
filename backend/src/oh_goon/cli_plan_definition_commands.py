from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic
from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.cli_plan_summary import resolved_plan_summary as _resolved_plan_summary
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_input_inspection import InputInspection
from oh_goon.services.fleet_plan_input_routing import CallerFileRoutingOrigin
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import FleetPlanService

_INSPECTION_SCHEMA = "oh-goon.plan-input-inspection/v1"


def _inspection_input(item: InputInspection) -> dict[str, object]:
    """Presentation only: never infer constraints or read an input value."""
    definition = item.definition
    result: dict[str, object] = {
        "name": definition.name,
        "type": definition.input_type.value,
        "required": definition.required,
    }
    if definition.default is not None:
        result["default"] = definition.default
    if definition.choices:
        result["choices"] = list(definition.choices)
    if definition.minimum is not None:
        result["minimum"] = definition.minimum
    if definition.maximum is not None:
        result["maximum"] = definition.maximum
    if definition.pattern is not None:
        result["pattern"] = definition.pattern
    if definition.starts_with is not None:
        result["starts_with"] = definition.starts_with
    if definition.non_empty:
        result["non_empty"] = True
    if definition.description:
        result["description"] = definition.description
    result["owner_paths"] = [list(path) for path in item.owner_paths]
    return result


def _print_input_inspection(
    args: argparse.Namespace,
    inspector: Callable[[Path], tuple[tuple[InputInspection, ...], str]],
) -> int:
    try:
        inspected, root_digest = inspector(args.plan)
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc))
        return 2
    report = {
        "schema": _INSPECTION_SCHEMA,
        "root": args.plan.as_posix(),
        "root_source_sha256": root_digest,
        "status": "contract_inspected",
        "inputs": [_inspection_input(item) for item in inspected],
    }
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(f"Plan/Goon: {report['root']}")
    print("Status: contract_inspected (read-only; not resolved, admitted or ready)")
    print(f"Root source SHA-256: {root_digest} (root file only; not imported closure)")
    if not report["inputs"]:
        print("Inputs: none")
        return 0
    print("Inputs:")
    for item in report["inputs"]:
        requirement = "required" if item["required"] else "optional"
        details = []
        for key in (
            "default",
            "minimum",
            "maximum",
            "choices",
            "pattern",
            "starts_with",
            "non_empty",
        ):
            if key in item:
                details.append(f"{key}={item[key]!r}")
        suffix = f" — {', '.join(details)}" if details else ""
        print(f"  {item['name']} ({item['type']}; {requirement}){suffix}")
        if "description" in item:
            print(f"    Description: {item['description']}")
        for path in item["owner_paths"]:
            print(f"    Owner: {' -> '.join(path)}")
        if not item["owner_paths"]:
            print("    Owner: directly authored root input")
    return 0


def _inputs(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Direct dispatcher parity for service users; never selects values."""
    return _print_input_inspection(args, runtime.plans.inspect_inputs_with_source)


def _inputs_pre_runtime(args: argparse.Namespace) -> int:
    """CLI discovery is strictly read-only, requiring no runtime state or enrollment."""
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    if getattr(args, "source_closure", False):
        if not all(
            (args.expect_sha256, args.repository_root, args.skill_root, args.allow_acceptance)
        ):
            print(
                "AG-01 source preview requires --expect-sha256, --repository-root, --skill-root and --allow-acceptance"
            )
            return 2
        try:
            aliases: dict[str, str] = {}
            for item in args.skill_alias or ():
                if "=" not in item:
                    raise ValueError("AG-01 --skill-alias must use NAME=LOCAL_REF")
                name, source = item.split("=", 1)
                if not name or name in aliases or not source:
                    raise ValueError("AG-01 duplicate or empty skill alias")
                aliases[name] = source
            evidence = resolver.explain_agent_sources(
                args.plan,
                inputs=plan_inputs(args),
                repository_root=args.repository_root,
                skill_root=args.skill_root,
                allowed_acceptance=tuple(args.allow_acceptance),
                expected_source_sha256=args.expect_sha256,
                skill_aliases=aliases,
            )
        except (FleetDomainError, OSError, ValueError) as exc:
            print(str(exc))
            return 2
        print(json.dumps(evidence, indent=2, sort_keys=True))
        return 0
    if any(
        (
            getattr(args, "plan_inputs", None),
            args.expect_sha256,
            args.repository_root,
            args.skill_root,
            args.allow_acceptance,
            args.skill_alias,
        )
    ):
        print(
            "AG-01 source parameters require --source-closure; ordinary inputs inspection is value-free"
        )
        return 2
    return _print_input_inspection(args, resolver.inspect_inputs_with_source)


def _validate_with_resolver(
    args: argparse.Namespace,
    resolve: Callable[..., ResolvedFleetPlan],
) -> int:
    try:
        plan = resolve(args.plan, extensions_path=args.extensions, inputs=plan_inputs(args))
    except (FleetDomainError, ValueError) as exc:
        if getattr(args, "diagnostics", False):
            print_safe_plan_diagnostic(exc, as_json=args.as_json)
            return 2
        print(str(exc))
        if getattr(args, "repair_handoff", False):
            print("\nAPP CERTIFICATION COULD NOT START")
            print(f"Reason: {exc}")
            print("No certification Goon was allowed to run.")
            print("Fix this bootstrap/admission problem, then rerun: make certify")
            print("Do not skip admission, change the Plan, or bypass locked execution.")
        return 2
    if args.as_json:
        print(plan.model_dump_json(indent=2))
        return 0
    for line in _resolved_plan_summary(plan):
        print(line)
    return 0


def _validate(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Service dispatch parity: use the exact same authoring presentation."""
    return _validate_with_resolver(args, runtime.plans.resolve)


def _validate_pre_runtime(args: argparse.Namespace) -> int:
    """Compile the authoring contract with its original owner, without SQLite.

    This is *not* Fleet admission or a substitute for prepared extension/tool
    runtimes: the canonical resolver still rejects missing runtime authority.
    """
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    return _validate_with_resolver(args, resolver.resolve)


def _explain_with_owner(
    args: argparse.Namespace, owner: FleetPlanResolver | FleetPlanService
) -> int:
    """Keep one presentation of the original resolver-owned explanation."""
    if getattr(args, "routes", False):
        try:
            selected = plan_inputs(args)
            snapshot = getattr(args, "_caller_input_file_snapshot", None)
            origin = None
            if snapshot is not None:
                origin = CallerFileRoutingOrigin(
                    relative_path=snapshot.relative_path,
                    sha256=snapshot.sha256,
                    names=frozenset(name for name, _ in snapshot.entries),
                )
            routing = owner.explain_routing(
                args.plan,
                extensions_path=args.extensions,
                inputs=selected,
                caller_file=origin,
            )
        except (FleetDomainError, ValueError, OSError) as exc:
            if getattr(args, "diagnostics", False):
                print_safe_plan_diagnostic(exc, as_json=args.as_json)
            else:
                print(str(exc))
            return 2
        if args.as_json:
            print(json.dumps(routing, indent=2, ensure_ascii=False))
            return 0
        print(f"Plan: {routing['root']}")
        print(f"Plan ID: {routing['plan_id']}")
        print("Status: resolved_not_executed (not admitted or executed)")
        print(f"Root source SHA-256: {routing['root_source_sha256']} (root file only)")
        for item in routing["inputs"]:
            print(f"  Input {item['name']}: origin={item['origin']['kind']}")
            for recipient in item["recipients"]:
                print(f"    -> {recipient['import_instance_id']}: {recipient['input']}")
        for entry in routing["bindings"]:
            if entry["root_input"] is None:
                print(
                    f"  Other binding {entry['import_instance_id']}:{entry['input']} "
                    f"origin={entry['origin']['kind']} (no raw value shown)"
                )
        return 0
    try:
        report = owner.explain(args.plan, extensions_path=args.extensions, inputs=plan_inputs(args))
    except (FleetDomainError, ValueError) as exc:
        if getattr(args, "diagnostics", False):
            print_safe_plan_diagnostic(exc, as_json=args.as_json)
        else:
            print(str(exc))
        return 2
    if args.as_json:
        print(report.model_dump_json(indent=2))
        return 0
    print(f"Plan: {report.name}")
    print(f"Plan ID: {report.plan_id}")
    if report.values:
        print("Values:")
        for value in report.values:
            host = " host-value-redacted=true" if value.contains_host_value else ""
            declared = f" declared={value.declared!r}" if value.declared else ""
            print(
                f"  {value.source}:{value.scope}:{value.namespace.value}.{value.name} "
                f"origin={value.origin} resolved={value.resolved!r}{declared}{host}"
            )
    print("Steps:")
    for entry in report.entries:
        compiled = f" -> {', '.join(entry.compiled_step_ids)}" if entry.compiled_step_ids else ""
        reason = f" — {entry.reason}" if entry.reason else ""
        print(
            f"  {entry.source}:{entry.step_id}: {entry.disposition.value} "
            f"({entry.kind}){compiled}{reason}"
        )
    return 0


def _explain(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Preserve stateful service-dispatch compatibility."""
    return _explain_with_owner(args, runtime.plans)


def _explain_pre_runtime(args: argparse.Namespace) -> int:
    """Read-only source and recipient diagnostics, no SQLite or Fleet startup."""
    return _explain_with_owner(args, FleetPlanResolver(goon_templates=GoonTemplateStore()))


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


def _prepare(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    try:
        plan, imported = runtime.plans.prepare(
            args.plan, extensions_path=args.extensions, inputs=plan_inputs(args)
        )
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    print(f"Plan: {plan.name}")
    print(f"Plan ID: {plan.plan_id}")
    print(f"Prepared extensions: {len(imported)}")
    for extension in imported:
        print(f"  {extension.name}: {extension.extension_id}")
    prepared_tools = (
        "pre-commit" if any(step.kind == "pre_commit" for step in plan.steps) else "none"
    )
    print(f"Prepared built-in tools: {prepared_tools}")
    return 0


def _preview_with_owner(
    args: argparse.Namespace, owner: FleetPlanResolver | FleetPlanService
) -> int:
    """Render the original repository-scoped evaluator without reinterpreting it."""
    try:
        preview = owner.preview(
            args.plan, args.repository, extensions_path=args.extensions, inputs=plan_inputs(args)
        )
    except (FleetDomainError, ValueError) as exc:
        print(str(exc))
        return 2
    if args.as_json:
        print(preview.model_dump_json(indent=2))
        return 0
    print(f"Plan ID: {preview.plan_id}")
    print(f"Repository: {preview.repository}")
    for step in preview.steps:
        suffix = f" — {step.reason}" if step.reason else ""
        print(f"  {step.step_id}: {step.disposition.value}{suffix}")
    return 0


def _preview(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    """Preserve service-dispatch compatibility through the canonical resolver."""
    return _preview_with_owner(args, runtime.plans)


def _preview_pre_runtime(args: argparse.Namespace) -> int:
    """Authoring-time repository evaluation needs neither SQLite nor a Fleet Run."""
    return _preview_with_owner(args, FleetPlanResolver(goon_templates=GoonTemplateStore()))
