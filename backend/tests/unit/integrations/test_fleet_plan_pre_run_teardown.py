from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    PathExistsCondition,
    PathMissingCondition,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    StepFailurePolicy,
    StepLifecycle,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
    PlanStepWorkspace,
    StepPlanCacheSource,
    StepWorkspaceSource,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from tests.factories.runtime_authority import authorized_command_result
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness


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
        return "pre-run-teardown"

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
        name="pre-run-teardown",
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


def _repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    return repository


def test_unavailable_workspace_source_still_releases_finalizer_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    releases: list[str] = []
    original_release = PreparedPlanRun.release_step

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        code = 7 if argv[-1] == "producer" else 0
        return authorized_command_result(argv=tuple(argv), returncode=code, stdout="", stderr="")

    def record_release(self: PreparedPlanRun, step):
        releases.append(step.step_id)
        return original_release(self, step)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        on_failure=StepFailurePolicy.CONTINUE,
        workspace=PlanStepWorkspace(publish=True),
    )
    finalizer = ResolvedCommandStep(
        step_id="finalizer",
        argv=("true", "finalizer"),
        lifecycle=StepLifecycle.FINALIZER,
        on_failure=StepFailurePolicy.CONTINUE,
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )

    with pytest.raises(FleetPlanInvalid, match="finalizer requires unavailable workspace producer"):
        _executor().execute(repository, _cicd(producer, finalizer), cancel_requested=None)

    assert releases == ["producer", "finalizer"]


def test_unavailable_cache_after_attempt_creation_is_cleaned_and_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    releases: list[str] = []
    original_release = PreparedPlanRun.release_step

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        code = 9 if argv[-1] == "producer" else 0
        return authorized_command_result(argv=tuple(argv), returncode=code, stdout="", stderr="")

    def record_release(self: PreparedPlanRun, step):
        evidence = original_release(self, step)
        releases.append(step.step_id)
        if step.step_id == "finalizer":
            assert not self._layout.attempt_root(step.step_id, 1).exists()
        return evidence

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        on_failure=StepFailurePolicy.CONTINUE,
        cache=PlanStepCache(publish=True),
    )
    finalizer = ResolvedCommandStep(
        step_id="finalizer",
        argv=("true", "finalizer"),
        lifecycle=StepLifecycle.FINALIZER,
        on_failure=StepFailurePolicy.CONTINUE,
        cache=PlanStepCache(source=StepPlanCacheSource(step_id="producer")),
    )

    with pytest.raises(FleetPlanInvalid, match="finalizer requires unavailable cache producer"):
        _executor().execute(repository, _cicd(producer, finalizer), cancel_requested=None)

    assert releases == ["producer", "finalizer"]


def test_false_condition_skips_before_unavailable_cache_is_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        return authorized_command_result(
            argv=tuple(argv),
            returncode=5 if argv[-1] == "producer" else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        on_failure=StepFailurePolicy.CONTINUE,
        cache=PlanStepCache(publish=True),
    )
    skipped = ResolvedCommandStep(
        step_id="skipped",
        argv=("true", "skipped"),
        when=PathExistsCondition(path="condition-does-not-exist"),
        cache=PlanStepCache(source=StepPlanCacheSource(step_id="producer")),
    )

    outcome = _executor().execute(repository, _cicd(producer, skipped), cancel_requested=None)

    assert launched == ["producer"]
    assert [step.status for step in outcome.steps] == ["FAILED", "SKIPPED"]


def test_path_conditions_follow_base_and_published_workspace_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    (repository / "base-present.txt").write_text("base\n", encoding="utf-8")
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        cwd = Path(kwargs["cwd"])
        if argv[-1] == "producer":
            (cwd / "base-present.txt").unlink()
            (cwd / "published-present.txt").write_text("published\n", encoding="utf-8")
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    base_exists = ResolvedCommandStep(
        step_id="base-exists",
        argv=("true", "base-exists"),
        when=PathExistsCondition(path="base-present.txt"),
    )
    base_missing = ResolvedCommandStep(
        step_id="base-missing",
        argv=("true", "base-missing"),
        when=PathMissingCondition(path="base-never-present.txt"),
    )
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    published_exists = ResolvedCommandStep(
        step_id="published-exists",
        argv=("true", "published-exists"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
        when=PathExistsCondition(path="published-present.txt"),
    )
    published_missing = ResolvedCommandStep(
        step_id="published-missing",
        argv=("true", "published-missing"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
        when=PathMissingCondition(path="base-present.txt"),
    )

    outcome = _executor().execute(
        repository,
        _cicd(base_exists, base_missing, producer, published_exists, published_missing),
        cancel_requested=None,
    )

    assert launched == [
        "base-exists",
        "base-missing",
        "producer",
        "published-exists",
        "published-missing",
    ]
    assert [step.status for step in outcome.steps] == ["COMPLETED"] * 5
