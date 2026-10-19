import pytest

from oh_goon.core.models.output_acceptance import (
    ExecutionGeneration,
    OutputAcceptance,
    SideEffectAttempt,
    SideEffectDisposition,
)
from oh_goon.core.product_identity import fleet_step_attempt_id, repository_attempt_operation_id
from oh_goon.runtime.output_acceptance import OutputAcceptanceAuthority


def _execution(generation: int = 2) -> ExecutionGeneration:
    operation_id = repository_attempt_operation_id("run-1", "repo-1")
    return ExecutionGeneration(
        execution_id="exec-1",
        generation=generation,
        lineage_id="lineage-1",
        work_id="work-7",
        fleet_run_id="run-1",
        repository_id="repo-1",
        operation_id=operation_id,
        step_id="build",
        attempt_number=1,
        step_attempt_id=fleet_step_attempt_id(operation_id, "build", 1),
        physical_execution_id="physical-1",
    )


def _output(execution: ExecutionGeneration, *, digest: str = "a" * 64) -> OutputAcceptance:
    return OutputAcceptance(
        execution=execution,
        output_name="result",
        sha256=digest,
        byte_count=7,
        producer_execution_identity=execution.identity,
    )


def _effect(execution: ExecutionGeneration) -> SideEffectAttempt:
    return SideEffectAttempt(
        execution=execution, effect_kind="publish", effect_identity="registry:item"
    )


def test_output_acceptance_is_generation_bound_and_replay_idempotent() -> None:
    execution = _execution()
    authority = OutputAcceptanceAuthority(execution)
    output = _output(execution)
    first = authority.accept_output(output)
    assert authority.accept_output(output).acceptance_id == first.acceptance_id
    with pytest.raises(ValueError, match="stale or foreign"):
        authority.accept_output(_output(_execution(1)))


def test_same_output_name_cannot_be_rewritten_after_acceptance() -> None:
    execution = _execution()
    authority = OutputAcceptanceAuthority(execution)
    authority.accept_output(_output(execution))
    with pytest.raises(ValueError, match="different immutable content"):
        authority.accept_output(_output(execution, digest="b" * 64))


def test_attempted_external_effect_is_not_accepted_output() -> None:
    execution = _execution()
    authority = OutputAcceptanceAuthority(execution)
    receipt = authority.record_effect(
        _effect(execution), reason="physical effect observed before acceptance"
    )
    assert receipt.disposition is SideEffectDisposition.ATTEMPTED
    assert receipt.accepted_output_id is None


def test_side_effect_acceptance_requires_previously_accepted_exact_output() -> None:
    execution = _execution()
    authority = OutputAcceptanceAuthority(execution)
    output = _output(execution)
    with pytest.raises(ValueError, match="before durable output acceptance"):
        authority.record_effect(_effect(execution), accepted_output=output, reason="publish")
    accepted = authority.accept_output(output)
    receipt = authority.record_effect(
        _effect(execution), accepted_output=accepted, reason="publish"
    )
    assert receipt.disposition is SideEffectDisposition.ACCEPTED
    assert receipt.accepted_output_id == accepted.acceptance_id


def test_stale_generation_cannot_publish_external_effect() -> None:
    authority = OutputAcceptanceAuthority(_execution(2))
    with pytest.raises(ValueError, match="stale or foreign"):
        authority.record_effect(_effect(_execution(1)), reason="redelivered old message")


def test_conflicting_effect_replay_fails_closed() -> None:
    execution = _execution()
    authority = OutputAcceptanceAuthority(execution)
    attempt = _effect(execution)
    authority.reject_effect(attempt, reason="terminal authority already won")
    with pytest.raises(ValueError, match="conflicts"):
        authority.record_effect(attempt, reason="late observation")


def test_output_identity_binds_run_repository_step_attempt_and_physical_execution() -> None:
    execution = _execution()
    output = _output(execution)
    assert output.execution.fleet_run_id == "run-1"
    assert output.execution.repository_id == "repo-1"
    assert output.execution.operation_id == repository_attempt_operation_id("run-1", "repo-1")
    assert output.execution.step_id == "build"
    assert output.execution.step_attempt_id == fleet_step_attempt_id(
        output.execution.operation_id, "build", 1
    )
    assert output.execution.physical_execution_id == "physical-1"


def test_output_rejects_claimed_producer_that_is_not_exact_execution_generation() -> None:
    execution = _execution()
    with pytest.raises(ValueError, match="exact execution generation"):
        OutputAcceptance(
            execution=execution,
            output_name="result",
            sha256="a" * 64,
            byte_count=7,
            producer_execution_identity="foreign-producer",
        )


def test_output_execution_rejects_cross_repository_operation_identity() -> None:
    operation_id = repository_attempt_operation_id("run-1", "other-repo")
    with pytest.raises(ValueError, match="canonical run/repository identity"):
        ExecutionGeneration(
            execution_id="exec-1",
            generation=2,
            lineage_id="lineage-1",
            work_id="work-7",
            fleet_run_id="run-1",
            repository_id="repo-1",
            operation_id=operation_id,
            step_id="build",
            attempt_number=1,
            step_attempt_id=fleet_step_attempt_id(operation_id, "build", 1),
            physical_execution_id="physical-1",
        )


def test_output_execution_rejects_cross_attempt_step_identity() -> None:
    operation_id = repository_attempt_operation_id("run-1", "repo-1")
    with pytest.raises(ValueError, match="operation/Step/attempt identity"):
        ExecutionGeneration(
            execution_id="exec-1",
            generation=2,
            lineage_id="lineage-1",
            work_id="work-7",
            fleet_run_id="run-1",
            repository_id="repo-1",
            operation_id=operation_id,
            step_id="build",
            attempt_number=1,
            step_attempt_id=fleet_step_attempt_id(operation_id, "build", 2),
            physical_execution_id="physical-1",
        )
