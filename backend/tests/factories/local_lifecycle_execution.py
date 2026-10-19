from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource, OperationAction
from oh_goon.engine.git import GitService
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.integrations.fleet_plans import FleetPlanRunner
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator, ExecutionTicket
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.game_tape_live_output import GameTapeLiveOutputStore
from oh_goon.runtime.journal import Journal
from oh_goon.services.capability_executor import CapabilityExecutor
from oh_goon.services.execution_lock_persistence import write_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.fleet_plans import prepare_execution_target
from oh_goon.services.fleet_run_execution import FleetRunExecution
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from oh_goon.services.fleet_target_scheduler import FleetTargetScheduler
from oh_goon.services.repository_effect_execution import RepositoryEffectExecution
from oh_goon.services.repository_effect_fence import RepositoryEffectFence
from oh_goon.services.repository_effect_settlement import RepositoryEffectSettlement
from tests.factories.cicd_repository import seed_cicd_repository


@dataclass(slots=True)
class LocalLifecycleExecution:
    """Production-owner composition for one real local direct Fleet Plan lifecycle."""

    root: Path
    repository: RepositoryIdentity
    target: FleetPlanTarget
    database_path: Path
    database: LocalDatabase
    journal: Journal
    lifecycle: FleetRunLifecycle
    execution: FleetRunExecution
    ticket: ExecutionTicket
    attempts: RepositoryEffectAttemptStore
    fleet_run_id: str

    def execute(self) -> FleetRunDetail:
        return self.execution.execute(self.ticket)

    def reconstructed_detail(self) -> FleetRunDetail:
        # Deliberately create new durable owners. The caller does not reuse this
        # fixture's LocalDatabase, Journal, lifecycle, supervisor, or live-output store.
        database = LocalDatabase(self.database_path)
        detail = Journal(database).read_view().run_detail(self.fleet_run_id)
        if detail is None:
            raise AssertionError("Fresh Journal reconstruction lost the Fleet run")
        return detail


def _prepare_repository_and_target(
    root: Path, plan_text: str, script_body: str | None
) -> tuple[RepositoryIdentity, FleetPlanTarget]:
    repository_path = root / "repository"
    seed_cicd_repository(repository_path)
    if script_body is not None:
        script = repository_path / "lifecycle-step.py"
        script.write_text(f"#!{sys.executable}\n{script_body}\n", encoding="utf-8")
        script.chmod(script.stat().st_mode | 0o111)
    repository = RepositoryIdentity(path=repository_path)

    plan_path = root / "plan" / "goon.yml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan_text, encoding="utf-8")
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(plan_path)
    lock_plan = resolver.resolve_source_graph(graph)
    write_execution_lock(plan_path.parent / "goon.lock", build_execution_lock(lock_plan))
    return repository, prepare_execution_target(resolver, plan_path)


def _compose_run_execution(
    root: Path, database: LocalDatabase, journal: Journal, lifecycle: FleetRunLifecycle
) -> tuple[FleetRunExecution, RepositoryEffectAttemptStore]:
    git = GitService()
    live_output = GameTapeLiveOutputStore()
    plan_runner = FleetPlanRunner(
        git,
        process_isolation_backends=(),
        path_authority=None,
        content_store=FleetContentStore(root / "content-store"),
        run_parent=root / "runs",
        output_observer_factory=live_output.observer,
        lifecycle_observer_factory=journal.execution_lifecycle_observer,
    )
    capability_executor = CapabilityExecutor({CapabilityRunnerKind.FLEET_PLAN: plan_runner})
    attempts = RepositoryEffectAttemptStore(database)
    effects = RepositoryEffectExecution(
        capability_executor,
        journal,
        attempts,
        git,
        effect_fence=RepositoryEffectFence(git),
        settlement=RepositoryEffectSettlement(database, journal, attempts),
    )
    return (
        FleetRunExecution(lifecycle, FleetTargetScheduler(effects), live_output),
        attempts,
    )


def prepare_local_lifecycle_execution(
    root: Path,
    *,
    plan_text: str,
    fleet_run_id: str = "local-lifecycle-run",
    script_body: str | None = None,
    rerun_from_run_id: str | None = None,
    lease_environment=None,
) -> LocalLifecycleExecution:
    """Resolve, lock and admit a direct Plan through the real local execution owners."""
    repository, target = _prepare_repository_and_target(root, plan_text, script_body)
    database_path = root / "runtime.db"
    database = LocalDatabase(database_path)
    journal = Journal(database)
    execution_coordinator = ExecutionCoordinator(database, lease_environment=lease_environment)
    lifecycle = FleetRunLifecycle(
        database, journal, execution_coordinator, FleetExecutorPool(database, None)
    )
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    ticket, _ = lifecycle.admit(
        fleet_run_id,
        intent,
        fleet_repository_scope({repository.repository_id}),
        [repository],
        expected_durable_revision=database.durable_revision(),
        source=FleetRunSource.MANUAL,
        repository_admissions=(repository_plan_admission_evidence(repository, target.plan),),
        rerun_from_run_id=rerun_from_run_id,
    )
    run_execution, attempts = _compose_run_execution(root, database, journal, lifecycle)
    return LocalLifecycleExecution(
        root=root,
        repository=repository,
        target=target,
        database_path=database_path,
        database=database,
        journal=journal,
        lifecycle=lifecycle,
        execution=run_execution,
        ticket=ticket,
        attempts=attempts,
        fleet_run_id=fleet_run_id,
    )
