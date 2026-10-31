import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.cli_result import CliTermination
from oh_goon.core.errors import ErrorKind
from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.runtime_safety import (
    ProvenRuntimeSafetyProof,
)
from oh_goon.core.models.runtime_supervision import WatchdogDecision, WatchdogDisposition
from oh_goon.core.models.tool_health import (
    MissingOptionalToolHealth,
    SystemHealth,
)
from oh_goon.core.vocabulary import (
    BackgroundFailureStage,
    RuntimeSafetyStatus,
    SystemHealthStatus,
    ToolStatus,
)
from oh_goon.engine.command import (
    CommandResult,
    CommandSupervisionTerminated,
    CommandSupervisorReceipt,
)
from oh_goon.services.system_health import SystemHealthService


def _safety(status: RuntimeSafetyStatus = RuntimeSafetyStatus.PROVEN_SAFE) -> SimpleNamespace:
    if status is RuntimeSafetyStatus.PROVEN_SAFE:
        proof = ProvenRuntimeSafetyProof(status=status, durable_revision=0, journal_revision=0)
    else:
        raise AssertionError(f"Unsupported test safety state: {status}")
    return SimpleNamespace(refresh=lambda: proof)


def test_tool_and_system_health_reject_derived_or_contradictory_state() -> None:
    with pytest.raises(ValidationError):
        MissingOptionalToolHealth.model_validate(
            {"name": "mani", "status": ToolStatus.OPTIONAL_MISSING, "required": True}
        )
    proof = ProvenRuntimeSafetyProof(
        status=RuntimeSafetyStatus.PROVEN_SAFE,
        durable_revision=0,
        journal_revision=0,
    )
    health = SystemHealth(tools=(), runtime_safety=proof, background_failures=())
    assert health.status is SystemHealthStatus.READY
    with pytest.raises(ValidationError):
        SystemHealth.model_validate(
            {
                "tools": (),
                "runtime_safety": proof,
                "background_failures": (),
                "status": SystemHealthStatus.DEGRADED,
            }
        )


def test_health_contains_required_tools() -> None:
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    names = {tool.name for tool in health.tools}
    assert {"git", "uv"}.issubset(names)
    assert "mani" not in names
    assert health.runtime_safety.effects_allowed


def test_missing_required_tool_requires_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args: object, **_kwargs: object) -> None:
        raise OSError(2, "missing")

    monkeypatch.setattr("oh_goon.services.system_health.run_command", missing)
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    assert health.status is SystemHealthStatus.SETUP_REQUIRED
    assert all(tool.status is ToolStatus.MISSING for tool in health.tools)


def test_broken_tool_is_reported_without_native_error_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "oh_goon.services.system_health.run_command",
        lambda *_args, **_kwargs: CommandResult(returncode=1, stdout="", stderr="boom"),
    )
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    assert health.status is SystemHealthStatus.SETUP_REQUIRED
    assert all(tool.status is ToolStatus.BROKEN for tool in health.tools)
    assert all(
        tool.detail == "Native version probe returned a nonzero exit status"
        for tool in health.tools
    )
    assert "boom" not in health.model_dump_json()


def test_health_observes_uv_version_without_redeciding_project_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "oh_goon.services.system_health.run_command",
        lambda name, *_args, **_kwargs: CommandResult(
            returncode=0,
            stdout="uv 0.1.0" if name == "uv" else "git version 2.50.0",
            stderr="",
        ),
    )
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    uv = next(tool for tool in health.tools if tool.name == "uv")
    assert uv.status is ToolStatus.READY
    assert uv.version == "uv 0.1.0"
    assert health.status is SystemHealthStatus.READY


