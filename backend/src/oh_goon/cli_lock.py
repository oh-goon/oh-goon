"""Original execution-lock authoring, without requiring Fleet runtime startup.

Both entrypoints use FleetPlanService's original single source-graph/lock writer.
No other writer, digest policy, or authorization path is introduced here.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from oh_goon.cli_plan_inputs import plan_inputs
from oh_goon.core.errors import FleetDomainError
from oh_goon.core.models.execution_lock_models import ExecutionLock
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import _generate_execution_lock


def run_lock_with_owner(
    args: argparse.Namespace,
    lock_owner: Callable[..., ExecutionLock],
) -> int:
    """Present one original lock-writer result; never independently write a lock."""
    try:
        lock = lock_owner(args.plan, inputs=plan_inputs(args), output=args.output)
    except (FleetDomainError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc))
        return 2
    destination = args.output or (args.plan.parent / "goon.lock")
    print(f"Generated: {destination}")
    print(f"Execution digest: {lock.digest}")
    return 0


def run_stateless_lock(args: argparse.Namespace) -> int:
    """Skip app DB/Fleet initialization, not the original atomic lock writer."""
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())

    def canonical_lock(
        plan: Path,
        *,
        inputs: dict[str, PlanInputValue] | None,
        output: Path | None,
    ) -> ExecutionLock:
        selected = bool(
            args.agent_repository_root or args.agent_skill_root or args.allow_agent_acceptance
        )
        if not selected:
            return _generate_execution_lock(
                resolver, plan, extensions_path=None, inputs=inputs, output=output
            )
        if (
            args.agent_repository_root is None
            or args.agent_skill_root is None
            or not args.allow_agent_acceptance
        ):
            from oh_goon.core.errors import FleetPlanInvalid

            raise FleetPlanInvalid(
                "Agent lock requires both authorized source roots and acceptance"
            )
        resolved, _, _ = resolver.resolve_frozen_local_search_agent(
            plan,
            inputs=inputs or {},
            repository_root=args.agent_repository_root,
            skill_root=args.agent_skill_root,
            allowed_acceptance=tuple(args.allow_agent_acceptance),
        )
        from oh_goon.core.execution_lock_identity import build_execution_lock
        from oh_goon.services.execution_lock_persistence import LOCK_FILE_NAME, write_execution_lock

        lock = build_execution_lock(resolved)
        write_execution_lock(output or (plan.parent / LOCK_FILE_NAME), lock)
        return lock

    return run_lock_with_owner(args, canonical_lock)
