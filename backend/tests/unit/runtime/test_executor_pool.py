from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutorAdmissionBlocked, ExecutorPlacementLost, FleetPlanInvalid
from oh_goon.core.models.fleet_executor import FleetExecutorIdentity, FleetExecutorPlacement
from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    UvExecutionEnvironment,
)
from oh_goon.core.models.fleet_plan_evidence import PlanIsolationBackendEvidence
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    ParallelRepositoryExecution,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
    IsolationThreatModel,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCapabilities
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import (
    StrictFleetExecutionAdmission,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import (
    FleetRunSource,
    FleetRunStatus,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.integrations.fleet_plan_process_isolation import isolation_backend_evidence
from oh_goon.runtime._executor_configuration import (
    ConfiguredFleetExecutor,
    ExecutorPoolConfiguration,
)
from oh_goon.runtime._executor_placement import _placement_order
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution import ExecutionCoordinator
from oh_goon.runtime.executor_capacity import (
    ExecutorCapacitySettings,
    FleetExecutorCapacityReservations,
)
from oh_goon.runtime.executor_pool import FleetExecutorPool
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_run_lifecycle import FleetRunLifecycle
from tests.factories.capabilities import resolved_intent
from tests.factories.fleet_plan_targets import structural_fleet_plan_target
from tests.factories.path_authority import TEST_PATH_AUTHORITY
from tests.factories.run_admission import repository_admission_for


def _control_identity(pool: FleetExecutorPool, executor_id: str = "executor-a") -> tuple[int, int]:
    status = next(
        item for item in pool.executor_snapshot().executors if item.executor_id == executor_id
    )
    return status.generation, status.operational_revision


def _drain(pool: FleetExecutorPool, executor_id: str = "executor-a"):
    generation, revision = _control_identity(pool, executor_id)
    return pool.drain_executor(
        executor_id, expected_generation=generation, expected_revision=revision
    )


def _activate(pool: FleetExecutorPool, executor_id: str = "executor-a"):
    generation, revision = _control_identity(pool, executor_id)
    return pool.activate_executor(
        executor_id, expected_generation=generation, expected_revision=revision
    )


def _revoke(pool: FleetExecutorPool, executor_id: str = "executor-a"):
    generation, revision = _control_identity(pool, executor_id)
    return pool.revoke_executor_trust(
        executor_id, expected_generation=generation, expected_revision=revision
    )


def _restore(pool: FleetExecutorPool, executor_id: str = "executor-a"):
    generation, revision = _control_identity(pool, executor_id)
    return pool.restore_executor_trust(
        executor_id, expected_generation=generation, expected_revision=revision
    )


class _Backend:
    def __init__(self, backend_id: str, *, configuration: str, available: bool = True) -> None:
        self.capabilities = IsolationBackendCapabilities(
            backend_id=backend_id,
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
        self._configuration = configuration
        self._available = available

    def configuration_identity(self) -> str:
        return self._configuration

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        del plan
        if not self._available:
            raise FleetPlanInvalid(f"{self.capabilities.backend_id} unavailable")

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        del request, timeout, cancel_requested
        raise AssertionError("placement tests never execute a process")


def _plan(
    *,
    cpu: int = 1000,
    memory: int = 1024,
    parallel: int = 4,
    threat_model: IsolationThreatModel = IsolationThreatModel.TRUSTED_REPOSITORY,
    tools: tuple[str, ...] = (),
) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="executor-pool",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=(threat_model is IsolationThreatModel.TRUSTED_REPOSITORY),
            resources=CicdResourcePolicy(cpu_millis=cpu, memory_mib=memory, max_processes=256),
            repositories=ParallelRepositoryExecution(max_parallel=parallel),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="noop",
                argv=("true",),
                capabilities=PlanStepCapabilities(tools=tools),
            ),
        ),
    )


def _uv_plan() -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="executor-pool-uv",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=1000, memory_mib=1024, max_processes=256),
        ),
        requirements=(),
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="managed-uv",
                argv=(
                    "uv",
                    "run",
                    "--frozen",
                    "--no-default-groups",
                    "--project",
                    "backend",
                    "--group",
                    "build",
                    "demo-tool",
                ),
                uv=UvExecutionEnvironment(project="backend", group="build"),
            ),
        ),
    )


