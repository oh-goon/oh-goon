from datetime import UTC, datetime, timedelta

import pytest

from oh_goon.core.models.runtime_supervision import (
    RuntimeObservation,
    RuntimeSupervisionPolicy,
    SemanticProgress,
    WatchdogDisposition,
)
from oh_goon.runtime.watchdog import accept_semantic_progress, evaluate_watchdog

T0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
POLICY = RuntimeSupervisionPolicy(
    slow_after_seconds=10,
    max_runtime_seconds=60,
    heartbeat_interval_seconds=2,
    heartbeat_timeout_seconds=8,
    progress_start_grace_seconds=5,
    no_progress_timeout_seconds=20,
    termination_grace_seconds=5,
    quiescence_timeout_seconds=10,
    yield_deadline_seconds=15,
    policy_revision="runtime-v1",
)


def obs(seconds: int, **kwargs):
    return RuntimeObservation(
        execution_id="exec-1",
        generation=2,
        started_at=T0,
        observed_at=T0 + timedelta(seconds=seconds),
        lease_valid=kwargs.pop("lease_valid", True),
        process_alive=kwargs.pop("process_alive", True),
        last_heartbeat_at=kwargs.pop(
            "last_heartbeat_at", T0 + timedelta(seconds=max(0, seconds - 1))
        ),
        **kwargs,
    )


def progress(sequence: int, seconds: int, **kwargs):
    return SemanticProgress(
        execution_id=kwargs.pop("execution_id", "exec-1"),
        generation=kwargs.pop("generation", 2),
        sequence=sequence,
        observed_at=T0 + timedelta(seconds=seconds),
        **kwargs,
    )


def test_slow_is_evidence_only_when_progress_is_healthy():
    decision = evaluate_watchdog(POLICY, obs(12, last_progress=progress(1, 8)))
    assert decision.disposition is WatchdogDisposition.SLOW
    assert decision.termination_required is False


def test_heartbeat_does_not_count_as_semantic_progress():
    decision = evaluate_watchdog(POLICY, obs(25))
    assert decision.disposition is WatchdogDisposition.STALLED


def test_semantic_progress_keeps_stall_clock_independent_of_runtime_clock():
    decision = evaluate_watchdog(POLICY, obs(25, last_progress=progress(3, 18)))
    assert decision.disposition is WatchdogDisposition.SLOW
    assert decision.progress_idle_seconds == 7


def test_hard_runtime_has_precedence_over_stall():
    decision = evaluate_watchdog(POLICY, obs(60, last_progress=progress(9, 59)))
    assert decision.disposition is WatchdogDisposition.HARD_TIMEOUT


def test_completion_accepted_before_watchdog_observation_wins():
    decision = evaluate_watchdog(
        POLICY,
        obs(61, completed_at=T0 + timedelta(seconds=59), lease_valid=False, process_alive=False),
    )
    assert decision.disposition is WatchdogDisposition.CONTINUE


def test_lease_loss_is_hard_authority_and_fences_output():
    decision = evaluate_watchdog(POLICY, obs(7, lease_valid=False, last_progress=progress(2, 6)))
    assert decision.disposition is WatchdogDisposition.LEASE_LOST
    assert decision.output_fenced is True


def test_process_loss_is_distinct_from_semantic_stall():
    decision = evaluate_watchdog(POLICY, obs(7, process_alive=False))
    assert decision.disposition is WatchdogDisposition.PROCESS_LOST


def test_heartbeat_loss_is_distinct_from_semantic_stall():
    decision = evaluate_watchdog(
        POLICY, obs(12, last_heartbeat_at=T0 + timedelta(seconds=2), last_progress=progress(2, 11))
    )
    assert decision.disposition is WatchdogDisposition.HEARTBEAT_LOST


def test_missing_heartbeat_becomes_lost_only_after_timeout():
    assert (
        evaluate_watchdog(POLICY, obs(7, last_heartbeat_at=None)).disposition
        is WatchdogDisposition.CONTINUE
    )
    assert (
        evaluate_watchdog(POLICY, obs(8, last_heartbeat_at=None)).disposition
        is WatchdogDisposition.HEARTBEAT_LOST
    )


def test_startup_grace_prevents_false_stall_during_model_load():
    policy = POLICY.model_copy(
        update={"progress_start_grace_seconds": 30, "no_progress_timeout_seconds": 10}
    )
    assert evaluate_watchdog(policy, obs(20)).disposition is WatchdogDisposition.SLOW


def test_no_progress_protocol_disabled_does_not_invent_progress_from_other_signals():
    policy = POLICY.model_copy(update={"no_progress_timeout_seconds": None})
    decision = evaluate_watchdog(policy, obs(40))
    assert decision.disposition is WatchdogDisposition.SLOW
    assert decision.termination_required is False


def test_progress_rejects_obsolete_generation():
    with pytest.raises(ValueError, match="stale semantic progress generation"):
        accept_semantic_progress(
            None, progress(1, 1, generation=1), execution_id="exec-1", generation=2
        )


def test_progress_duplicate_is_idempotent_but_conflicting_duplicate_fails():
    current = progress(1, 1, marker="compile")
    assert (
        accept_semantic_progress(current, current, execution_id="exec-1", generation=2) is current
    )
    with pytest.raises(ValueError, match="changed payload"):
        accept_semantic_progress(
            current, progress(1, 1, marker="test"), execution_id="exec-1", generation=2
        )


def test_progress_cannot_move_sequence_backwards():
    with pytest.raises(ValueError, match="cannot move backwards"):
        accept_semantic_progress(
            progress(2, 2), progress(1, 3), execution_id="exec-1", generation=2
        )


def test_policy_validates_all_runtime_boundaries():
    with pytest.raises(ValueError, match="slow_after_seconds"):
        POLICY.model_copy(update={"slow_after_seconds": 60}).model_validate(
            POLICY.model_copy(update={"slow_after_seconds": 60}).model_dump()
        )
    with pytest.raises(ValueError, match="heartbeat_timeout_seconds"):
        RuntimeSupervisionPolicy(
            max_runtime_seconds=60,
            heartbeat_interval_seconds=10,
            heartbeat_timeout_seconds=5,
            termination_grace_seconds=5,
            quiescence_timeout_seconds=10,
            yield_deadline_seconds=15,
            policy_revision="x",
        )


def test_cooperative_yield_is_distinct_and_non_terminal_before_deadline():
    decision = evaluate_watchdog(
        POLICY,
        obs(20, last_progress=progress(4, 19), yield_requested_at=T0 + timedelta(seconds=12)),
    )
    assert decision.disposition is WatchdogDisposition.YIELD_REQUESTED
    assert decision.termination_required is False
    assert decision.output_fenced is False
    assert decision.reason == "cooperative_yield_requested"


def test_cooperative_yield_deadline_terminates_and_fences_output():
    decision = evaluate_watchdog(
        POLICY,
        obs(30, last_progress=progress(5, 29), yield_requested_at=T0 + timedelta(seconds=15)),
    )
    assert decision.disposition is WatchdogDisposition.YIELD_DEADLINE_EXCEEDED
    assert decision.termination_required is True
    assert decision.output_fenced is True
    assert decision.reason == "cooperative_yield_deadline_exceeded"


def test_completion_wins_over_pending_cooperative_yield():
    decision = evaluate_watchdog(
        POLICY,
        obs(
            30,
            completed_at=T0 + timedelta(seconds=29),
            yield_requested_at=T0 + timedelta(seconds=10),
        ),
    )
    assert decision.disposition is WatchdogDisposition.CONTINUE
