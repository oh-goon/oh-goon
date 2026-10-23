from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    PlanArtifactDeclaration,
    PlanPublishedOutput,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    StepFailurePolicy,
    StepLifecycle,
    StepRetryPolicy,
)
from oh_goon.core.models.fleet_plan_evidence import (
    FleetPlanResult,
    PlanIsolationBackendEvidence,
    PlanStandardIsolationEvidence,
)
from oh_goon.core.models.fleet_plan_execution_policy import (
    CicdExecutionPolicy,
    MaintenanceExecutionPolicy,
)
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.engine.command import (
    CommandOutputLimitExceeded,
    CommandResult,
    CommandSupervisorReceipt,
    CommandTimeoutExpired,
)
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
from oh_goon.integrations._fleet_plan_step_evidence import attempt_evidence
from oh_goon.integrations.fleet_plan_output_limit import bounded_execution_failure_outcome
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
        return "output-contract-edges"

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


def _execute_output_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer,
    *,
    max_bytes: int = 4,
    physical_execution_id: str | None = "physical-test-authorized",
):
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    step = ResolvedCommandStep(
        step_id="output",
        argv=("true", "output"),
        outputs=(PlanPublishedOutput(name="VALUE", path="result.txt", max_bytes=max_bytes),),
    )
    plan = ResolvedFleetPlan.create(
        name="output-contract-edges",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        ),
        requirements=(),
        steps=(step,),
    )

    def fake_run(*argv: str, **kwargs) -> CommandResult:
        writer(Path(kwargs["cwd"]))
        result = authorized_command_result(
            argv=tuple(argv), returncode=0, stdout="command passed", stderr=""
        )
        return replace(result, physical_execution_id=physical_execution_id)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run)
    executor = PhysicalFleetPlanExecutorHarness(
        GitService(), process_isolation_backends=(_UnitProcessIsolation(),)
    )
    return executor.execute(repository, plan, cancel_requested=None)


def test_strict_output_without_physical_execution_identity_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _execute_output_case(
        tmp_path,
        monkeypatch,
        lambda root: (root / "result.txt").write_bytes(b"test"),
        physical_execution_id=None,
    )

    assert result.steps[0].status == "FAILED"
    assert result.steps[0].outputs == ()
    assert "requires physical execution identity" in result.stderr


def test_published_output_exact_max_bytes_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _execute_output_case(
        tmp_path,
        monkeypatch,
        lambda root: (root / "result.txt").write_bytes(b"test"),
    )

    assert result.steps[0].status == "COMPLETED"
    assert result.steps[0].outputs[0].name == "VALUE"
    assert result.steps[0].outputs[0].byte_count == 4


@pytest.mark.parametrize(
    ("writer", "message"),
    [
        (lambda root: (root / "result.txt").write_bytes(b"tests"), "exceeds 4 bytes"),
        (lambda root: (root / "result.txt").write_bytes(b"\xff"), "must be UTF-8 text"),
        (lambda root: (root / "result.txt").write_bytes(b"a\x00b"), "cannot contain NUL bytes"),
        (lambda root: (root / "result.txt").mkdir(), "did not publish required output"),
    ],
)
def test_invalid_published_output_is_failed_step_not_pass_laundering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer, message: str
) -> None:
    result = _execute_output_case(tmp_path, monkeypatch, writer)

    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode != 0
    assert "command passed" in result.stdout
    assert message in result.stderr
    assert result.steps[0].outputs == ()


def test_symlink_cannot_satisfy_regular_published_output_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def writer(root: Path) -> None:
        (root / "target.txt").write_text("test", encoding="utf-8")
        (root / "result.txt").symlink_to("target.txt")

    result = _execute_output_case(tmp_path, monkeypatch, writer)

    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode != 0
    assert "result.txt" in result.stderr
    assert result.steps[0].outputs == ()


def test_published_output_that_becomes_unreadable_is_failed_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.integrations._fleet_plan_step_dataflow as dataflow

    original_open = dataflow.os.open

    def deny_result(path, flags, *args, **kwargs):
        if Path(path).name == "result.txt":
            raise PermissionError("synthetic unreadable output")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(dataflow.os, "open", deny_result)
    result = _execute_output_case(
        tmp_path,
        monkeypatch,
        lambda root: (root / "result.txt").write_bytes(b"test"),
    )

    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode != 0
    assert "cannot read published output VALUE" in result.stderr
    assert result.steps[0].outputs == ()


def test_published_output_disappearing_between_validation_and_open_is_failed_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.integrations._fleet_plan_step_dataflow as dataflow

    original_open = dataflow.os.open
    removed = False

    def remove_before_open(path, flags, *args, **kwargs):
        nonlocal removed
        candidate = Path(path)
        if candidate.name == "result.txt" and not removed:
            removed = True
            candidate.unlink()
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(dataflow.os, "open", remove_before_open)
    result = _execute_output_case(
        tmp_path,
        monkeypatch,
        lambda root: (root / "result.txt").write_bytes(b"test"),
    )

    assert removed is True
    assert result.steps[0].status == "FAILED"
    assert result.steps[0].returncode != 0
    assert "did not publish required output VALUE" in result.stderr
    assert result.steps[0].outputs == ()