def _candidate(
    tmp_path: Path,
    executor_id: str,
    *,
    priority: int,
    cpu: int = 4000,
    memory: int = 4096,
    available: bool = True,
    generation: int = 1,
    kernel: IsolationKernelBoundary = IsolationKernelBoundary.VIRTUAL_MACHINE,
) -> ConfiguredFleetExecutor:
    backend = _Backend(
        f"backend-{executor_id}",
        configuration=f"configuration-{executor_id}",
        available=available,
    )
    backend.capabilities = backend.capabilities.model_copy(update={"kernel_boundary": kernel})
    return ConfiguredFleetExecutor(
        priority=priority,
        backend=backend,
        capacity=ExecutorCapacitySettings(
            executor=FleetExecutorIdentity(executor_id=executor_id, generation=generation),
            cpu_millis=cpu,
            memory_mib=memory,
            max_processes=4096,
        ),
    )


def _database(tmp_path: Path) -> LocalDatabase:
    return LocalDatabase(tmp_path / "runtime.db")


def _targets(count: int, *, root: Path | None = None) -> tuple[RepositoryIdentity, ...]:
    base = (root or Path.cwd()).resolve()
    return tuple(
        RepositoryIdentity(path=base / f"executor-target-{index}") for index in range(count)
    )


def _lose_next_product_write_response(database: LocalDatabase) -> None:
    original = database.transaction
    lost = False

    @contextmanager
    def ambiguous_transaction(*, write: bool = False, track_revision: bool = True):
        nonlocal lost
        with original(write=write, track_revision=track_revision) as connection:
            yield connection
        if write and track_revision and not lost:
            lost = True
            raise RuntimeError("strict Fleet admission commit response lost after commit")

    database.transaction = ambiguous_transaction  # type: ignore[method-assign]


def _pool(
    tmp_path: Path, *executors: ConfiguredFleetExecutor, wait_seconds: int = 0
) -> FleetExecutorPool:
    return FleetExecutorPool(
        _database(tmp_path),
        ExecutorPoolConfiguration(
            placement_wait_seconds=wait_seconds,
            placement_queue_limit=64,
            path_authority=TEST_PATH_AUTHORITY,
            executors=tuple(executors),
        ),
    )


def _reserve_admitted(
    pool: FleetExecutorPool,
    *,
    fleet_run_id: str,
    plan: ResolvedFleetPlan,
    target_count: int,
):
    placement, _ = pool.admit_strict_with(
        fleet_run_id=fleet_run_id,
        plan=plan,
        targets=_targets(target_count),
        commit=lambda _connection, _placement: None,
    )
    return placement


def _release(tmp_path: Path, placement: FleetExecutorPlacement) -> None:
    database = _database(tmp_path)
    reservations = FleetExecutorCapacityReservations(database)
    with database.transaction(write=True) as connection:
        reservations.release_in(connection, placement.executor_capacity)


