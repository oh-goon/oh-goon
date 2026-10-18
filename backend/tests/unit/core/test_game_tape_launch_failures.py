from __future__ import annotations

import hashlib

from oh_goon.core.execution_ledger import _step_terminal_message
from oh_goon.core.game_tape_step_projection import project_steps
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_evidence import FailedPlanStepExecution, PlanOutputEvidence
from oh_goon.core.models.fleet_plan_step_primitives import (
    PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GameTapeStatus


def _output(text: str) -> PlanOutputEvidence:
    encoded = text.encode()
    return PlanOutputEvidence(
        sha256=hashlib.sha256(encoded).hexdigest(),
        byte_count=len(encoded),
        line_count=len(text.splitlines()),
    )


def test_game_tape_preserves_missing_executable_fact() -> None:
    executable = "definitely-not-an-oh-goon-executable-9d61"
    now = utc_now()
    planned = ResolvedCommandStep(
        step_id="missing-tool-step",
        argv=(executable, "--version"),
        timeout_seconds=5,
    )
    execution = FailedPlanStepExecution(
        step_id="missing-tool-step",
        kind="command",
        started_at=now,
        finished_at=now,
        argv=(executable, "--version"),
        returncode=PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE,
        stdout_evidence=_output(""),
        stderr_evidence=_output(f"Executable not found: {executable}\n"),
        duration_ms=0,
        repository_state_before="before",
        repository_state_after="before",
    )
    projected = project_steps(
        "run-missing-tool",
        "repo-missing-tool",
        (planned,),
        (execution,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="OPERATION_FAILED",
    )

    assert projected[0].reason == f"Executable not found: {executable}."
    assert projected[0].failure is not None
    assert projected[0].failure.exit_code == PLAN_EXECUTABLE_NOT_FOUND_RETURN_CODE
    assert (
        _step_terminal_message(GameTapeStatus.FAILED, execution) == "Step executable was not found."
    )


def test_game_tape_preserves_not_runnable_executable_fact() -> None:
    from oh_goon.core.models.fleet_plan_step_primitives import (
        PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
    )

    executable = "./tool.sh"
    now = utc_now()
    planned = ResolvedCommandStep(
        step_id="permission-step",
        argv=(executable,),
        timeout_seconds=5,
    )
    execution = FailedPlanStepExecution(
        step_id="permission-step",
        kind="command",
        started_at=now,
        finished_at=now,
        argv=(executable,),
        returncode=PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE,
        stdout_evidence=_output(""),
        stderr_evidence=_output(f"Executable is not runnable: {executable}\n"),
        duration_ms=0,
        repository_state_before="before",
        repository_state_after="before",
    )
    projected = project_steps(
        "run-permission",
        "repo-permission",
        (planned,),
        (execution,),
        step_sources={},
        source_label="goon.yml",
        terminal=True,
        operation_reason_code="OPERATION_FAILED",
    )

    assert projected[0].reason == f"Executable is not runnable: {executable}."
    assert projected[0].failure is not None
    assert projected[0].failure.exit_code == PLAN_EXECUTABLE_NOT_RUNNABLE_RETURN_CODE
    assert (
        _step_terminal_message(GameTapeStatus.FAILED, execution)
        == "Step executable could not be run."
    )
