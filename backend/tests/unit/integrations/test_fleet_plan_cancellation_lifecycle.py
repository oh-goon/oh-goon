from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.models.fleet_plan import (
    ResolvedCommandStep,
    ResolvedFleetPlan,
    StepFailurePolicy,
    StepRetryPolicy,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.engine.command import CommandCancelledError, CommandResult, CommandSupervisorReceipt
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations.fleet_plan_run import PreparedPlanRun
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
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
        return "cancellation-lifecycle"

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


def _plan(step: ResolvedCommandStep) -> ResolvedFleetPlan:
    return ResolvedFleetPlan.create(
        name="cancellation-lifecycle",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(step,),
    )


def _executor() -> PhysicalFleetPlanExecutorHarness:
    return PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_UnitProcessIsolation(),)
    )


def _repo(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    for index in range(40):
        path = repository / "payload" / f"item-{index:03d}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"payload-{index}\n", encoding="utf-8")
    return repository


def test_cancellation_during_attempt_materialization_never_launches_and_tears_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    step = ResolvedCommandStep(step_id="copy-cancel", argv=("true", "copy-cancel"))
    launched: list[tuple[str, ...]] = []
    releases: list[str] = []
    original_copy = __import__(
        "oh_goon.integrations.content_snapshot", fromlist=["_copy_tree"]
    )._copy_tree
    original_release = PreparedPlanRun.release_step

    def cancel_copy(source: Path, destination: Path, *, cancel_requested, label: str) -> None:
        if "source workspace" not in label:
            return original_copy(
                source,
                destination,
                cancel_requested=cancel_requested,
                label=label,
            )
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "partial.txt").write_text("partial\n", encoding="utf-8")
        raise OperationCancelledError("cancelled during attempt materialization")

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        launched.append(tuple(argv))
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="")

    def record_release(self: PreparedPlanRun, resolved_step):
        evidence = original_release(self, resolved_step)
        releases.append(resolved_step.step_id)
        assert not self._layout.attempt_root(resolved_step.step_id, 1).exists()
        return evidence

    monkeypatch.setattr("oh_goon.integrations.content_snapshot._copy_tree", cancel_copy)
    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)

    with pytest.raises(OperationCancelledError, match="attempt materialization"):
        _executor().execute(repository, _plan(step), cancel_requested=None)

    assert launched == []
    assert releases == ["copy-cancel"]


def test_cancellation_during_retry_delay_never_launches_next_attempt_and_tears_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repo(tmp_path)
    step = ResolvedCommandStep(
        step_id="retry-cancel",
        argv=("true", "retry-cancel"),
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(9,),
            reason="test transient before cancellation",
            delays_seconds=(1,),
        ),
    )
    calls = 0
    releases: list[str] = []
    original_release = PreparedPlanRun.release_step

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(argv=tuple(argv), returncode=9, stdout="attempt failed", stderr="")

    def cancel_wait(delay_seconds: float, cancel_requested) -> None:
        assert delay_seconds == 1
        raise OperationCancelledError("cancelled during retry delay")

    def record_release(self: PreparedPlanRun, resolved_step):
        releases.append(resolved_step.step_id)
        return original_release(self, resolved_step)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    monkeypatch.setattr("oh_goon.integrations._fleet_plan_step_retry.wait_for_retry", cancel_wait)
    monkeypatch.setattr(PreparedPlanRun, "release_step", record_release)

    with pytest.raises(OperationCancelledError, match="retry delay"):
        _executor().execute(repository, _plan(step), cancel_requested=lambda: False)

    assert calls == 1
    assert releases == ["retry-cancel"]


@pytest.mark.parametrize("quiescent", [True, False])
def test_native_cancellation_preserves_causal_cleanup_without_post_cancel_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quiescent: bool
) -> None:
    from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
    from oh_goon.core.models.fleet_plan_step_primitives import StepLifecycle

    repository = _repo(tmp_path)
    original = GitService().observe(repository)
    observations = 0
    commands: list[tuple[str, ...]] = []
    events = []

    def observe(self, *args, **kwargs):
        nonlocal observations
        observations += 1
        assert observations == 1, "A repository helper launched after cancellation"
        return original

    def cancel(*argv, **kwargs):
        commands.append(argv)
        raise CommandCancelledError(
            "cancelled with private control diagnostics",
            stdout="PRIVATE_MODEL_OUTPUT",
            stderr="PRIVATE_NATIVE_DIAGNOSTIC",
            physical_execution_id="original-physical-execution",
            supervisor=CommandSupervisorReceipt(
                process_group_cleanup_requested=True,
                cleanup_reason="cancel",
                final_process_group_quiescent=quiescent,
            ),
        )

    def lifecycle(step_id, step_order, event_type, **fields):
        events.append((event_type, fields))

    monkeypatch.setattr(GitService, "observe", observe)
    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", cancel)
    plan = ResolvedFleetPlan.create(
        name="cancelled-source",
        description="",
        execution=MaintenanceExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                step_id="cancel-me",
                argv=("native-work",),
            ),
            ResolvedCommandStep(
                step_id="finalize",
                argv=("must-not-run",),
                lifecycle=StepLifecycle.FINALIZER,
                on_failure=StepFailurePolicy.CONTINUE,
            ),
        ),
    )
    with pytest.raises(OperationCancelledError):
        FleetPlanRunExecutor(GitService(), process_isolation_backends=()).execute_direct_run(
            repository, plan, cancel_requested=lambda: False, lifecycle_observer=lifecycle
        )
    assert commands == [("native-work",)]
    cancellations = [fields["cancellation"] for kind, fields in events if kind == "STEP_CANCELLED"]
    assert len(cancellations) == 1
    evidence = cancellations[0]
    assert evidence.settlement.outcome == ("CANCELLED" if quiescent else "INCOMPLETE")
    assert evidence.settlement.cleanup == ("VERIFIED" if quiescent else "UNVERIFIED")
    assert evidence.physical_execution_id == "original-physical-execution"
    assert evidence.supervisor.final_process_group_quiescent is quiescent
    assert evidence.standard_isolation is None
    assert "PRIVATE_MODEL_OUTPUT" not in evidence.model_dump_json()
    assert "PRIVATE_NATIVE_DIAGNOSTIC" not in evidence.model_dump_json()