def test_strict_admission_replays_exact_bundle_after_ambiguous_commit_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _database(tmp_path)
    candidate = _candidate(tmp_path, "executor-ambiguous", priority=0)
    pool = FleetExecutorPool(
        database,
        ExecutorPoolConfiguration(
            placement_wait_seconds=0,
            placement_queue_limit=64,
            path_authority=TEST_PATH_AUTHORITY,
            executors=(candidate,),
        ),
    )
    journal = Journal(database)
    execution = ExecutionCoordinator(database)
    lifecycle = FleetRunLifecycle(database, journal, execution, pool)
    plan = _plan(parallel=2)
    target = structural_fleet_plan_target(plan)
    intent = resolved_intent(OperationAction.EXECUTE_PLAN, target)
    repository = RepositoryIdentity(path=tmp_path / "repo")
    expected_revision = database.durable_revision()

    _lose_next_product_write_response(database)
    ticket, state = lifecycle.admit(
        "strict-ambiguous-admission",
        intent,
        fleet_repository_scope(None),
        [repository],
        expected_durable_revision=expected_revision,
        source=FleetRunSource.MANUAL,
        repository_admissions=(
            repository_admission_for(
                repository_id=repository.repository_id,
                plan_id=target.plan.plan_id,
                active_step_ids=tuple(step.step_id for step in target.plan.steps),
            ),
        ),
    )

    assert state.status is FleetRunStatus.QUEUED
    detail = journal.read_view().run_detail("strict-ambiguous-admission")
    assert detail is not None
    assert isinstance(detail.execution, StrictFleetExecutionAdmission)
    assert execution.active_lease() is not None
    assert pool.executor_snapshot().executors[0].active_fleet_run_ids == (
        "strict-ambiguous-admission",
    )
    assert pool.placement_queue().entries == ()

    session = lifecycle.activate(ticket)

    def reject_split_read(*_args, **_kwargs):
        raise AssertionError("execution authority opened a standalone durable read")

    monkeypatch.setattr(journal, "read_view", reject_split_read)
    monkeypatch.setattr(pool, "require_bound", reject_split_read)
    authority = lifecycle.execution_authority(session)
    assert isinstance(authority.execution, StrictFleetExecutionAdmission)

    lifecycle.finish(session, FleetRunTerminalIntent.CANCEL)
    assert execution.active_lease() is None
    assert pool.executor_snapshot().executors[0].active_fleet_run_ids == ()


def test_configured_executor_identity_is_owned_by_capacity_settings(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path, "executor-one-owner", priority=0, generation=7)

    assert candidate.identity is candidate.capacity.executor
    assert candidate.identity.executor_id == "executor-one-owner"
    assert candidate.identity.generation == 7


def test_release_uses_pool_storage_even_after_executor_is_removed(tmp_path: Path) -> None:
    selected = _candidate(tmp_path, "executor-release", priority=0)
    original = _pool(tmp_path, selected)
    placement = _reserve_admitted(
        original, fleet_run_id="run-release-after-removal", plan=_plan(), target_count=1
    )

    _pool(tmp_path)
    _release(tmp_path, placement)

    assert original.executor_snapshot().executors[0].active_fleet_run_ids == ()


def test_equal_priority_placement_is_deterministic_for_run_identity(tmp_path: Path) -> None:
    first = _candidate(tmp_path, "executor-a", priority=10)
    second = _candidate(tmp_path, "executor-b", priority=10)
    candidates = (first, second)

    first_order = _placement_order(candidates, "run-stable")
    repeated_order = _placement_order(candidates, "run-stable")

    assert tuple(item.identity.executor_id for item in repeated_order) == tuple(
        item.identity.executor_id for item in first_order
    )


def test_placement_tries_next_executor_when_preferred_candidate_cannot_fit(tmp_path: Path) -> None:
    too_small = _candidate(tmp_path, "executor-small", priority=0, cpu=500, memory=512)
    fallback = _candidate(tmp_path, "executor-fit", priority=1, cpu=4000, memory=4096)
    pool = _pool(tmp_path, too_small, fallback)

    placement = _reserve_admitted(pool, fleet_run_id="run-fit", plan=_plan(), target_count=2)

    assert placement.executor_capacity.executor_id == "executor-fit"
    assert placement.executor_capacity.placement_rank == 1
    assert placement.executor_capacity.executor_priority == 1


def test_uv_placement_does_not_resolve_dependencies_before_admission(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "1"\n',
        encoding="utf-8",
    )
    # Structurally present but intentionally not a valid uv lock. Placement owns
    # deterministic executor/source feasibility, not package-manager resolution.
    (backend / "uv.lock").write_text("not-a-valid-uv-lock\n", encoding="utf-8")
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-uv", priority=0))

    placement, _ = pool.admit_strict_with(
        fleet_run_id="run-uv-structural-admission",
        plan=_uv_plan(),
        targets=(RepositoryIdentity(path=repository),),
        commit=lambda _connection, _placement: None,
    )

    assert placement.executor_capacity.executor_id == "executor-uv"
    _release(tmp_path, placement)


