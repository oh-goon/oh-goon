from __future__ import annotations

import errno
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan import (
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
    PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
    ResolvedCommandStep,
)
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_step_outcome import settle_step_execution
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime, execute_direct_step


def test_direct_command_missing_executable_is_normal_step_failure(tmp_path: Path) -> None:
    executable = "definitely-not-an-oh-goon-executable-9d61"
    step = ResolvedCommandStep(
        step_id="missing-tool-step", argv=(executable, "--version"), timeout_seconds=5
    )
    outcome = execute_direct_step(
        tmp_path,
        step,
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
        ),
        step_order=1,
    )

    assert outcome.returncode == PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE
    assert outcome.argv == (executable, "--version")
    assert outcome.stderr == f"Executable not found: {executable}"
    assert outcome.completion == "not_started"
    assert outcome.supervisor is None and outcome.observed_returncode is None
    settled = settle_step_execution(step, outcome)
    assert settled.outcome == "FAILED" and settled.reason == "FAILED_TO_START"
    assert settled.cleanup == "VERIFIED"


def test_missing_executable_return_code_cannot_be_declared_success() -> None:
    with pytest.raises(ValidationError, match="reserved failure codes: 127"):
        ResolvedCommandStep(
            step_id="bad-success-code",
            argv=("python", "--version"),
            success_codes=(0, PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE),
        )


def test_direct_command_permission_denied_is_normal_step_failure(tmp_path: Path) -> None:
    executable = tmp_path / "tool.sh"
    executable.write_text("#!/bin/sh\necho should-not-run\n", encoding="utf-8")
    executable.chmod(0o644)
    outcome = execute_direct_step(
        tmp_path,
        ResolvedCommandStep(
            step_id="permission-step",
            argv=("./tool.sh",),
            timeout_seconds=5,
        ),
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
        ),
        step_order=1,
    )

    assert outcome.returncode == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE
    assert outcome.argv == ("./tool.sh",)
    assert outcome.stderr == "Executable is not runnable: ./tool.sh"
    assert outcome.completion == "not_started" and outcome.supervisor is None


def test_not_runnable_return_code_cannot_be_declared_success() -> None:
    with pytest.raises(ValidationError, match="reserved failure codes: 126"):
        ResolvedCommandStep(
            step_id="bad-not-runnable-code",
            argv=("python", "--version"),
            success_codes=(0, PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE),
        )


def test_direct_command_invalid_executable_format_is_normal_step_failure(tmp_path: Path) -> None:
    executable = tmp_path / "tool"
    executable.write_text("echo no-shebang\n", encoding="utf-8")
    executable.chmod(0o755)
    outcome = execute_direct_step(
        tmp_path,
        ResolvedCommandStep(
            step_id="invalid-format-step",
            argv=("./tool",),
            timeout_seconds=5,
        ),
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
        ),
        step_order=1,
    )

    assert outcome.returncode == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE
    assert outcome.argv == ("./tool",)
    assert outcome.stderr == "Executable is not runnable: ./tool"
    assert outcome.completion == "not_started" and outcome.supervisor is None


def test_existing_script_with_missing_interpreter_is_not_runnable(tmp_path: Path) -> None:
    executable = tmp_path / "tool.sh"
    executable.write_text("#!/definitely/missing/interpreter\necho hi\n", encoding="utf-8")
    executable.chmod(0o755)
    outcome = execute_direct_step(
        tmp_path,
        ResolvedCommandStep(
            step_id="missing-interpreter-step",
            argv=("./tool.sh",),
            timeout_seconds=5,
        ),
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
        ),
        step_order=1,
    )

    assert executable.exists()
    assert outcome.returncode == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE
    assert outcome.argv == ("./tool.sh",)
    assert outcome.stderr == "Executable is not runnable: ./tool.sh"
    assert outcome.completion == "not_started" and outcome.observed_returncode is None


def test_other_launch_error_path_cannot_inherit_selected_command_refusal(tmp_path, monkeypatch):
    other = str(tmp_path / "different-authority")

    def fail(*args, **kwargs):
        raise FileNotFoundError(errno.ENOENT, "Unrelated missing path", other)

    monkeypatch.setattr("oh_goon.integrations.fleet_plan_step_execution.run_command", fail)
    with pytest.raises(FileNotFoundError) as error:
        execute_direct_step(
            tmp_path,
            ResolvedCommandStep(step_id="work", argv=("selected-command",)),
            StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None),
            step_order=1,
        )
    assert error.value.filename == other


def test_direct_step_transports_only_resolved_runtime_supervision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timezone

    from oh_goon.core.models.runtime_supervision import RuntimeSupervisionPolicy
    from oh_goon.engine.command import CommandResult
    from oh_goon.engine.command_supervision import CommandRuntimeSupervision

    observed: dict[str, object] = {}

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        observed.update(kwargs)
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="", duration_ms=1)

    now = datetime.now(timezone.utc)
    supervision = CommandRuntimeSupervision(
        policy=RuntimeSupervisionPolicy(
            max_runtime_seconds=30,
            heartbeat_interval_seconds=1,
            heartbeat_timeout_seconds=5,
            termination_grace_seconds=1,
            quiescence_timeout_seconds=2,
            yield_deadline_seconds=5,
            policy_revision="step-runtime-v1",
        ),
        generation=3,
        heartbeat=lambda _execution_id, _generation: now,
        lease_valid=lambda _execution_id, _generation: True,
        semantic_progress=lambda _execution_id, _generation: None,
    )
    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )

    outcome = execute_direct_step(
        tmp_path,
        ResolvedCommandStep(step_id="supervised", argv=("echo", "ok"), timeout_seconds=10),
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
            runtime_supervision=supervision,
        ),
        step_order=1,
    )

    assert outcome.returncode == 0
    assert observed["timeout"] is None
    assert observed["runtime_supervision"] is supervision
