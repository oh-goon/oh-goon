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
from oh_goon.core.models.fleet_plan_step_contract import PlanStepCache, PlanStepWorkspace
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from tests.factories.cicd_repository import seed_cicd_repository
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness
from tests.factories.runtime_authority import authorized_command_result


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
        return "publication-transaction"

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
        name="publication-transaction",
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


def test_workspace_publication_failure_is_step_failure_and_always_releases_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    releases: list[str] = []
    original_release = PreparedPlanRun.release_step

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        return authorized_command_result(
            argv=tuple(argv), returncode=0, stdout="command passed", stderr=""
        )

    def fail_publish(self: PreparedPlanRun, step, workspace: Path) -> None:
        raise FleetPlanInvalid("synthetic workspace publication failure")

    def record_release(self: PreparedPlanRun, step):
        releases.append(step.step_id)
        return original_release(self, step)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "publish_workspace", fail_publish)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)
    step = ResolvedCommandStep(
        step_id="publish", argv=("true", "publish"), workspace=PlanStepWorkspace(publish=True)
    )

    outcome = _executor().execute(repository, _cicd(step), cancel_requested=None)

    assert releases == ["publish"]
    assert len(outcome.steps) == 1
    assert outcome.steps[0].status == "FAILED"
    assert outcome.steps[0].returncode != 0
    assert "workspace publication failure" in outcome.stderr
    assert "command passed" in outcome.stdout


def test_cache_publication_failure_rolls_back_workspace_and_releases_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    releases: list[str] = []
    rollbacks: list[str] = []
    original_release = PreparedPlanRun.release_step
    original_discard = PreparedPlanRun.discard_publications

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cwd = Path(kwargs["cwd"])
        (cwd / "published-before-cache-failure.txt").write_text("attempt\n", encoding="utf-8")
        return authorized_command_result(
            argv=tuple(argv), returncode=0, stdout="command passed", stderr=""
        )

    def fail_cache(self: PreparedPlanRun, step, cache: Path):
        raise FleetPlanInvalid("synthetic cache publication failure")

    def record_discard(self: PreparedPlanRun, step) -> None:
        rollbacks.append(step.step_id)
        original_discard(self, step)
        assert step.step_id not in self._lineage._published
        assert step.step_id not in self._lineage._published_caches

    def record_release(self: PreparedPlanRun, step):
        releases.append(step.step_id)
        return original_release(self, step)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "publish_cache", fail_cache)
    monkeypatch.setattr(PreparedPlanRun, "discard_publications", record_discard)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)
    step = ResolvedCommandStep(
        step_id="publish-both",
        argv=("true", "publish-both"),
        workspace=PlanStepWorkspace(publish=True),
        cache=PlanStepCache(publish=True),
    )

    outcome = _executor().execute(repository, _cicd(step), cancel_requested=None)

    assert rollbacks == ["publish-both"]
    assert releases == ["publish-both"]
    assert len(outcome.steps) == 1
    assert outcome.steps[0].status == "FAILED"
    assert outcome.steps[0].returncode != 0
    assert "cache publication failure" in outcome.stderr
    assert "command passed" in outcome.stdout


def test_unexpected_publication_exception_rolls_back_and_still_releases_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    releases: list[str] = []
    rollbacks: list[str] = []
    original_release = PreparedPlanRun.release_step
    original_discard = PreparedPlanRun.discard_publications

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        return authorized_command_result(
            argv=tuple(argv), returncode=0, stdout="command passed", stderr=""
        )

    def explode(self: PreparedPlanRun, step, workspace: Path) -> None:
        raise RuntimeError("synthetic unexpected publication error")

    def record_discard(self: PreparedPlanRun, step) -> None:
        rollbacks.append(step.step_id)
        original_discard(self, step)

    def record_release(self: PreparedPlanRun, step):
        releases.append(step.step_id)
        return original_release(self, step)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "publish_workspace", explode)
    monkeypatch.setattr(PreparedPlanRun, "discard_publications", record_discard)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)
    step = ResolvedCommandStep(
        step_id="unexpected-publish",
        argv=("true", "unexpected-publish"),
        workspace=PlanStepWorkspace(publish=True),
    )

    with pytest.raises(RuntimeError, match="unexpected publication error"):
        _executor().execute(repository, _cicd(step), cancel_requested=None)

    assert rollbacks == ["unexpected-publish"]
    assert releases == ["unexpected-publish"]


def test_artifact_collection_failure_rolls_back_publication_and_records_step_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    releases: list[str] = []
    rollbacks: list[str] = []
    original_release = PreparedPlanRun.release_step
    original_discard = PreparedPlanRun.discard_publications

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        cwd = Path(kwargs["cwd"])
        (cwd / "artifact.txt").write_text("artifact\n", encoding="utf-8")
        return authorized_command_result(
            argv=tuple(argv), returncode=0, stdout="command passed", stderr=""
        )

    def fail_artifacts(*args, **kwargs):
        raise FleetPlanInvalid("synthetic artifact publication failure")

    def record_discard(self: PreparedPlanRun, step) -> None:
        rollbacks.append(step.step_id)
        original_discard(self, step)
        assert step.step_id not in self._lineage._published
        assert step.step_id not in self._lineage._published_caches

    def record_release(self: PreparedPlanRun, step):
        releases.append(step.step_id)
        return original_release(self, step)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_step_sequence.collect_strict_artifacts", fail_artifacts
    )
    monkeypatch.setattr(PreparedPlanRun, "discard_publications", record_discard)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)
    step = ResolvedCommandStep(
        step_id="artifact",
        argv=("true", "artifact"),
        workspace=PlanStepWorkspace(publish=True),
    )

    outcome = _executor().execute(repository, _cicd(step), cancel_requested=None)

    assert rollbacks == ["artifact"]
    assert releases == ["artifact"]
    assert len(outcome.steps) == 1
    assert outcome.steps[0].status == "FAILED"
    assert outcome.steps[0].returncode != 0
    assert "artifact publication failure" in outcome.stderr
    assert "command passed" in outcome.stdout
