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
    StepStatusCondition,
)
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepTeardownEvidence
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.core.models.fleet_plan_step_contract import (
    PlanStepCache,
    PlanStepWorkspace,
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
        return "second-pass-maturity"

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
        name="second-pass-maturity",
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


def test_stop_halts_normal_condition_but_finalizer_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        code = 17 if argv[-1] == "fail" else 0
        return authorized_command_result(argv=tuple(argv), returncode=code, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    failed = ResolvedCommandStep(step_id="failed", argv=("true", "fail"))
    blocked = ResolvedCommandStep(
        step_id="blocked",
        argv=("true", "blocked"),
        when=StepStatusCondition(step_id="failed", statuses=("FAILED",)),
    )
    finalizer = ResolvedCommandStep(
        step_id="finalizer",
        argv=("true", "finalizer"),
        lifecycle=StepLifecycle.FINALIZER,
        on_failure=StepFailurePolicy.CONTINUE,
        when=StepStatusCondition(step_id="failed", statuses=("FAILED",)),
    )

    outcome = _executor().execute(repository, _cicd(failed, blocked, finalizer), cancel_requested=None)

    assert launched == ["fail", "finalizer"]
    assert [step.status for step in outcome.steps] == ["FAILED", "SKIPPED", "COMPLETED"]
    assert "earlier stop-policy failure" in outcome.steps[1].reason
    assert outcome.steps[0].status == "FAILED"


def test_teardown_failure_halts_even_finalizers_and_records_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []
    original_release = PreparedPlanRun.release_step

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    def fail_first_teardown(self: PreparedPlanRun, step):
        evidence = original_release(self, step)
        if step.step_id == "first":
            return FailedPlanStepTeardownEvidence(
                attempt_roots_observed=evidence.attempt_roots_observed,
                reason_code="ROOT_REMAINS",
            )
        return evidence

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "release_step", fail_first_teardown)
    first = ResolvedCommandStep(step_id="first", argv=("true", "first"))
    finalizer = ResolvedCommandStep(
        step_id="finalizer",
        argv=("true", "finalizer"),
        lifecycle=StepLifecycle.FINALIZER,
        on_failure=StepFailurePolicy.CONTINUE,
    )

    outcome = _executor().execute(repository, _cicd(first, finalizer), cancel_requested=None)

    assert launched == ["first"]
    assert outcome.steps[0].teardown.status == "FAILED"
    assert outcome.steps[1].status == "SKIPPED"
    assert "teardown failed" in outcome.steps[1].reason.lower()


def test_multiple_finalizers_continue_after_one_finalizer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        code = 9 if argv[-1] == "finalizer-2" else 0
        return authorized_command_result(argv=tuple(argv), returncode=code, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    finalizers = tuple(
        ResolvedCommandStep(
            step_id=f"f{index}",
            argv=("true", f"finalizer-{index}"),
            lifecycle=StepLifecycle.FINALIZER,
            on_failure=StepFailurePolicy.CONTINUE,
        )
        for index in (1, 2, 3)
    )

    outcome = _executor().execute(repository, _cicd(*finalizers), cancel_requested=None)

    assert launched == ["finalizer-1", "finalizer-2", "finalizer-3"]
    assert [step.status for step in outcome.steps] == ["COMPLETED", "FAILED", "COMPLETED"]


def test_step_status_conditions_cover_pass_fail_continue_and_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        return authorized_command_result(
            argv=tuple(argv),
            returncode=5 if argv[-1] == "fail-continue" else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    passed = ResolvedCommandStep(step_id="passed", argv=("true", "passed"))
    after_pass = ResolvedCommandStep(
        step_id="after-pass",
        argv=("true", "after-pass"),
        when=StepStatusCondition(step_id="passed", statuses=("COMPLETED",)),
    )
    failed = ResolvedCommandStep(
        step_id="failed",
        argv=("true", "fail-continue"),
        on_failure=StepFailurePolicy.CONTINUE,
    )
    after_fail = ResolvedCommandStep(
        step_id="after-fail",
        argv=("true", "after-fail"),
        when=StepStatusCondition(step_id="failed", statuses=("FAILED",)),
    )
    skipped = ResolvedCommandStep(
        step_id="skipped",
        argv=("true", "never"),
        when=PathExistsCondition(path="missing.condition.file"),
    )
    after_skip = ResolvedCommandStep(
        step_id="after-skip",
        argv=("true", "after-skip"),
        when=StepStatusCondition(step_id="skipped", statuses=("SKIPPED",)),
    )

    outcome = _executor().execute(
        repository,
        _cicd(passed, after_pass, failed, after_fail, skipped, after_skip),
        cancel_requested=None,
    )

    assert launched == ["passed", "after-pass", "fail-continue", "after-fail", "after-skip"]
    assert [step.status for step in outcome.steps] == [
        "COMPLETED",
        "COMPLETED",
        "FAILED",
        "COMPLETED",
        "SKIPPED",
        "COMPLETED",
    ]


def test_path_condition_uses_declared_published_source_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        cwd = Path(kwargs["cwd"])
        if argv[-1] == "producer":
            (cwd / "published.marker").write_text("producer\n", encoding="utf-8")
        if argv[-1] == "consumer":
            assert (cwd / "published.marker").read_text(encoding="utf-8") == "producer\n"
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer = ResolvedCommandStep(
        step_id="consumer",
        argv=("true", "consumer"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
        when=PathExistsCondition(path="published.marker"),
    )
    base = ResolvedCommandStep(
        step_id="base",
        argv=("true", "base"),
        when=PathExistsCondition(path="published.marker"),
    )

    outcome = _executor().execute(repository, _cicd(producer, consumer, base), cancel_requested=None)

    assert launched == ["producer", "consumer"]
    assert outcome.steps[1].status == "COMPLETED"
    assert outcome.steps[2].status == "SKIPPED"


def test_skipped_first_consumer_does_not_release_workspace_before_second_consumer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        cwd = Path(kwargs["cwd"])
        if argv[-1] == "producer":
            (cwd / "lineage.txt").write_text("published\n", encoding="utf-8")
        if argv[-1] == "consumer-2":
            assert (cwd / "lineage.txt").read_text(encoding="utf-8") == "published\n"
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer"),
        workspace=PlanStepWorkspace(publish=True),
    )
    consumer_1 = ResolvedCommandStep(
        step_id="consumer-1",
        argv=("true", "consumer-1"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
        when=PathMissingCondition(path="lineage.txt"),
    )
    consumer_2 = ResolvedCommandStep(
        step_id="consumer-2",
        argv=("true", "consumer-2"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )

    outcome = _executor().execute(
        repository, _cicd(producer, consumer_1, consumer_2), cancel_requested=None
    )

    assert launched == ["producer", "consumer-2"]
    assert outcome.steps[1].status == "SKIPPED"
    assert outcome.steps[2].status == "COMPLETED"


def test_failed_continue_consumer_does_not_replace_shared_published_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        cwd = Path(kwargs["cwd"])
        if argv[-1] == "producer":
            (cwd / "lineage.txt").write_text("published\n", encoding="utf-8")
            return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")
        if argv[-1] == "consumer-1":
            (cwd / "lineage.txt").write_text("failed-attempt\n", encoding="utf-8")
            return authorized_command_result(argv=tuple(argv), returncode=23, stdout="", stderr="")
        assert (cwd / "lineage.txt").read_text(encoding="utf-8") == "published\n"
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer", argv=("true", "producer"), workspace=PlanStepWorkspace(publish=True)
    )
    consumer_1 = ResolvedCommandStep(
        step_id="consumer-1",
        argv=("true", "consumer-1"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
        on_failure=StepFailurePolicy.CONTINUE,
    )
    consumer_2 = ResolvedCommandStep(
        step_id="consumer-2",
        argv=("true", "consumer-2"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )

    outcome = _executor().execute(
        repository, _cicd(producer, consumer_1, consumer_2), cancel_requested=None
    )

    assert launched == ["producer", "consumer-1", "consumer-2"]
    assert [step.status for step in outcome.steps] == ["COMPLETED", "FAILED", "COMPLETED"]


def test_finalizer_declared_as_last_workspace_consumer_keeps_lineage_alive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        cwd = Path(kwargs["cwd"])
        if argv[-1] == "producer":
            (cwd / "lineage.txt").write_text("published\n", encoding="utf-8")
        elif argv[-1] in {"consumer", "finalizer"}:
            assert (cwd / "lineage.txt").read_text(encoding="utf-8") == "published\n"
        return authorized_command_result(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer", argv=("true", "producer"), workspace=PlanStepWorkspace(publish=True)
    )
    consumer = ResolvedCommandStep(
        step_id="consumer",
        argv=("true", "consumer"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )
    finalizer = ResolvedCommandStep(
        step_id="finalizer",
        argv=("true", "finalizer"),
        lifecycle=StepLifecycle.FINALIZER,
        on_failure=StepFailurePolicy.CONTINUE,
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
    )

    outcome = _executor().execute(
        repository, _cicd(producer, consumer, finalizer), cancel_requested=None
    )

    assert launched == ["producer", "consumer", "finalizer"]
    assert [step.status for step in outcome.steps] == ["COMPLETED", "COMPLETED", "COMPLETED"]


def test_finalizer_with_unavailable_published_workspace_fails_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        return authorized_command_result(
            argv=tuple(argv),
            returncode=17 if argv[-1] == "producer-fails" else 0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer-fails"),
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

    assert launched == ["producer-fails"]


def test_condition_false_step_with_unavailable_workspace_resolves_source_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    launched: list[str] = []

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(argv[-1])
        return authorized_command_result(argv=tuple(argv), returncode=19, stdout="", stderr="")

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    producer = ResolvedCommandStep(
        step_id="producer",
        argv=("true", "producer-fails-continue"),
        on_failure=StepFailurePolicy.CONTINUE,
        workspace=PlanStepWorkspace(publish=True),
    )
    condition_false = ResolvedCommandStep(
        step_id="condition-false",
        argv=("true", "must-not-launch"),
        workspace=PlanStepWorkspace(source=StepWorkspaceSource(step_id="producer")),
        when=PathExistsCondition(path="never-created.marker"),
    )

    with pytest.raises(
        FleetPlanInvalid, match="condition-false requires unavailable workspace producer"
    ):
        _executor().execute(repository, _cicd(producer, condition_false), cancel_requested=None)

    assert launched == ["producer-fails-continue"]
