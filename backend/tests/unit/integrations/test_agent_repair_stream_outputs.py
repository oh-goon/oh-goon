"""Native stream publication never reads inherited repository diagnostics."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan import ResolvedCommandStep, StepFailurePolicy
from oh_goon.core.models.fleet_plan_step_primitives import PlanStreamOutput
from oh_goon.engine.command import CommandResult
from oh_goon.integrations._fleet_plan_step_outcome import from_command
from oh_goon.integrations._fleet_plan_step_retry import _successful_direct_publication


def test_stream_diagnostic_is_the_current_native_attempt(tmp_path):
    (tmp_path / "diagnostic.txt").write_text("obsolete previous failure")
    step = ResolvedCommandStep(
        step_id="test",
        argv=("verify",),
        on_failure=StepFailurePolicy.CONTINUE,
        outputs=(PlanStreamOutput(name="diagnostic", stream="stderr", max_bytes=100),),
    )
    result = _successful_direct_publication(
        step,
        from_command(
            CommandResult(argv=("verify",), returncode=13, stdout="", stderr="current failure\n")
        ),
        tmp_path,
    )
    assert result.outcome.returncode == 13
    assert result.outputs == {"diagnostic": "current failure"}
    assert result.output_evidence[0].sha256 == hashlib.sha256(b"current failure").hexdigest()
    assert (tmp_path / "diagnostic.txt").read_text() == "obsolete previous failure"


@pytest.mark.parametrize("text", ["too much output", "bad\x00diagnostic"])
def test_invalid_stream_cannot_erase_native_failure_or_publish_feedback(tmp_path, text):
    step = ResolvedCommandStep(
        step_id="test",
        argv=("verify",),
        on_failure=StepFailurePolicy.CONTINUE,
        outputs=(PlanStreamOutput(name="diagnostic", stream="stdout", max_bytes=10),),
    )
    result = _successful_direct_publication(
        step,
        from_command(CommandResult(argv=("verify",), returncode=19, stdout=text, stderr="")),
        tmp_path,
    )
    assert result.outcome.returncode == 19
    assert result.outputs == {}
    assert result.output_evidence == ()


def test_file_and_stream_sources_cannot_be_combined():
    with pytest.raises(ValidationError):
        ResolvedCommandStep(
            step_id="test",
            argv=("verify",),
            outputs=({"name": "diagnostic", "path": "old.txt", "stream": "stdout"},),
        )
