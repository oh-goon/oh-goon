from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.models.fleet_plan import (
    PlanPublishedOutput,
    ResolvedCommandStep,
    ResolvedFleetPlan,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_isolation import (
    CicdResourcePolicy,
    IsolationBackendCapabilities,
    IsolationKernelBoundary,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_resource_evidence import write_remote_resource_evidence
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