def test_background_failure_degrades_health_without_blocking_runtime_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "oh_goon.services.system_health.run_command",
        lambda name, *_args, **_kwargs: CommandResult(
            returncode=0,
            stdout="uv 99.0.0" if name == "uv" else "git version 2.50.0",
            stderr="",
        ),
    )
    failure = BackgroundFailure(
        code="SCHEDULER_FAILURE",
        kind=ErrorKind.INTERNAL,
        stage=BackgroundFailureStage.SCHEDULE_SCHEDULER,
        message="scheduler failed",
    )
    health = SystemHealthService(_safety(), lambda: [failure]).inspect()  # type: ignore[arg-type]
    assert health.status is SystemHealthStatus.DEGRADED
    assert health.runtime_safety.effects_allowed
    assert health.background_failures == (failure,)


@pytest.mark.process
@pytest.mark.parametrize("failure", ["stalled", "noisy", "failed"])
def test_native_tool_version_probe_has_bounded_failure_without_private_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    executable = tmp_path / "tool"
    body = (
        "import time; time.sleep(6); print('tool version 1')"
        if failure == "stalled"
        else "import os; os.write(1, b'PRIVATE_VERSION_CANARY' * 8192)"
        if failure == "noisy"
        else "import os, sys; os.write(2, b'PRIVATE_VERSION_CANARY'); sys.exit(17)"
    )
    executable.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(SystemHealthService, "REQUIRED", (str(executable),))

    tools = SystemHealthService.inspect_tools()

    assert len(tools) == 1
    observed = tools[0]
    assert observed.status is ToolStatus.BROKEN
    assert observed.reason_code.value == "TOOL_BROKEN"
    assert observed.version is None
    assert observed.detail == (
        "Native version probe exceeded its deadline"
        if failure == "stalled"
        else "Native version probe exceeded its output limit"
        if failure == "noisy"
        else "Native version probe returned a nonzero exit status"
    )
    assert "PRIVATE_VERSION_CANARY" not in observed.model_dump_json()


@pytest.mark.parametrize("supervision", ["terminated", "unsettled_result"])
def test_unproven_version_probe_does_not_hide_other_native_tool_diagnostics(
    monkeypatch: pytest.MonkeyPatch, supervision: str
) -> None:
    receipt = CommandSupervisorReceipt(final_process_group_quiescent=False)
    failure = CommandSupervisionTerminated(
        "PRIVATE_SUPERVISOR_CANARY",
        decision=WatchdogDecision(
            disposition=WatchdogDisposition.STALLED,
            policy_revision="version-probe-fixture",
            elapsed_seconds=1,
            progress_idle_seconds=1,
            termination_required=True,
            reason="PRIVATE_SUPERVISOR_CANARY",
        ),
        supervisor=receipt,
    )

    def inspect(name: str, *_args: object, **_kwargs: object) -> CommandResult:
        if name == "unsettled":
            if supervision == "terminated":
                raise failure
            return CommandResult(
                returncode=0, stdout="unsettled version", stderr="", supervisor=receipt
            )
        return CommandResult(returncode=0, stdout="healthy version", stderr="")

    monkeypatch.setattr(SystemHealthService, "REQUIRED", ("unsettled", "healthy"))
    monkeypatch.setattr("oh_goon.services.system_health.run_command", inspect)

    observations = SystemHealthService.inspect_tools()

    assert [tool.status for tool in observations] == [ToolStatus.BROKEN, ToolStatus.READY]
    assert observations[0].detail is not None and "UNPROVEN" in observations[0].detail
    assert observations[1].version == "healthy version"
    assert "PRIVATE_SUPERVISOR_CANARY" not in "".join(
        tool.model_dump_json() for tool in observations
    )


@pytest.mark.parametrize(
    "termination", [KeyboardInterrupt, CliTermination], ids=["SIGINT", "SIGTERM"]
)
def test_native_version_probe_keeps_caller_interruption_authority(
    monkeypatch: pytest.MonkeyPatch, termination: type[KeyboardInterrupt]
) -> None:
    failure = termination()

    def interrupted(*_args: object, **_kwargs: object) -> CommandResult:
        raise failure

    monkeypatch.setattr("oh_goon.services.system_health.run_command", interrupted)
    with pytest.raises(type(failure)):
        SystemHealthService.inspect_tools()
