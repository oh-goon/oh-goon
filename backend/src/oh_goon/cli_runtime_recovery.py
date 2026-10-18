from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.recovery_status import RecoveryStatus


def run_recovery_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int | None:
    if args.command != "dev":
        return None
    if args.dev_command == "effect-quarantines":
        return _list_effect_quarantines(runtime.recovery.status())
    if args.dev_command == "acknowledge-effect":
        try:
            observed = _effect_quarantine(runtime.recovery.status(), args.operation_id)
            status = runtime.recovery.acknowledge(
                args.operation_id,
                note=args.note,
                expected_record_version=observed.record_version,
            )
        except FleetDomainError as exc:
            print(str(exc))
            return 2
        print(f"Acknowledged repository effect {args.operation_id}")
        return 2 if _still_quarantined(status, args.operation_id) else 0
    if args.dev_command == "reconcile-effect":
        try:
            observed = _effect_quarantine(runtime.recovery.status(), args.operation_id)
            status = runtime.recovery.reconcile(
                args.operation_id,
                expected_record_version=observed.record_version,
            )
        except FleetDomainError as exc:
            print(str(exc))
            return 2
        remaining = next(
            (item for item in status.effect_quarantines if item.operation_id == args.operation_id),
            None,
        )
        if remaining is None:
            print(f"Reconciled {args.operation_id}: quarantine cleared")
            return 0
        print(f"Reconciled {args.operation_id}: {remaining.status.value}")
        if remaining.reconciliation is not None:
            print(f"Outcome: {remaining.reconciliation.outcome.value}")
            print(f"Reason: {remaining.reconciliation.reason}")
        return 2
    if args.dev_command != "invariants":
        return None
    status = runtime.recovery.status()
    if args.invariant_action == "repair-plan":
        plan = status.repair_plan
        if not plan.findings:
            print("No invariant findings; no repair plan required.")
            return 0
        print("Invariant repair plan")
        for action in plan.actions:
            print(
                f"- {action.action} target={action.target} "
                f"confirmation={str(action.requires_confirmation).lower()}"
            )
            print(f"  {action.explanation}")
        if not plan.actions:
            print("No automatic/mechanical repair action is safe for the current findings.")
        return 2
    audit = status.invariants
    if not audit.findings:
        print("All checked oh-goon invariants hold.")
        return 0
    print("oh-goon invariant findings")
    for finding in audit.findings:
        print(f"- {finding.severity.value} {finding.code}: {finding.message}")
    return 0 if audit.healthy else 2


def _effect_quarantine(status: RecoveryStatus, operation_id: str):
    item = next((item for item in status.effect_quarantines if item.operation_id == operation_id), None)
    if item is None:
        from oh_goon.core.errors import OperationNotFound

        raise OperationNotFound(operation_id)
    return item


def _still_quarantined(status: RecoveryStatus, operation_id: str) -> bool:
    return any(item.operation_id == operation_id for item in status.effect_quarantines)


def _list_effect_quarantines(status: RecoveryStatus) -> int:
    quarantines = status.effect_quarantines
    if not quarantines:
        print("No repository effects are quarantined.")
        return 0
    print("Repository effects requiring inspection")
    for item in quarantines:
        print(
            f"- {item.operation_id} source={item.source.value} repository={item.repository.path} "
            f"action={item.action.value} safety={item.safety.value} status={item.status.value}"
        )
        if item.reconciliation is not None:
            print(f"  reconciliation={item.reconciliation.outcome.value}")
            print(f"  {item.reconciliation.reason}")
    return 2
