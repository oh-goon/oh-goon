from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import PlanStepWorkspace, StepWorkspaceSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.content_snapshot import snapshot_identity
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import (
    TEST_ISOLATION_BACKEND,
    PhysicalFleetPlanExecutorHarness,
    prepare_physical_test_run,
)
from tests.factories.path_authority import TEST_PATH_AUTHORITY


class _UnitProcessIsolation:
    capabilities = IsolationBackendCapabilities(
        backend_id="unit-strict",
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
        return "process-isolation-backend-unit-strict"

    def require_available(self, plan) -> None:
        return None

    def execute(self, request, *, timeout, cancel_requested, process_observer=None):
        import oh_goon.integrations.fleet_plan_step_execution as step_execution

        assert request.attempt.resources is not None
        write_remote_resource_evidence(request.attempt.paths.root, request.attempt.resources)
        return step_execution.run_command(
            *request.argv,
            timeout=timeout,
            cwd=Path(request.cwd),
            env=dict(request.env),
            cancel_requested=cancel_requested,
        )


def _cicd(*steps: ResolvedCommandStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="authority-guards",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=steps,
    )


def _executor() -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_UnitProcessIsolation(),)
    )


def test_full_strict_path_rejects_frozen_source_mutation_between_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "version.txt").write_text("v1", encoding="utf-8")
    first = ResolvedCommandStep(timeout_seconds=3600, step_id="first", argv=("true", "first"))
    second = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="second",
        argv=("true", "second"),
        workspace=PlanStepWorkspace(publish=True),
    )
    launched: list[tuple[str, ...]] = []
    attempted_destinations: list[Path] = []
    published: list[str] = []
    original_release = PreparedPlanRun.release_step
    original_publish = PreparedPlanRun.publish_workspace
    from oh_goon.integrations import fleet_plan_run as fleet_plan_run_module

    original_copy = fleet_plan_run_module.copy_frozen_directory

    from oh_goon.integrations import fleet_plan_step_execution as step_execution

    original_run_command = step_execution.run_command

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(tuple(argv))
        return original_run_command(*argv, **kwargs)

    def release_then_corrupt(self: PreparedPlanRun, step):
        evidence = original_release(self, step)
        if step.step_id == "first":
            (self.base_workspace / "version.txt").write_text("corrupted", encoding="utf-8")
        return evidence

    def record_copy(source: Path, destination: Path, **kwargs):
        attempted_destinations.append(Path(destination))
        return original_copy(source, destination, **kwargs)

    def record_publish(self: PreparedPlanRun, step, workspace: Path) -> None:
        if step.workspace.publish:
            published.append(step.step_id)
        original_publish(self, step, workspace)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "release_step", release_then_corrupt)
    monkeypatch.setattr("oh_goon.integrations.fleet_plan_run.copy_frozen_directory", record_copy)
    monkeypatch.setattr(PreparedPlanRun, "publish_workspace", record_publish)

    with pytest.raises(FleetPlanInvalid, match="source changed while creating attempt"):
        _executor().execute(repository, _cicd(first, second), cancel_requested=None)

    assert launched == [("true", "first")]
    assert published == []
    assert len(attempted_destinations) == 2
    assert not attempted_destinations[-1].exists()


def test_source_lineage_transition_changes_exact_source_binding(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    (repository / "lineage.txt").write_text("base\n", encoding="utf-8")
    producer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="consumer",
        argv=("true", "consumer"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )
    base_again = ResolvedCommandStep(
        timeout_seconds=3600,
        step_id="base-again",
        argv=("true", "base-again"),
    )

    with prepare_physical_test_run(
        RepositoryIdentity(path=repository),
        _cicd(producer, consumer, base_again),
        isolation_backend=TEST_ISOLATION_BACKEND,
        path_authority=TEST_PATH_AUTHORITY,
    ) as session:
        base_source = session.source_for(producer)
        base_identity = snapshot_identity(base_source)
        attempt = session.create_attempt(producer, 1)
        (attempt.paths.workspace / "lineage.txt").write_text("published\n", encoding="utf-8")
        session.publish_workspace(producer, attempt.paths.workspace)
        session.release_step(producer)

        published_source = session.source_for(consumer)
        published_identity = snapshot_identity(published_source)
        assert published_source != base_source
        assert published_identity != base_identity

        session.release_step(consumer)
        returned_source = session.source_for(base_again)
        assert returned_source == base_source
        assert snapshot_identity(returned_source) == base_identity
