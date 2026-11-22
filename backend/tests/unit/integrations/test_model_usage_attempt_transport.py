"""Observed usage follows original attempts and never selects their result."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.models.agent_review import review_scope
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, StepRetryPolicy
from oh_goon.core.models.fleet_plan_evidence import (
    PlanStepAttemptEvidence,
    PlanStepCancellationEvidence,
)
from oh_goon.core.models.model_usage import (
    MAX_MODEL_USAGE_RECEIPT_BYTES,
    ModelUsageObservation,
    ModelUsageReport,
)
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandOutputLimitExceeded,
    CommandResult,
    CommandSupervisorReceipt,
    CommandTimeoutExpired,
)
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations import _fleet_plan_step_retry as retries
from oh_goon.integrations._fleet_plan_step_evidence import cancellation_evidence
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome
from oh_goon.integrations.agent_review_execution import execute_provider_agent
from oh_goon.integrations.fleet_plan_output_limit import bounded_execution_failure_outcome
from tests.factories.agent_review import agent_review_case


def _usage(identity: str = "original-request", *, tokens: int = 0) -> ModelUsageReport:
    return ModelUsageReport(
        observations=(
            ModelUsageObservation(
                observation_id=identity,
                source="openai-chat-completions",
                scope="request",
                scope_identity=identity,
                implementation_identity="oh-goon-provider-client/v1",
                requested_model="requested",
                reported_model="reported",
                input_tokens=tokens,
                output_tokens=0,
                completeness="reported",
            ),
        )
    )


@pytest.fixture
def provider_attempt(tmp_path):
    resolver, path, _repository, options = agent_review_case(tmp_path)
    plan, handoffs, _source = resolver.resolve_frozen_agents(path, **options)
    step = plan.steps[0]
    scratch = tmp_path / "private-provider"
    scratch.mkdir(mode=0o700)
    process = SimpleNamespace(
        paths=SimpleNamespace(tmp=scratch), host_environment={}, environment={}
    )
    runtime = SimpleNamespace(cancel_requested=None, runtime_supervision=None)
    return step, handoffs[step.step_id], process, runtime


def _receipt(request: Path, environment: dict, usage: ModelUsageReport) -> dict:
    return {
        "invocation_id": environment["OH_GOON_AGENT_STEP_USAGE_ID"],
        "usage": usage.model_dump(mode="json"),
    }


def _execute(case):
    step, handoff, process, runtime = case
    return execute_provider_agent(
        step,
        handoff,
        process,
        runtime,
        timeout=5,
        repository_evidence="Exact producer-qualified repository orientation",
    )


def _clean_review(case) -> str:
    step, handoff, _process, _runtime = case
    return json.dumps(
        {
            "schema": "oh-goon.agent-review/v1",
            "reviewer": step.step_id,
            "candidate_identity": handoff.source.repository.content_identity,
            "scope_identity": review_scope(handoff),
            "skill_identity": handoff.source.skill.content_identity,
            "verdict": "CLEAN",
            "summary": "Completed the selected review",
            "reviewed_paths": [
                "repo/" + member.path for member in handoff.source.repository.members
            ],
            "findings": [],
        }
    )


@pytest.mark.parametrize("returncode", [2, 3, 17])
def test_usage_receipt_preserves_original_failure_and_private_scratch(provider_attempt, returncode):
    usage = _usage()
    process = provider_attempt[2]

    def runner(*args, **options):
        request = Path(args[-1])
        request.with_suffix(".usage.json").write_text(
            json.dumps(_receipt(request, options["env"], usage))
        )
        return CommandResult(returncode, "original output", "original failure", argv=args)

    process.command_runner = runner
    outcome = _execute(provider_attempt)
    assert outcome.returncode == returncode
    assert outcome.stdout == "original output"
    assert outcome.stderr == "original failure"
    assert outcome.model_usage == usage
    assert outcome.model_usage.observations[0].input_tokens == 0
    assert outcome.model_usage.observations[0].total_tokens is None
    assert outcome.failure_kind == ("model_output_truncated" if returncode == 3 else None)
    assert not list(process.paths.tmp.iterdir())


def test_rejected_typed_output_keeps_original_process_and_observed_usage(provider_attempt):
    usage = _usage()
    process = provider_attempt[2]
    supervisor = CommandSupervisorReceipt()

    def runner(*args, **options):
        request = Path(args[-1])
        request.with_suffix(".usage.json").write_text(
            json.dumps(_receipt(request, options["env"], usage))
        )
        return CommandResult(
            0,
            '{"verdict":"CLEAN"}',
            "",
            argv=args,
            physical_execution_id="original-physical",
            supervisor=supervisor,
        )

    process.command_runner = runner
    outcome = _execute(provider_attempt)
    assert outcome.returncode != 0
    assert outcome.observed_returncode == 0
    assert outcome.physical_execution_id == "original-physical"
    assert outcome.supervisor is supervisor
    assert outcome.model_usage == usage
    assert "record schema" in outcome.stderr
    assert not list(process.paths.tmp.iterdir())


@pytest.mark.parametrize(
    "condition",
    [
        "absent",
        "wrong-invocation",
        "invalid-json",
        "private-extra",
        "negative-count",
        "oversize",
        "duplicate-key",
        "symlink",
        "hardlink",
        "fifo",
    ],
)
@pytest.mark.parametrize("returncode", [0, 17])
def test_unavailable_or_malformed_usage_cannot_change_execution_result(
    provider_attempt, tmp_path, condition, returncode
):
    process = provider_attempt[2]
    victim = tmp_path / "independent-file"
    victim.write_text("Independent bytes must survive")

    def runner(*args, **options):
        request = Path(args[-1])
        target = request.with_suffix(".usage.json")
        wire = _receipt(request, options["env"], _usage())
        if condition == "wrong-invocation":
            wire["invocation_id"] = "0" * 32
        elif condition == "private-extra":
            wire["usage"]["observations"][0]["transcript"] = "private prompt"
        elif condition == "negative-count":
            wire["usage"]["observations"][0]["input_tokens"] = -1
        if condition == "symlink":
            target.symlink_to(victim)
        elif condition == "hardlink":
            os.link(victim, target)
        elif condition == "fifo":
            os.mkfifo(target)
        elif condition == "oversize":
            target.write_bytes(b"x" * (MAX_MODEL_USAGE_RECEIPT_BYTES + 1))
        elif condition == "invalid-json":
            target.write_text("{interrupted telemetry")
        elif condition == "duplicate-key":
            target.write_text(json.dumps(wire)[:-1] + ',"invocation_id":"foreign"}')
        elif condition != "absent":
            target.write_text(json.dumps(wire))
        return CommandResult(
            returncode,
            _clean_review(provider_attempt) if returncode == 0 else "physical stdout",
            "physical diagnostic",
            argv=args,
            supervisor=CommandSupervisorReceipt(),
            physical_execution_id="original-physical",
        )

    process.command_runner = runner
    outcome = _execute(provider_attempt)
    assert outcome.returncode == returncode
    assert outcome.stderr == "physical diagnostic"
    if returncode == 0:
        assert json.loads(outcome.stdout)["verdict"] == "CLEAN"
    else:
        assert outcome.stdout == "physical stdout"
    assert outcome.model_usage is None
    assert victim.read_text() == "Independent bytes must survive"
    assert not list(process.paths.tmp.iterdir())


@pytest.mark.parametrize("interruption", ["cancel", "timeout", "output-limit"])
def test_interruption_keeps_observed_usage_under_original_error_owner(
    provider_attempt, interruption
):
    usage = _usage(tokens=12)
    process = provider_attempt[2]
    reason = "output_limit" if interruption == "output-limit" else interruption
    supervisor = CommandSupervisorReceipt(
        cleanup_reason=reason, process_group_cleanup_requested=True
    )
    if interruption == "cancel":
        original = CommandCancelledError(
            "cancelled original process",
            supervisor=supervisor,
            physical_execution_id="original-physical",
        )
    elif interruption == "timeout":
        original = CommandTimeoutExpired(
            ("provider",),
            5,
            supervisor=supervisor,
            physical_execution_id="original-physical",
        )
    else:
        original = CommandOutputLimitExceeded(
            stream="stdout",
            limit_bytes=16,
            stdout="original bounded output",
            stderr="",
            supervisor=supervisor,
        )

    def runner(*args, **options):
        request = Path(args[-1])
        request.with_suffix(".usage.json").write_text(
            json.dumps(_receipt(request, options["env"], usage))
        )
        raise original

    process.command_runner = runner
    with pytest.raises(type(original)) as captured:
        _execute(provider_attempt)
    assert captured.value is original
    assert captured.value.model_usage == usage
    assert not list(process.paths.tmp.iterdir())
    step = provider_attempt[0]
    if interruption == "cancel":
        error = OperationCancelledError("Original cancellation")
        error.__cause__ = original
        evidence = cancellation_evidence(step, error)
        assert evidence.settlement.outcome == "CANCELLED"
        assert evidence.physical_execution_id == "original-physical"
        assert evidence.model_usage == usage
        assert (
            PlanStepCancellationEvidence.model_validate_json(evidence.model_dump_json()) == evidence
        )
    else:
        outcome = bounded_execution_failure_outcome(step, original)
        assert outcome.returncode != 0
        assert outcome.model_usage == usage


def test_retry_owner_keeps_usage_per_physical_attempt_and_old_attempts_unknown(
    tmp_path, monkeypatch
):
    step = ResolvedCommandStep(
        step_id="provider",
        argv=("provider",),
        timeout_seconds=5,
        retry=StepRetryPolicy(
            max_attempts=2,
            known_transient_exit_codes=(17,),
            reason="The selected transient failure is explicitly replayable",
            delays_seconds=(0,),
        ),
    )
    reports = (_usage("request-1", tokens=8), _usage("request-2", tokens=0))
    seen = []

    def execute(*_args, attempt_number, **_options):
        seen.append(attempt_number)
        return StepOutcome(
            argv=("provider",),
            returncode=17 if attempt_number == 1 else 0,
            physical_execution_id=f"physical-{attempt_number}",
            supervisor=CommandSupervisorReceipt(),
            model_usage=reports[attempt_number - 1],
        )

    monkeypatch.setattr(retries, "execute_direct_step", execute)
    git = GitService()
    runtime = SimpleNamespace(git=git, cancel_requested=None, lifecycle_observer=None)
    result = retries.execute_direct_with_retries(
        tmp_path, step, step, runtime, git.observe(tmp_path), step_order=1
    )
    assert seen == [1, 2]
    assert result.outcome.returncode == 0
    assert tuple(item.physical_execution_id for item in result.attempts) == (
        "physical-1",
        "physical-2",
    )
    assert tuple(item.model_usage for item in result.attempts) == reports
    for attempt in result.attempts:
        wire = attempt.model_dump(mode="json", round_trip=True)
        assert (
            PlanStepAttemptEvidence.model_validate_json(attempt.model_dump_json(round_trip=True))
            == attempt
        )
        wire.pop("model_usage")
        old = PlanStepAttemptEvidence.model_validate(wire)
        assert old.model_usage is None
        assert "model_usage" not in old.model_dump(mode="json")


def test_cancelled_outcome_usage_is_passive_even_when_settlement_is_incomplete():
    step = ResolvedCommandStep(step_id="model", argv=("provider",), timeout_seconds=5)
    error = OperationCancelledError("Unacknowledged original cancellation")
    error.outcome = StepOutcome(argv=("provider",), returncode=17, model_usage=_usage())
    evidence = cancellation_evidence(step, error)
    assert evidence.settlement.outcome == "INCOMPLETE"
    assert evidence.physical_execution_id is None
    assert evidence.model_usage == _usage()
    old = evidence.model_dump(mode="json")
    old.pop("model_usage")
    assert PlanStepCancellationEvidence.model_validate(old).model_usage is None
