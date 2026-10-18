from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import ErrorKind
from oh_goon.core.models.background import BackgroundFailure
from oh_goon.core.models.runtime_safety import (
    ProvenRuntimeSafetyProof,
)
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
from oh_goon.engine.command import CommandResult
from oh_goon.services.system_health import SystemHealthService


def _safety(status: RuntimeSafetyStatus = RuntimeSafetyStatus.PROVEN_SAFE):
    if status is RuntimeSafetyStatus.PROVEN_SAFE:
        proof = ProvenRuntimeSafetyProof(status=status, durable_revision=0, journal_revision=0)
    else:
        raise AssertionError(f"Unsupported test safety state: {status}")
    return SimpleNamespace(refresh=lambda: proof)


def test_tool_and_system_health_reject_derived_or_contradictory_state() -> None:
    with pytest.raises(ValidationError):
        MissingOptionalToolHealth(
            name="mani",
            status=ToolStatus.OPTIONAL_MISSING,
            required=True,
        )
    proof = ProvenRuntimeSafetyProof(
        status=RuntimeSafetyStatus.PROVEN_SAFE,
        durable_revision=0,
        journal_revision=0,
    )
    health = SystemHealth(tools=(), runtime_safety=proof, background_failures=())
    assert health.status is SystemHealthStatus.READY
    with pytest.raises(ValidationError):
        SystemHealth(
            tools=(),
            runtime_safety=proof,
            background_failures=(),
            status=SystemHealthStatus.DEGRADED,
        )


def test_health_contains_required_tools() -> None:
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    names = {tool.name for tool in health.tools}
    assert {"git", "uv"}.issubset(names)
    assert "mani" not in names
    assert health.runtime_safety.effects_allowed


def test_missing_required_tool_requires_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*_args):
        raise OSError(2, "missing")
    monkeypatch.setattr("oh_goon.services.system_health.run_command", missing)
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    assert health.status is SystemHealthStatus.SETUP_REQUIRED
    assert all(tool.status is ToolStatus.MISSING for tool in health.tools)


def test_broken_tool_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "oh_goon.services.system_health.run_command",
        lambda *_args: CommandResult(returncode=1, stdout="", stderr="boom"),
    )
    health = SystemHealthService(_safety(), lambda: []).inspect()  # type: ignore[arg-type]
    assert health.status is SystemHealthStatus.SETUP_REQUIRED
    assert all(tool.status is ToolStatus.BROKEN for tool in health.tools)
    assert all(tool.detail == "boom" for tool in health.tools)


def test_health_observes_uv_version_without_redeciding_project_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "oh_goon.services.system_health.run_command",
        lambda name, *_args: CommandResult(
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
        lambda name, *_args: CommandResult(
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


