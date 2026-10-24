"""Original execution-lock authoring, without requiring Fleet runtime startup.

Both entrypoints use FleetPlanService's original single source-graph/lock writer.
No other writer, digest policy, or authorization path is introduced here.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from oh_goon.cli_agent_providers import agent_connection_from_args
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
        if hasattr(args, "_run_configuration"):
            from oh_goon.cli_plan_diagnostics import print_safe_plan_diagnostic

            print_safe_plan_diagnostic(exc, as_json=False)
        else:
            print(str(exc))
        return 2
    destination = args.output or (args.plan.parent / "goon.lock")
    print(f"Generated: {destination}")
    print(f"Execution digest: {lock.digest}")
    return 0


def run_stateless_lock(args: argparse.Namespace) -> int:
    """Skip app DB/Fleet initialization, not the original atomic lock writer."""

    def canonical_lock(
        plan: Path,
        *,
        inputs: dict[str, PlanInputValue] | None,
        output: Path | None,
    ) -> ExecutionLock:
        selected = bool(
            args.agent_repository_root
            or args.agent_skill_root
            or args.allow_agent_acceptance
            or args.native_agent
        )
        native, git = None, None
        if args.native_agent is not None:
            from oh_goon.cli_ai import native_plan_selection

            native, _, git = native_plan_selection(args)
        resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(), git_service=git)
        if not selected:
            return _generate_execution_lock(
                resolver,
                plan,
                extensions_path=None,
                inputs=inputs,
                output=output,
                external_artifacts=args._run_configuration.snapshot.external_artifacts()
                if hasattr(args, "_run_configuration")
                else (),
                caller_input_file=args._run_configuration.snapshot.authority()
                if hasattr(args, "_run_configuration")
                else None,
                source_graph=getattr(args, "_source_graph", None),
                repository=args._run_configuration.repository.path
                if hasattr(args, "_run_configuration")
                else None,
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

        from oh_goon.services.execution_lock_persistence import (
            LOCK_FILE_NAME,
            load_execution_lock,
            write_execution_lock,
        )

        destination = output or (plan.parent / LOCK_FILE_NAME)
        existing = load_execution_lock(destination) if destination.exists() else None
        resolved, handoffs, _ = resolver.resolve_frozen_agents(
            plan,
            inputs=inputs or {},
            repository_root=args.agent_repository_root,
            skill_root=args.agent_skill_root,
            allowed_acceptance=tuple(args.allow_agent_acceptance),
            connection=(lambda: agent_connection_from_args(args)) if native is None else None,
            native_agent=native,
            repository=args._run_configuration.repository.path
            if hasattr(args, "_run_configuration")
            else args.native_workspace
            if native is not None
            else None,
            git_service=git,
            locked_skills=existing.skill_sources if existing else None,
            update_skills=args.update_skills,
            require_review_phases=bool(args.review_passes),
            source_graph=getattr(args, "_source_graph", None),
        )
        if native is not None or hasattr(args, "_run_configuration"):
            captured = next(iter(handoffs.values())).source
            repository = (
                args.agent_repository_root / captured.repository_reference.removeprefix("local:")
            ).resolve(strict=True)
            expected_repository = (
                args._run_configuration.repository.path
                if hasattr(args, "_run_configuration")
                else args.native_workspace
            )
            if repository != expected_repository:
                from oh_goon.core.errors import FleetPlanInvalid

                raise FleetPlanInvalid(
                    "Captured Agent repository differs from the explicitly selected target"
                )
        from oh_goon.core.execution_lock_identity import build_execution_lock

        lock = build_execution_lock(
            resolved,
            external_artifacts=args._run_configuration.snapshot.external_artifacts()
            if hasattr(args, "_run_configuration")
            else (),
        )
        if hasattr(args, "_run_configuration"):
            args._run_configuration.snapshot.require_fresh()
        write_execution_lock(destination, lock)
        return lock

    return run_lock_with_owner(args, canonical_lock)
