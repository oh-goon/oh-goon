from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.product_identity import new_operation_id
from oh_goon.core.projections.explanation import explain_operation
from oh_goon.core.vocabulary import OperationAction


def run_operation_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    if args.command != "operation":
        return None
    repository = args.repository.expanduser().resolve()
    operation_id = getattr(args, "operation_id", None) or new_operation_id()
    try:
        result = runtime.operations.run(
            repository, OperationAction(args.action), operation_id=operation_id
        )
    except FleetDomainError as exc:
        print(str(exc))
        return 2
    if args.as_json:
        print(result.report.model_dump_json(indent=2))
        return 0 if result.returncode == 0 else result.returncode
    report = result.report
    print(f"Repository: {report.repository.name}")
    print(f"Repository ID: {report.repository.repository_id}")
    print(f"Operation ID: {report.provenance.operation_id}")
    if report.provenance.fleet_run_id is not None:
        print(f"Fleet Run ID: {report.provenance.fleet_run_id}")
    print(f"Action: {report.action.value}")
    print(f"Operation Outcome: {report.outcome.state}")
    explanation = explain_operation(report)
    print(f"Repository Verdict: {report.outcome.verdict}")
    print(f"Phase: {explanation.phase}")
    print(f"Reason Code: {explanation.reason_code}")
    print(f"Risk: {explanation.risk}")
    if explanation.auto_fix_action is not None:
        print(f"Auto Fix: {explanation.auto_fix_action}")
    return 0 if result.returncode == 0 else result.returncode