def test_missing_executor_tool_is_rejected_before_placement_or_final_admission(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    before = _database(tmp_path).durable_revision()
    committed = False

    def commit(_connection, _placement):
        nonlocal committed
        committed = True

    with pytest.raises(
        FleetPlanInvalid, match="missing required tool.*definitely-not-a-real-fleet-tool"
    ):
        pool.admit_strict_with(
            fleet_run_id="run-missing-tool",
            plan=_plan(tools=("definitely-not-a-real-fleet-tool",)),
            targets=_targets(1, root=tmp_path),
            commit=commit,
        )

    assert committed is False
    assert pool.placement_queue().entries == ()
    assert _database(tmp_path).durable_revision() == before


def test_unavailable_executor_is_skipped_before_admission(tmp_path: Path) -> None:
    unavailable = _candidate(tmp_path, "executor-down", priority=0, available=False)
    healthy = _candidate(tmp_path, "executor-up", priority=1)
    pool = _pool(tmp_path, unavailable, healthy)

    placement = _reserve_admitted(pool, fleet_run_id="run-healthy", plan=_plan(), target_count=1)

    assert placement.executor_capacity.executor_id == "executor-up"


def test_bound_executor_cannot_relocate_after_admission(tmp_path: Path) -> None:
    selected = _candidate(tmp_path, "executor-a", priority=0)
    alternative = _candidate(tmp_path, "executor-b", priority=1)
    original = _pool(tmp_path, selected, alternative)
    placement = _reserve_admitted(original, fleet_run_id="run-bound", plan=_plan(), target_count=1)

    restarted_without_selected = _pool(tmp_path, alternative)
    with pytest.raises(ExecutorPlacementLost, match="automatic relocation is forbidden"):
        restarted_without_selected.require_bound(placement, _plan())


def test_executor_placement_rejects_backend_capacity_mismatch(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    placement = _reserve_admitted(
        pool, fleet_run_id="run-placement-mismatch", plan=_plan(), target_count=1
    )
    wrong_backend = PlanIsolationBackendEvidence(
        backend_id=placement.isolation_backend.backend_id,
        configuration_id="different-backend-config",
        kernel_boundary=placement.isolation_backend.kernel_boundary,
        remote_execution=placement.isolation_backend.remote_execution,
        experimental=placement.isolation_backend.experimental,
    )

    with pytest.raises(ValueError, match="must match the admitted isolation backend"):
        FleetExecutorPlacement(
            isolation_backend=wrong_backend,
            executor_capacity=placement.executor_capacity,
            executor_trust_epoch=placement.executor_trust_epoch,
        )

    _release(tmp_path, placement)


def test_bound_executor_backend_drift_is_rejected_by_binding_revalidation(tmp_path: Path) -> None:
    selected = _candidate(tmp_path, "executor-a", priority=0)
    pool = _pool(tmp_path, selected)
    placement = _reserve_admitted(
        pool, fleet_run_id="run-backend-drift", plan=_plan(), target_count=1
    )
    selected.backend._configuration = "changed-after-admission"

    with pytest.raises(ExecutorPlacementLost, match="backend configuration changed"):
        pool.require_bound(placement, _plan())

    _release(tmp_path, placement)


def test_bound_executor_revalidates_exact_capacity_reservation(tmp_path: Path) -> None:
    selected = _candidate(tmp_path, "executor-a", priority=0)
    pool = _pool(tmp_path, selected)
    placement = _reserve_admitted(pool, fleet_run_id="run-bound", plan=_plan(), target_count=1)

    backend = pool.require_bound(placement, _plan())

    assert isolation_backend_evidence(backend) == placement.isolation_backend


def test_threat_model_is_checked_by_executor_pool_before_admission(tmp_path: Path) -> None:
    shared_kernel = _candidate(
        tmp_path,
        "local-only",
        priority=0,
        kernel=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )
    pool = _pool(tmp_path, shared_kernel)

    with pytest.raises(FleetPlanInvalid, match="No Fleet executor can satisfy"):
        _reserve_admitted(
            pool,
            fleet_run_id="run-hostile",
            plan=_plan(threat_model=IsolationThreatModel.HOSTILE_GENERATED_CODE),
            target_count=1,
        )


def test_executor_health_attestation_id_is_read_only_and_derived(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=7))
    placement = _reserve_admitted(pool, fleet_run_id="run-health", plan=_plan(), target_count=1)
    payload = placement.executor_capacity.model_dump(mode="python", exclude_computed_fields=True)
    payload["health_attestation_id"] = "tampered-health"

    with pytest.raises(ValueError):
        type(placement.executor_capacity).model_validate(payload)


def test_executor_generation_change_invalidates_admitted_placement(tmp_path: Path) -> None:
    original = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=1))
    placement = _reserve_admitted(
        original, fleet_run_id="run-generation", plan=_plan(), target_count=1
    )
    replacement = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=2))

    with pytest.raises(ExecutorPlacementLost, match="automatic relocation is forbidden"):
        replacement.require_bound(placement, _plan())


