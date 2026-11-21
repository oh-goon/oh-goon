"""Native stream publication never reads inherited repository diagnostics."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.fleet_plan import ResolvedCommandStep, StepFailurePolicy
from oh_goon.core.models.fleet_plan_evidence import (
    PlanOutputEvidence,
    PlanRetainedStreamOutputEvidence,
)
from oh_goon.core.models.fleet_plan_step_primitives import PlanRegressionFailure, PlanStreamOutput
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    ResolvedEnvironmentBinding,
)
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitBranchState, WorktreeState
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitObservation, GitService
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, from_command
from oh_goon.integrations._fleet_plan_step_retry import _successful_direct_publication
from oh_goon.integrations._fleet_plan_step_sequence import FleetPlanStepSequence
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime


def test_stream_diagnostic_is_the_current_native_attempt(tmp_path: Path) -> None:
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
    assert "retained_stream" not in result.output_evidence[0].model_dump(mode="json")
    assert (tmp_path / "diagnostic.txt").read_text() == "obsolete previous failure"


@pytest.mark.parametrize("text", ["too much output", "bad\x00diagnostic"])
def test_invalid_stream_cannot_erase_native_failure_or_publish_feedback(
    tmp_path: Path, text: str
) -> None:
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


def test_file_and_stream_sources_cannot_be_combined() -> None:
    with pytest.raises(ValidationError):
        ResolvedCommandStep.model_validate(
            {
                "step_id": "test",
                "argv": ["verify"],
                "outputs": [{"name": "diagnostic", "path": "old.txt", "stream": "stdout"}],
            }
        )


def _source(payload: bytes) -> PlanOutputEvidence:
    return PlanOutputEvidence(
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        line_count=len(payload.splitlines()),
    )


def _retaining_step() -> ResolvedCommandStep:
    return ResolvedCommandStep(
        step_id="test",
        argv=("verify",),
        on_failure=StepFailurePolicy.CONTINUE,
        outputs=(
            PlanStreamOutput(name="diagnostic", stream="stdout", max_bytes=2048, retain=True),
        ),
    )


def test_retained_stdout_keeps_canonical_and_original_native_identities(tmp_path: Path) -> None:
    raw = b"  assertion failed\n"
    source = _source(raw)
    result = _successful_direct_publication(
        _retaining_step(),
        from_command(
            CommandResult(
                argv=("verify",),
                returncode=13,
                stdout=raw.decode(),
                stderr="",
                stdout_evidence=source,
                physical_execution_id="native-verifier-1",
            )
        ),
        tmp_path,
    )
    assert result.outcome.returncode == 13
    assert result.outcome.observed_returncode == 13
    assert result.outputs == {"diagnostic": "assertion failed"}
    outer = result.output_evidence[0]
    retained = outer.retained_stream
    assert retained is not None
    assert retained.text == "assertion failed" and not retained.redacted
    assert retained.sha256 == outer.sha256 == hashlib.sha256(b"assertion failed").hexdigest()
    assert retained.byte_count == outer.byte_count == len(b"assertion failed")
    assert retained.source == source and retained.source.sha256 != outer.sha256
    assert retained.physical_execution_id == "native-verifier-1"


def test_stop_policy_can_retain_native_failure_without_changing_its_result(tmp_path: Path) -> None:
    step = _retaining_step().model_copy(update={"on_failure": StepFailurePolicy.STOP})
    result = _successful_direct_publication(
        step,
        from_command(
            CommandResult(
                argv=step.argv,
                returncode=13,
                stdout="assertion failed\n",
                stderr="",
                stdout_evidence=_source(b"assertion failed\n"),
                physical_execution_id="native-verifier-1",
            )
        ),
        tmp_path,
    )
    assert step.on_failure is StepFailurePolicy.STOP
    assert result.outcome.returncode == result.outcome.observed_returncode == 13
    assert result.outputs == {"diagnostic": "assertion failed"}
    retained = result.output_evidence[0].retained_stream
    assert retained is not None and retained.text == "assertion failed"


def test_stop_policy_without_retention_keeps_failed_outputs_unpublished(tmp_path: Path) -> None:
    step = ResolvedCommandStep(
        step_id="test",
        argv=("verify",),
        outputs=(PlanStreamOutput(name="diagnostic", stream="stdout", max_bytes=2048),),
    )
    result = _successful_direct_publication(
        step,
        from_command(
            CommandResult(
                argv=step.argv,
                returncode=13,
                stdout="assertion failed\n",
                stderr="",
                stdout_evidence=_source(b"assertion failed\n"),
                physical_execution_id="native-verifier-1",
            )
        ),
        tmp_path,
    )
    assert step.on_failure is StepFailurePolicy.STOP
    assert result.outcome.returncode == result.outcome.observed_returncode == 13
    assert result.outputs == {} and result.output_evidence == ()


@pytest.mark.parametrize("retain", [False, True])
def test_native_command_preserves_complete_stdout_only_for_explicit_retention(
    tmp_path: Path, retain: bool
) -> None:
    raw = b" assertion failed \t\n\n"
    step = ResolvedCommandStep(
        step_id="test",
        argv=(
            sys.executable,
            "-S",
            "-c",
            "import os; os.write(1, b' assertion failed \\t\\n\\n'); os._exit(13)",
        ),
        outputs=(
            PlanStreamOutput(name="diagnostic", stream="stdout", max_bytes=2048, retain=retain),
        ),
    )
    sequence = FleetPlanStepSequence(
        tmp_path,
        StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None),
    )
    sequence.run_direct(step)
    failed = sequence.results[0]
    assert failed.status == "FAILED" and failed.returncode == 13
    assert failed.settlement.cleanup == "VERIFIED"
    assert sequence.stdout == (raw.decode() if retain else raw.decode().rstrip())
    if retain:
        assert failed.stdout_evidence == _source(raw)
        retained = failed.outputs[0].retained_stream
        assert retained is not None and retained.text == "assertion failed"
        assert retained.source == failed.stdout_evidence == failed.attempts[0].stdout_evidence
        assert retained.physical_execution_id == failed.attempts[0].physical_execution_id
        assert retained.physical_execution_id
    else:
        assert failed.stdout_evidence == _source(raw.rstrip())
        assert failed.outputs == ()


@pytest.mark.parametrize("native_returncode", [0, 13])
def test_failure_feedback_bound_does_not_change_passing_or_failing_native_result(
    tmp_path: Path, native_returncode: int
) -> None:
    step = ResolvedCommandStep(
        step_id="test",
        argv=(
            sys.executable,
            "-S",
            "-c",
            f"import os; os.write(1, b'x' * 2049); os._exit({native_returncode})",
        ),
        regression_failure=PlanRegressionFailure(
            exit_codes=(13,), feedback="diagnostic", reason="verifier assertion failed"
        ),
        outputs=(
            PlanStreamOutput(name="diagnostic", stream="stdout", max_bytes=2048, retain=True),
        ),
    )
    sequence = FleetPlanStepSequence(
        tmp_path,
        StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None),
    )
    sequence.run_direct(step)
    result = sequence.results[0]
    assert result.status != "SKIPPED"
    assert result.returncode == native_returncode
    assert result.status == ("COMPLETED" if native_returncode == 0 else "FAILED")
    assert result.attempts[0].observed_returncode == native_returncode
    assert result.outputs == ()


@pytest.mark.parametrize(
    ("text", "source", "physical_execution_id"),
    [
        ("failed", None, "native-verifier-1"),
        ("failed", _source(b"failed"), None),
        ("failed", _source(b"failed!"), "native-verifier-1"),
        ("failed", _source(b"passed"), "native-verifier-1"),
        ("tail", _source(b"discarded prefix\ntail"), "native-verifier-1"),
        ("\ufffd", _source(b"\xff"), "native-verifier-1"),
        ("\ud800", _source(b"invalid UTF-8"), "native-verifier-1"),
        ("bad\x00diagnostic", _source(b"bad\x00diagnostic"), "native-verifier-1"),
        ("\u00e9" * 1025, _source(("\u00e9" * 1025).encode()), "native-verifier-1"),
    ],
    ids=[
        "missing-native-source",
        "missing-physical-attempt",
        "wrong-byte-count",
        "wrong-native-digest",
        "truncated-tail",
        "invalid-native-utf8",
        "invalid-text-utf8",
        "nul",
        "utf8-byte-limit",
    ],
)
def test_unproven_retained_stream_preserves_failed_exit_without_feedback(
    tmp_path: Path,
    text: str,
    source: PlanOutputEvidence | None,
    physical_execution_id: str | None,
) -> None:
    result = _successful_direct_publication(
        _retaining_step(),
        from_command(
            CommandResult(
                argv=("verify",),
                returncode=19,
                stdout=text,
                stderr="",
                stdout_evidence=source,
                physical_execution_id=physical_execution_id,
            )
        ),
        tmp_path,
    )
    assert result.outcome.returncode == result.outcome.observed_returncode == 19
    assert result.outputs == {} and result.output_evidence == ()


@pytest.mark.parametrize(
    "declaration",
    [
        {"name": "diagnostic", "stream": "stderr", "max_bytes": 100, "retain": True},
        {"name": "diagnostic", "stream": "stdout", "max_bytes": 2049, "retain": True},
    ],
)
def test_retention_requires_explicit_bounded_stdout(declaration: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PlanStreamOutput.model_validate(declaration)


def test_stream_retention_default_keeps_existing_serialized_contract() -> None:
    ordinary = PlanStreamOutput(name="diagnostic", stream="stdout")
    assert ordinary.retain is False
    assert ordinary.model_dump(mode="json") == {
        "name": "diagnostic",
        "stream": "stdout",
        "max_bytes": 4096,
    }


@pytest.mark.parametrize(
    "identity_change",
    [
        {"text": "different"},
        {"sha256": "0" * 64},
        {"byte_count": 5},
        {"text": "\u00e9" * 1025, "byte_count": 2050},
        {"text": "failed\x00"},
        {"text": " failed "},
        {"physical_execution_id": ""},
    ],
)
def test_retained_text_validates_its_own_identity(identity_change: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "text": "failed",
        "sha256": hashlib.sha256(b"failed").hexdigest(),
        "byte_count": 6,
        "source": _source(b"failed\n"),
        "physical_execution_id": "native-verifier-1",
    }
    payload.update(identity_change)
    with pytest.raises(ValidationError):
        PlanRetainedStreamOutputEvidence.model_validate(payload)


@pytest.mark.parametrize("secret", ["protected-token", " protected-token "])
def test_durable_retained_stdout_uses_original_secret_redaction_before_strip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: str
) -> None:
    step = _retaining_step().model_copy(
        update={
            "on_failure": StepFailurePolicy.STOP,
            "environment": (
                ResolvedEnvironmentBinding(
                    name="TEST_TOKEN",
                    parts=(HostEnvironmentPart(name="TEST_TOKEN"),),
                ),
            ),
        }
    )
    raw = secret + "\n"
    source = _source(raw.encode())
    launched: list[str] = []

    def execute(*args: object, **kwargs: object) -> StepOutcome:
        launched.append(step.step_id)
        return from_command(
            CommandResult(
                argv=step.argv,
                returncode=13,
                stdout=raw,
                stderr="",
                stdout_evidence=source,
                physical_execution_id="native-verifier-1",
            )
        )

    def observe(*args: object, **kwargs: object) -> GitObservation:
        return GitObservation(
            fingerprint=RepositoryStateFingerprint(
                captured_at=utc_now(),
                branch_state=GitBranchState.UNAVAILABLE,
                worktree_state=WorktreeState.NOT_GIT,
            ),
            changed_files=(),
        )

    monkeypatch.setenv("TEST_TOKEN", secret)
    monkeypatch.setattr("oh_goon.integrations._fleet_plan_step_retry.execute_direct_step", execute)
    monkeypatch.setattr(GitService, "observe", observe)
    sequence = FleetPlanStepSequence(
        tmp_path,
        StepRuntime(git=GitService(), extensions=None, tools=None, cancel_requested=None),
    )
    sequence.run_direct(step)
    failed = sequence.results[0]
    assert failed.status == "FAILED" and failed.returncode == 13
    assert failed.settlement.cleanup == "VERIFIED"
    output = failed.outputs[0]
    retained = output.retained_stream
    assert retained is not None
    assert retained.text == "***" and retained.redacted
    assert retained.sha256 == hashlib.sha256(b"***").hexdigest()
    assert retained.byte_count == 3
    assert retained.source == failed.stdout_evidence == failed.attempts[0].stdout_evidence == source
    assert retained.physical_execution_id == failed.attempts[0].physical_execution_id
    assert retained.physical_execution_id == "native-verifier-1"
    assert output.sha256 == hashlib.sha256(raw.strip().encode()).hexdigest()
    assert output.byte_count == len(raw.strip().encode())
    assert "protected-token" not in failed.model_dump_json()
    assert sequence.stdout == "***\n"
    sequence.run_direct(ResolvedCommandStep(step_id="followup", argv=("followup",)))
    assert sequence.results[-1].status == "SKIPPED"
    assert launched == [step.step_id]
