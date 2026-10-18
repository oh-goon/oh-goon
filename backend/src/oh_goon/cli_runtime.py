from __future__ import annotations

import argparse

from oh_goon.application import ApplicationRuntime
from oh_goon.cli_enforcer import run_enforcer_command
from oh_goon.cli_runtime_goons import run_goon_command
from oh_goon.cli_runtime_league import run_league_command
from oh_goon.cli_runtime_observability import run_observability_command
from oh_goon.cli_runtime_operations import run_operation_command
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.cli_runtime_recovery import run_recovery_command
from oh_goon.cli_runtime_state import run_state_command


def run_stateful_command(runtime: ApplicationRuntime, args: argparse.Namespace) -> int:
    if args.command == "lock":
        # Retain the stateful service adapter for API/in-process parity; the
        # ordinary CLI now enters this same original lock owner without DB.
        from oh_goon.cli_lock import run_lock_with_owner

        return run_lock_with_owner(args, runtime.plans.lock)
    for handler in (
        run_enforcer_command,
        run_league_command,
        run_plan_command,
        run_goon_command,
        run_operation_command,
        run_state_command,
        run_recovery_command,
        run_observability_command,
    ):
        result = handler(runtime, args)
        if result is not None:
            return result
    return -1