def test_stale_process_local_generation_cannot_ignore_newer_durable_generation(
    tmp_path: Path,
) -> None:
    original = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=1))
    placement = _reserve_admitted(
        original,
        fleet_run_id="run-stale-process-generation",
        plan=_plan(),
        target_count=1,
    )
    replacement = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=2))

    # Publish generation 2 durably while the original process still retains its
    # process-local generation-1 configuration.
    replacement.executor_snapshot()

    with pytest.raises(ExecutorPlacementLost, match="generation.*no longer current"):
        original.require_bound(placement, _plan())


def test_capacity_wait_allows_fifo_head_to_admit_after_release(tmp_path: Path) -> None:
    import threading
    import time

    candidate = _candidate(tmp_path, "executor-a", priority=0, cpu=1000, memory=1024)
    pool = _pool(tmp_path, candidate, wait_seconds=1)
    first = _reserve_admitted(
        pool, fleet_run_id="run-first", plan=_plan(parallel=2), target_count=1
    )

    releaser = threading.Thread(
        target=lambda: (time.sleep(0.1), _release(tmp_path, first)),
        daemon=True,
    )
    releaser.start()
    second = _reserve_admitted(
        pool, fleet_run_id="run-second", plan=_plan(parallel=2), target_count=1
    )
    releaser.join(timeout=1)

    assert second.executor_capacity.executor_id == "executor-a"
    assert second.executor_capacity.health_attestation_id


def test_zero_wait_capacity_exhaustion_is_retryable_busy(tmp_path: Path) -> None:
    from oh_goon.core.errors import ExecutorCapacityBusy

    candidate = _candidate(tmp_path, "executor-a", priority=0, cpu=1000, memory=1024)
    pool = _pool(tmp_path, candidate, wait_seconds=0)
    first = _reserve_admitted(
        pool, fleet_run_id="run-first", plan=_plan(parallel=2), target_count=1
    )
    try:
        with pytest.raises(ExecutorCapacityBusy, match="placement wait expired"):
            _reserve_admitted(
                pool, fleet_run_id="run-second", plan=_plan(parallel=2), target_count=1
            )
    finally:
        _release(tmp_path, first)