@pytest.mark.parametrize(
    "native_returncode,retry,process_quiescent",
    [(0, False, False), (9, False, False), (0, True, False), (9, True, False), (0, False, True)],
)
def test_unsettled_boundary_cannot_publish_retry_or_run_later_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_returncode: int,
    retry: bool,
    process_quiescent: bool,
) -> None:
    repository = tmp_path / "repo"
    seed_cicd_repository(repository)
    work = ResolvedCommandStep(
        step_id="work",
        argv=("true", "work"),
        success_codes=(0,) if retry else (0, 9),
        on_failure=StepFailurePolicy.CONTINUE,
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(PLAN_CONTRACT_RETURN_CODE, 9),
            reason="explicit transient native failure",
            delays_seconds=(0,),
        )
        if retry
        else StepRetryPolicy(),
        outputs=(PlanPublishedOutput(name="VALUE", path="result.txt"),),
        artifacts=(PlanArtifactDeclaration(path="result.txt"),),
    )
    plan = ResolvedFleetPlan.create(
        name="unsettled-process",
        description="",
        execution=CicdExecutionPolicy(
            trust_repository_code=True,
            resources=CicdResourcePolicy(cpu_millis=2000, memory_mib=4096, max_processes=256),
        )
        if retry
        else MaintenanceExecutionPolicy(),
        requirements=(),
        steps=(
            work,
            ResolvedCommandStep(step_id="followup", argv=("true", "followup")),
            ResolvedCommandStep(
                step_id="finalizer",
                argv=("true", "finalizer"),
                lifecycle=StepLifecycle.FINALIZER,
                on_failure=StepFailurePolicy.CONTINUE,
            ),
        ),
    )
    launched = []

    def command(*argv: str, **kwargs) -> CommandResult:
        launched.append(tuple(argv))
        (Path(kwargs["cwd"]) / "result.txt").write_text("unsafe output", encoding="utf-8")
        return replace(
            authorized_command_result(
                argv=tuple(argv),
                returncode=native_returncode,
                stdout="native leader exited",
                stderr="",
            ),
            supervisor=CommandSupervisorReceipt(
                leader_exited_before_group_quiescence=True,
                process_group_cleanup_requested=True,
                cleanup_reason="output_cleanup",
                forced_termination=True,
                final_process_group_quiescent=process_quiescent,
            ),
            standard_isolation=PlanStandardIsolationEvidence(
                backend=PlanIsolationBackendEvidence(
                    backend_id="bubblewrap",
                    configuration_id="fixture-boundary",
                    kernel_boundary="shared_host_kernel",
                    remote_execution=False,
                    experimental=False,
                ),
                workspace_read_only=True,
                broker_socket_mounted=False,
                resource_owner="none",
                filesystem_cleanup="unverified",
            )
            if process_quiescent
            else None,
        )

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", command)
    result = PhysicalFleetPlanExecutorHarness(
        GitService(),
        process_isolation_backends=(_UnitProcessIsolation(),) if retry else (),
    ).execute(repository, plan, cancel_requested=None)
    assert launched == [("true", "work")]
    assert [step.status for step in result.steps] == ["FAILED", "SKIPPED", "SKIPPED"]
    failed = result.steps[0]
    assert failed.status == "FAILED"
    assert failed.returncode != 0
    assert failed.outputs == ()
    assert failed.artifacts == ()
    assert len(failed.attempts) == 1
    assert failed.attempts[0].retry is None
    if not retry:
        assert (repository / "result.txt").read_text(encoding="utf-8") == "unsafe output"
    assert "Execution cleanup is unverified" in result.stderr
    retained = FleetPlanResult.model_validate_json(
        FleetPlanResult(
            plan_id=plan.plan_id, steps=result.steps, execution_inputs=result.execution_inputs
        ).model_dump_json(exclude_computed_fields=True)
    )
    supervisor = retained.steps[0].attempts[0].supervisor
    assert supervisor is not None
    assert supervisor.final_process_group_quiescent is process_quiescent
    assert supervisor.forced_termination is True
    if process_quiescent:
        isolation = retained.steps[0].attempts[0].standard_isolation
        assert isolation is not None and isolation.filesystem_cleanup == "unverified"
    for skipped in retained.steps[1:]:
        assert skipped.status == "SKIPPED"
        assert "cleanup is unverified" in skipped.reason


@pytest.mark.parametrize("failure", ["timeout", "output_limit"])
def test_bounded_failure_preserves_actual_cleanup_uncertainty(failure: str) -> None:
    step = ResolvedCommandStep(step_id="bounded", argv=("true",))
    supervisor = CommandSupervisorReceipt(
        process_group_cleanup_requested=True,
        cleanup_reason="timeout" if failure == "timeout" else "output_limit",
        forced_termination=True,
        final_process_group_quiescent=False,
    )
    error = (
        CommandTimeoutExpired(step.argv, 1, supervisor=supervisor)
        if failure == "timeout"
        else CommandOutputLimitExceeded(
            argv=step.argv,
            stream="stdout",
            limit_bytes=1,
            stdout="x",
            stderr="",
            supervisor=supervisor,
        )
    )
    outcome = bounded_execution_failure_outcome(step, error)
    assert outcome.returncode != 0
    evidence = attempt_evidence(
        step=step,
        attempt=1,
        outcome=outcome,
        repository_state_before="before",
        repository_state_after="after",
        worktree_files_after=(),
    )
    assert evidence.supervisor is not None
    assert evidence.supervisor.cleanup_reason == failure
    assert evidence.supervisor.final_process_group_quiescent is False
    assert evidence.supervisor.forced_termination is True
