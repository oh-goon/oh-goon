from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from oh_goon.core.models.fleet_plan import ResolvedFleetPlan, ResolvedPlanStep
from oh_goon.core.models.fleet_plan_evidence import (
    PlanIsolationBackendEvidence,
    PlanSnapshotEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.integrations.fleet_plan_process_isolation import (
    ProcessIsolationBackend,
    isolation_backend_evidence,
)
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from oh_goon.integrations.fleet_plan_run_execution import (
    FleetPlanRunExecutor as ProductionFleetPlanRunExecutor,
)
from oh_goon.integrations.fleet_plan_run_execution import (
    PlanExecutionOutcome,
)
from oh_goon.integrations.source_snapshot import source_snapshot_identity
from tests.factories.path_authority import test_path_authority_for_plan

TEST_ISOLATION_BACKEND = PlanIsolationBackendEvidence(
    backend_id="test-isolation",
    configuration_id="tests-explicit-isolation-backend",
    kernel_boundary="shared_host_kernel",
    remote_execution=False,
    experimental=False,
)


class _PreparedRunProcessIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="test-isolation",
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
        kernel_boundary=IsolationKernelBoundary.SHARED_HOST_KERNEL,
    )

    def configuration_identity(self) -> str:
        return TEST_ISOLATION_BACKEND.configuration_id

    def require_available(self, plan: ResolvedFleetPlan) -> None:
        return None

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        raise AssertionError(
            "prepare_physical_test_run process backend is not an execution harness"
        )


_TEST_PROCESS_ISOLATION = _PreparedRunProcessIsolation()


def source_authority_for_test(repository: RepositoryIdentity) -> RepositorySourceAuthority:
    source = source_snapshot_identity(repository.path)
    return RepositorySourceAuthority.create(
        subject_instance_id=repository_subject_instance_id(repository.path),
        source_snapshot=PlanSnapshotEvidence(
            sha256=source.sha256,
            byte_count=source.byte_count,
            entry_count=source.entry_count,
        ),
    )


def prepare_physical_test_run(
    repository: RepositoryIdentity,
    plan: ResolvedFleetPlan,
    **kwargs: Any,
) -> PreparedPlanRun:
    """Build a strict prepared run for low-level run/attempt tests."""

    if "source_authority" not in kwargs:
        kwargs["source_authority"] = source_authority_for_test(repository)
    kwargs.setdefault("process_isolation", _TEST_PROCESS_ISOLATION)
    kwargs.setdefault("isolation_backend", TEST_ISOLATION_BACKEND)
    if "path_authority" not in kwargs:
        kwargs["path_authority"] = test_path_authority_for_plan(plan)
    return PreparedPlanRun(repository, plan, **kwargs)


def strict_test_plan(*steps: ResolvedPlanStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="strict-test-plan",
        description="",
        execution=CicdExecutionPolicy(),
        requirements=(),
        steps=tuple(steps),
    )


class PhysicalFleetPlanExecutorHarness(ProductionFleetPlanRunExecutor):
    """Test-only one-call harness around the production prepare/execute lifecycle."""

    def __init__(
        self,
        *args: object,
        process_isolation_backends: tuple[ProcessIsolationBackend, ...] = (),
        **kwargs: Any,
    ) -> None:
        self._plan_scoped_path_authority = "path_authority" not in kwargs
        self._admitted_backends = process_isolation_backends
        super().__init__(
            *args,
            process_isolation_backends=process_isolation_backends,
            **kwargs,
        )

    def prepare_strict_run(
        self,
        repository: RepositoryIdentity,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
        admitted_source_authority: RepositorySourceAuthority | None = None,
        admitted_isolation_backend: PlanIsolationBackendEvidence | None,
    ) -> PreparedPlanRun:
        if admitted_source_authority is None:
            admitted_source_authority = source_authority_for_test(repository)
        if not self._plan_scoped_path_authority:
            return super().prepare_strict_run(
                repository,
                plan,
                cancel_requested=cancel_requested,
                admitted_source_authority=admitted_source_authority,
                admitted_isolation_backend=admitted_isolation_backend,
            )
        previous = self._path_authority
        self._path_authority = test_path_authority_for_plan(plan)
        try:
            return super().prepare_strict_run(
                repository,
                plan,
                cancel_requested=cancel_requested,
                admitted_source_authority=admitted_source_authority,
                admitted_isolation_backend=admitted_isolation_backend,
            )
        finally:
            self._path_authority = previous

    def admitted_isolation_backend(self) -> PlanIsolationBackendEvidence:
        if len(self._admitted_backends) != 1:
            raise AssertionError(
                "Strict test execution must declare exactly one backend to simulate prior Fleet admission"
            )
        return isolation_backend_evidence(self._admitted_backends[0])

    def execute(
        self,
        repository: Path,
        plan: ResolvedFleetPlan,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> PlanExecutionOutcome:
        if not isinstance(plan.execution, CicdExecutionPolicy):
            self.prepare_direct_run(
                RepositoryIdentity(path=repository),
                plan,
                cancel_requested=cancel_requested,
            )
            return self.execute_direct_run(
                repository,
                plan,
                cancel_requested=cancel_requested,
            )

        prepared_run = self.prepare_strict_run(
            RepositoryIdentity(path=repository),
            plan,
            cancel_requested=cancel_requested,
            admitted_isolation_backend=self.admitted_isolation_backend(),
        )
        try:
            return self.execute_strict_run(
                repository,
                plan,
                prepared_run,
                cancel_requested=cancel_requested,
            )
        finally:
            prepared_run.close()
