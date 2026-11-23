"""Inspect or explicitly retire Pi state through the original Run owner."""

from __future__ import annotations

import argparse
import json

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.agent_providers import PiSessionIntent
from oh_goon.core.plan_validation_diagnostics import pi_session_safe_next_action


def run_pi_session_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    fleet_run_id = None
    try:
        fleet_run_id = PiSessionIntent.from_selector(f"run:{args.fleet_run_id}").prior_run_id
        assert fleet_run_id is not None
        result = (
            runtime.runs.retire_pi_session(fleet_run_id, authorize=args.authorize)
            if args.runs_command == "retire-pi-session"
            else runtime.runs.inspect_pi_session(fleet_run_id)
        )
    except (FleetDomainError, OSError, ValueError) as exc:
        code = exc.code if isinstance(exc, FleetDomainError) else "PI_SESSION_OPERATION_UNCONFIRMED"
        action = pi_session_safe_next_action(code) or (
            "Select an exact Run with runs list and inspect its current Pi session evidence."
        )
        readback = (
            ["oh-goon", "runs", "pi-session", fleet_run_id, "--json"]
            if fleet_run_id is not None
            else None
        )
        if args.as_json:
            print(
                json.dumps(
                    {
                        "schema": "oh-goon.pi-session-error/v1",
                        "fleet_run_id": fleet_run_id,
                        "code": code,
                        "safe_next_action": action,
                        "readback_argv": readback,
                    }
                )
            )
        else:
            print(f"Pi session operation refused ({code})")
            print(action)
            if readback is not None:
                print("Inspect argv " + json.dumps(readback))
        return 2

    if args.as_json:
        print(result.model_dump_json())
    else:
        print(f"Fleet Run ID {result.fleet_run_id}")
        print(f"Pi availability {result.availability} | retirement {result.retirement}")
        if result.native is not None:
            print(f"Recorded Pi identity {result.native.session_id} | leaf {result.native.leaf_id}")
        if result.refusal_code is not None:
            print(f"Reason {result.refusal_code}")
    return 0
