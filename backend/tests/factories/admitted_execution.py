from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from oh_goon.core.capability_model import CapabilityRunnerKind
from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity
from oh_goon.core.models.fleet_plan import ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.operation import OperationReport
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.vocabulary import FleetRunSource, OperationAction
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import (
    write_remote_resource_evidence,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_preview import repository_plan_admission_evidence
from oh_goon.integrations.fleet_plan_process_isolation import IsolatedProcessRequest
from oh_goon.integrations.fleet_plans import FleetPlanRunner
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.effect_attempts import RepositoryEffectAttemptStore
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_capacity import ExecutorCapacitySettings
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
from tests.factories.path_authority import TEST_PATH_AUTHORITY
from tests.factories.runtime_authority import authorized_command_result

DEFAULT_ADMITTED_PLAN = """execution: {purpose: cicd}
steps:
  - id: check
    run: "true"
"""


@dataclass(frozen=True, slots=True)
class AdmittedFleetPlanExecution:
    """Observable result of one complete locked, admitted production execution path."""

    target: FleetPlanTarget
    detail: FleetRunDetail
    operation: OperationReport
    process_requests: tuple[IsolatedProcessRequest, ...]
    remaining_effect_attempts: int


class _AdmittedProcessIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="admitted-test-isolation",
        private_filesystem=True,
        private_process_tree=True,
        enforced_network_policy=True,
        content_identified_runtime=True,
        exact_argv=True,
        disposable_attempt=True,
        immutable_authority_after_start=True,
        enforced_cpu_limit=True,
        enforced_memory_limit=True,
        enforced_process_limit=True,
        kernel_boundary=IsolationKernelBoundary.VIRTUAL_MACHINE,
        remote_execution=True,
    )

    def __init__(self, returncodes: tuple[int, ...]) -> None:
        if not returncodes:
            raise ValueError("Admitted execution requires at least one process outcome")
        self._returncodes = returncodes
        self.requests: list[IsolatedProcessRequest] = []

    def configuration_identity(self) -> str:
        return "admitted-test-isolation-v1"

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        del plan

    def execute(
        self,
        request: IsolatedProcessRequest,
        *,
        timeout: float | None,
        cancel_requested,
        stream_output=None,
        process_observer=None,
        runtime_supervision=None,
    ) -> CommandResult:
        del timeout, cancel_requested, runtime_supervision
        index = len(self.requests)
        if index >= len(self._returncodes):
            raise AssertionError("Admitted process fixture exhausted its declared outcomes")
        self.requests.append(request)
        if process_observer is not None:
            from oh_goon.engine.command import CommandProcessLifecycleEvent

            physical_execution_id = f"admitted-physical-{index + 1}"
            process_observer(
                CommandProcessLifecycleEvent(
                    kind="PROCESS_STARTING", physical_execution_id=physical_execution_id
                )
            )
            process_identity = f"admitted-process-{index + 1}"
            process_observer(
                CommandProcessLifecycleEvent(
                    kind="PROCESS_STARTED",
                    physical_execution_id=physical_execution_id,
                    pid=10_000 + index,
                    process_identity=process_identity,
                )
            )
        write_remote_resource_evidence(request.attempt.paths.root, request.attempt.resources)
        stdout = f"admitted-attempt-{index + 1}"
        if stream_output is not None:
            stream_output("stdout", stdout)
        returncode = self._returncodes[index]
        if process_observer is not None:
            process_observer(
                CommandProcessLifecycleEvent(
                    kind="PROCESS_EXITED",
                    physical_execution_id=physical_execution_id,
                    pid=10_000 + index,
                    process_identity=process_identity,
                    returncode=returncode,
                )
            )
            process_observer(
                CommandProcessLifecycleEvent(
                    kind="DESCENDANTS_QUIESCENT",
                    physical_execution_id=physical_execution_id,
                    pid=10_000 + index,
                    process_identity=process_identity,
                    returncode=returncode,
                )
            )
        return replace(
            authorized_command_result(
                argv=request.argv,
                returncode=returncode,
                stdout=stdout,
                stderr="",
                duration_ms=index + 1,
            ),
            physical_execution_id=physical_execution_id,
        )


def admitted_fleet_plan_execution(
    root: Path,
    *,
    plan_text: str = DEFAULT_ADMITTED_PLAN,
    returncodes: tuple[int, ...] = (0,),
    fleet_run_id: str = "admitted-fixture-run",
) -> AdmittedFleetPlanExecution:
    """Execute the canonical source/lock/admission/effect/capability production sequence."""
    repository_path = root / "repository"
    seed_cicd_repository(repository_path)
    repository = RepositoryIdentity(path=repository_path)

    plan_path = root / "plan" / "goon.yml"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(plan_text, encoding="utf-8")
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(plan_path)
    lock_plan = resolver.resolve_source_graph(graph)
    write_execution_lock(plan_path.parent / "goon.lock", build_execution_lock(lock_plan))
    target = prepare_execution_target(resolver, plan_path)

    database = LocalDatabase(root / "runtime.db")
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    backend = _AdmittedProcessIsolation(returncodes)
    executor_configuration = ExecutorPoolConfiguration(
        placement_wait_seconds=0,
        placement_queue_limit=64,
        path_authority=TEST_PATH_AUTHORITY,
        executors=(
            ConfiguredFleetExecutor(
                priority=0,
                backend=backend,
                capacity=ExecutorCapacitySettings(
                    executor=FleetExecutorIdentity(
                        executor_id="admitted-test-executor",
                        generation=1,
                    ),
                    cpu_millis=16_000,
                    memory_mib=16_384,
                    max_processes=4096,
                ),
            ),
        ),
    )
    executor_pool = FleetExecutorPool(database, executor_configuration)
    lifecycle = FleetRunLifecycle(database, journal, execution, executor_pool)
    intent = resolve_capability_intent(OperationAction.EXECUTE_PLAN, target)
    ticket, _ = lifecycle.admit(
        fleet_run_id,
        intent,
        fleet_repository_scope({repository.repository_id}),
        [repository],
        expected_durable_revision=database.durable_revision(),
        source=FleetRunSource.MANUAL,
        repository_admissions=(repository_plan_admission_evidence(repository, target.plan),),
    )

    git = GitService()
    plan_runner = FleetPlanRunner(
        git,
        process_isolation_backends=(backend,),
        path_authority=TEST_PATH_AUTHORITY,
        content_store=FleetContentStore(root / "content-store"),
        run_parent=root / "runs",
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
    run_execution = FleetRunExecution(
        lifecycle,
        FleetTargetScheduler(effects),
        GameTapeLiveOutputStore(),
    )
    run_execution.execute(ticket)

    detail = journal.read_view().run_detail(fleet_run_id)
    if detail is None or len(detail.operations) != 1:
        raise AssertionError("Canonical admitted execution did not commit exactly one operation")
    return AdmittedFleetPlanExecution(
        target=target,
        detail=detail,
        operation=detail.operations[0],
        process_requests=tuple(backend.requests),
        remaining_effect_attempts=len(attempts.all()),
    )