def test_drain_blocks_new_placement_without_invalidating_bound_run(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    placement = _reserve_admitted(
        pool, fleet_run_id="run-bound-drain", plan=_plan(), target_count=1
    )

    status = _drain(pool)

    assert status.admission_mode.value == "draining"
    assert status.accepting_new_runs is False
    backend = pool.require_bound(placement, _plan())
    assert isolation_backend_evidence(backend) == placement.isolation_backend
    with pytest.raises(ExecutorAdmissionBlocked, match="draining"):
        _reserve_admitted(pool, fleet_run_id="run-new-drain", plan=_plan(), target_count=1)
    _release(tmp_path, placement)


def test_trust_revocation_blocks_new_placement_and_invalidates_bound_revalidation(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    placement = _reserve_admitted(
        pool, fleet_run_id="run-bound-revoked", plan=_plan(), target_count=1
    )

    status = _revoke(pool)

    assert status.trust_state.value == "revoked"
    assert status.accepting_new_runs is False
    with pytest.raises(ExecutorPlacementLost, match="Trust.*revoked"):
        pool.require_bound(placement, _plan())
    with pytest.raises(ExecutorAdmissionBlocked, match="trust revoked"):
        _reserve_admitted(pool, fleet_run_id="run-new-revoked", plan=_plan(), target_count=1)
    _release(tmp_path, placement)


def test_exact_bound_revalidation_distinguishes_drain_from_trust_revoke(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    placement = _reserve_admitted(
        pool,
        fleet_run_id="run-drain-versus-revoke",
        plan=_plan(),
        target_count=1,
    )

    _drain(pool)
    assert isolation_backend_evidence(pool.require_bound(placement, _plan())) == (
        placement.isolation_backend
    )

    _revoke(pool)
    with pytest.raises(ExecutorPlacementLost, match="Trust.*revoked"):
        pool.require_bound(placement, _plan())

    _release(tmp_path, placement)


def test_trust_restore_does_not_resurrect_pre_revoke_placement(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    stale = _reserve_admitted(
        pool,
        fleet_run_id="run-before-trust-revoke",
        plan=_plan(),
        target_count=1,
    )

    _revoke(pool)
    _restore(pool)

    with pytest.raises(ExecutorPlacementLost, match="restoring trust authorizes new work only"):
        pool.require_bound(stale, _plan())

    fresh = _reserve_admitted(
        pool,
        fleet_run_id="run-after-trust-restore",
        plan=_plan(),
        target_count=1,
    )
    assert fresh.executor_trust_epoch == stale.executor_trust_epoch + 2
    assert isolation_backend_evidence(pool.require_bound(fresh, _plan())) == (
        fresh.isolation_backend
    )

    _release(tmp_path, stale)
    _release(tmp_path, fresh)


def test_drain_and_activate_do_not_advance_trust_epoch_or_invalidate_bound_placement(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    admitted = _reserve_admitted(
        pool,
        fleet_run_id="run-before-drain-cycle",
        plan=_plan(),
        target_count=1,
    )

    _drain(pool)
    assert isolation_backend_evidence(pool.require_bound(admitted, _plan())) == (
        admitted.isolation_backend
    )
    _activate(pool)
    assert isolation_backend_evidence(pool.require_bound(admitted, _plan())) == (
        admitted.isolation_backend
    )

    fresh = _reserve_admitted(
        pool,
        fleet_run_id="run-after-drain-cycle",
        plan=_plan(),
        target_count=1,
    )
    assert fresh.executor_trust_epoch == admitted.executor_trust_epoch

    _release(tmp_path, admitted)
    _release(tmp_path, fresh)


def test_operator_state_survives_executor_generation_replacement_until_explicit_change(
    tmp_path: Path,
) -> None:
    original = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=1))
    _drain(original)
    _revoke(original)

    replacement = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=2))
    status = replacement.executor_snapshot().executors[0]

    assert status.generation == 2
    assert status.admission_mode.value == "draining"
    assert status.trust_state.value == "revoked"
    assert status.accepting_new_runs is False
    assert _activate(replacement).accepting_new_runs is False
    restored = _restore(replacement)
    assert restored.admission_mode.value == "active"
    assert restored.trust_state.value == "trusted"
    assert restored.accepting_new_runs is True


def test_executor_status_projection_never_opens_a_second_capacity_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    placement = _reserve_admitted(pool, fleet_run_id="run-one-cut", plan=_plan(), target_count=1)

    def reject_split_read(_self, _executor_id: str):
        raise AssertionError("executor status opened a second capacity read")

    monkeypatch.setattr(
        FleetExecutorCapacityReservations, "active_fleet_run_ids", reject_split_read
    )

    snapshot = pool.executor_snapshot().executors[0]
    assert snapshot.active_fleet_run_ids == ("run-one-cut",)
    drained = _drain(pool)
    assert drained.admission_mode.value == "draining"
    assert drained.active_fleet_run_ids == ("run-one-cut",)
    _release(tmp_path, placement)


def test_executor_snapshot_reports_live_bound_runs_for_drain_readiness(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    placement = _reserve_admitted(pool, fleet_run_id="run-visible", plan=_plan(), target_count=1)

    status = pool.executor_snapshot().executors[0]
    assert status.active_fleet_run_ids == ("run-visible",)

    _release(tmp_path, placement)
    assert pool.executor_snapshot().executors[0].active_fleet_run_ids == ()


def test_drain_removes_executor_from_new_placement_without_rewriting_existing_run(
    tmp_path: Path,
) -> None:
    preferred = _candidate(tmp_path, "executor-a", priority=0)
    fallback = _candidate(tmp_path, "executor-b", priority=1)
    pool = _pool(tmp_path, preferred, fallback)
    admitted = _reserve_admitted(
        pool, fleet_run_id="run-before-drain", plan=_plan(), target_count=1
    )
    assert admitted.executor_capacity.executor_id == "executor-a"

    _drain(pool)
    newly_admitted = _reserve_admitted(
        pool, fleet_run_id="run-after-drain", plan=_plan(), target_count=1
    )

    assert newly_admitted.executor_capacity.executor_id == "executor-b"
    rebound = pool.require_bound(admitted, _plan())
    assert isolation_backend_evidence(rebound) == admitted.isolation_backend
    _release(tmp_path, admitted)
    _release(tmp_path, newly_admitted)


def test_operator_state_is_private_sqlite_coordination_without_product_revision(
    tmp_path: Path,
) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    database = _database(tmp_path)
    before = database.durable_revision()

    _drain(pool)
    _revoke(pool)

    assert database.durable_revision() == before
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT admission_mode, trust_state, revision, trust_epoch "
            "FROM executor_operational_state WHERE executor_id = ?",
            ("executor-a",),
        ).fetchone()
    assert row is not None
    assert row["admission_mode"] == "draining"
    assert row["trust_state"] == "revoked"
    assert row["revision"] == 2
    assert row["trust_epoch"] == 1
    assert not list(tmp_path.rglob("*.json"))


def test_failed_final_admission_rolls_back_capacity_and_consumes_no_ticket(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))

    def reject(_connection, _placement):
        raise RuntimeError("reject final admission")

    with pytest.raises(RuntimeError, match="reject final admission"):
        pool.admit_strict_with(
            fleet_run_id="run-rollback-final",
            plan=_plan(),
            targets=_targets(1),
            commit=reject,
        )

    assert pool.placement_queue().entries == ()
    assert pool.executor_snapshot().executors[0].active_fleet_run_ids == ()


