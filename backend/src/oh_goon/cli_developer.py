from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.capability import CapabilityTargetKind
from oh_goon.core.vocabulary import CommandSafety, OperationAction
from oh_goon.repository_tooling import certification_bootstrap_protocol
from oh_goon.services.capability_developer import (
    CapabilityScaffoldRequest,
    describe_capability,
    scaffold_capability,
)
from oh_goon.services.certification_bootstrap import (
    CertificationEnvironmentError,
    CertificationSourceError,
    CertificationStateBusy,
    preflight_certification_database,
    prepare_certification_bootstrap,
    reset_certification_control_state,
)


def _print_developer_activity(message: str) -> None:
    print("OH-GOON ACTIVITY: " + message, flush=True)


def _print_operation_economics(receipt) -> None:
    print(
        "OH_GOON_OPERATION_ECONOMICS=" + json.dumps(receipt.as_dict(), sort_keys=True),
        flush=True,
    )


def _bootstrap_certification_command(args: argparse.Namespace) -> int:
    strict = bool(args.require_strict_executor)
    activity = (
        "validating durable certification state and strict executor readiness"
        if strict
        else "validating durable certification state"
    )
    print(f"OH-GOON ACTIVITY: {activity}", flush=True)
    try:
        context = prepare_certification_bootstrap(
            source_root=Path.cwd(),
            state_home=args.state_root,
            environment=os.environ.copy(),
            require_strict_executor=strict,
        )
    except CertificationEnvironmentError as exc:
        print(
            certification_bootstrap_protocol.render_error_record(
                {"classification": "environment", "message": str(exc)}
            )
        )
        return 2
    except CertificationSourceError as exc:
        print(
            certification_bootstrap_protocol.render_error_record(
                {"classification": "application", "message": str(exc)}
            )
        )
        return 2
    verified = (
        "durable certification state and strict executor readiness verified"
        if strict
        else "durable certification state verified"
    )
    print(f"OH-GOON ACTIVITY: {verified}", flush=True)
    print(certification_bootstrap_protocol.render_success_record(context.as_payload()))
    return 0


def _preflight_certification_database_command(args: argparse.Namespace) -> int:
    try:
        payload = preflight_certification_database(
            source_root=Path.cwd(),
            state_home=args.state_root,
        )
    except (CertificationEnvironmentError, CertificationStateBusy) as exc:
        print("OH_GOON_CERTIFICATION_DATABASE_PREFLIGHT_ERROR=" + str(exc))
        return 2
    print("OH_GOON_CERTIFICATION_DATABASE_PREFLIGHT=" + json.dumps(payload, sort_keys=True))
    return 0


def _reset_certification_state_command(args: argparse.Namespace) -> int:
    try:
        deleted = reset_certification_control_state(
            source_root=Path.cwd(),
            state_home=args.state_root,
        )
    except CertificationStateBusy as exc:
        print("OH_GOON_CERTIFICATION_STATE_RESET_ERROR=" + str(exc))
        return 2
    print(
        "OH_GOON_CERTIFICATION_STATE_RESET="
        + json.dumps(
            {
                "deleted_file_count": len(deleted),
                "sealed_dependency_supply_retained": True,
            },
            sort_keys=True,
        )
    )
    return 0


def run_state_free_developer_command(args: argparse.Namespace) -> int | None:
    if args.command != "dev":
        return None
    handlers = {
        "bootstrap-certification": _bootstrap_certification_command,
        "preflight-certification-database": _preflight_certification_database_command,
        "reset-certification-state": _reset_certification_state_command,
        "capability": _capability_command,
    }
    handler = handlers.get(args.dev_command)
    return handler(args) if handler is not None else None


def _capability_command(args: argparse.Namespace) -> int:
    if args.capability_action == "show":
        try:
            print(describe_capability(args.capability_id))
        except FleetDomainError as exc:
            print(str(exc))
            return 2
        return 0
    packet = scaffold_capability(
        CapabilityScaffoldRequest(
            capability_id=args.capability_id,
            action=OperationAction(args.action),
            runner_kind=CapabilityRunnerKind(args.runner),
            label=args.label,
            description=args.description,
            target_kind=CapabilityTargetKind(args.target_kind),
            safety=CommandSafety(args.safety),
        )
    )
    if args.output is None:
        print(packet.render())
        return 0
    try:
        paths = packet.write(args.output.expanduser().resolve())
    except (FleetDomainError, OSError) as exc:
        print(str(exc))
        return 2
    print("Capability scaffold written:")
    for path in paths:
        print(f"- {path}")
    return 0