def test_executor_control_rejects_stale_generation(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0, generation=2))
    status = pool.executor_snapshot().executors[0]
    with pytest.raises(Exception, match="generation changed"):
        pool.drain_executor(
            "executor-a", expected_generation=1, expected_revision=status.operational_revision
        )


def test_executor_control_rejects_stale_operational_revision(tmp_path: Path) -> None:
    pool = _pool(tmp_path, _candidate(tmp_path, "executor-a", priority=0))
    status = pool.executor_snapshot().executors[0]
    _drain(pool)
    with pytest.raises(Exception, match="operational state is stale"):
        pool.activate_executor(
            "executor-a",
            expected_generation=status.generation,
            expected_revision=status.operational_revision,
        )


def test_static_resource_impossibility_rejects_before_placement_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    undersized = _candidate(
        tmp_path,
        "executor-too-small",
        priority=0,
        cpu=500,
        memory=512,
    )
    pool = _pool(tmp_path, undersized, wait_seconds=60)
    monkeypatch.setattr(
        "oh_goon.runtime._executor_placement_queue.ExecutorPlacementQueue.enter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("static executor impossibility must reject before queue entry")
        ),
    )

    with pytest.raises(FleetPlanInvalid, match="before queueing"):
        _reserve_admitted(
            pool,
            fleet_run_id="run-static-impossible",
            plan=_plan(cpu=1000, memory=1024),
            target_count=1,
        )

    assert pool.placement_queue().entries == ()


def test_placement_wait_uses_injected_monotonic_clock_and_bounded_wait() -> None:
    from oh_goon.runtime._executor_placement import _wait_for_placement_turn

    waits: list[float] = []
    _wait_for_placement_turn(10.0, monotonic=lambda: 9.98, wait=waits.append)
    assert waits == pytest.approx([0.02])

    with pytest.raises(Exception, match="expired behind an earlier admission request"):
        _wait_for_placement_turn(10.0, monotonic=lambda: 10.0, wait=waits.append)
