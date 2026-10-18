from __future__ import annotations

from oh_goon.core.models.runtime_supervision import (
    RuntimeObservation,
    RuntimeSupervisionPolicy,
    SemanticProgress,
    WatchdogDecision,
    WatchdogDisposition,
)


def accept_semantic_progress(
    current: SemanticProgress | None,
    candidate: SemanticProgress,
    *,
    execution_id: str,
    generation: int,
) -> SemanticProgress:
    """Accept monotonic progress only for the active execution generation."""
    if candidate.execution_id != execution_id or candidate.generation != generation:
        raise ValueError("stale semantic progress generation")
    if current is None:
        return candidate
    if current.execution_id != execution_id or current.generation != generation:
        raise ValueError("current semantic progress generation is inconsistent")
    if candidate.sequence < current.sequence:
        raise ValueError("semantic progress sequence cannot move backwards")
    if candidate.sequence == current.sequence:
        if candidate != current:
            raise ValueError("duplicate semantic progress sequence changed payload")
        return current
    if candidate.observed_at < current.observed_at:
        raise ValueError("semantic progress timestamp cannot move backwards")
    return candidate


def evaluate_watchdog(
    policy: RuntimeSupervisionPolicy, observation: RuntimeObservation
) -> WatchdogDecision:
    """Evaluate canonical runtime supervision without performing termination.

    Precedence is accepted completion, then hard authority/safety, then watchdog
    health, then non-terminal slow evidence. Heartbeat/process state never resets
    semantic progress and semantic progress never renews a lease.
    """
    elapsed = max(0, int((observation.observed_at - observation.started_at).total_seconds()))
    progress_at = (
        observation.last_progress.observed_at
        if observation.last_progress
        else observation.started_at
    )
    progress_idle = max(0, int((observation.observed_at - progress_at).total_seconds()))
    heartbeat_idle = (
        None
        if observation.last_heartbeat_at is None
        else max(0, int((observation.observed_at - observation.last_heartbeat_at).total_seconds()))
    )

    if observation.completed_at is not None:
        return _decision(
            policy,
            WatchdogDisposition.CONTINUE,
            elapsed,
            progress_idle,
            heartbeat_idle,
            False,
            False,
            "completion_already_accepted",
        )
    if not observation.lease_valid:
        return _decision(
            policy,
            WatchdogDisposition.LEASE_LOST,
            elapsed,
            progress_idle,
            heartbeat_idle,
            True,
            True,
            "execution_lease_lost",
        )
    if not observation.process_alive:
        return _decision(
            policy,
            WatchdogDisposition.PROCESS_LOST,
            elapsed,
            progress_idle,
            heartbeat_idle,
            True,
            True,
            "physical_process_not_alive",
        )
    heartbeat_failure = _heartbeat_failure(
        policy, observation, elapsed, progress_idle, heartbeat_idle
    )
    if heartbeat_failure is not None:
        return heartbeat_failure
    if elapsed >= policy.max_runtime_seconds:
        return _decision(
            policy,
            WatchdogDisposition.HARD_TIMEOUT,
            elapsed,
            progress_idle,
            heartbeat_idle,
            True,
            False,
            "max_runtime_exceeded",
        )
    yield_decision = _yield_decision(policy, observation, elapsed, progress_idle, heartbeat_idle)
    if yield_decision is not None:
        return yield_decision
    return _progress_health_decision(policy, elapsed, progress_idle, heartbeat_idle)


def _yield_decision(policy, observation, elapsed, progress_idle, heartbeat_idle):
    requested_at = observation.yield_requested_at
    if requested_at is None:
        return None
    yield_elapsed = max(0, int((observation.observed_at - requested_at).total_seconds()))
    if yield_elapsed >= policy.yield_deadline_seconds:
        return _decision(
            policy,
            WatchdogDisposition.YIELD_DEADLINE_EXCEEDED,
            elapsed,
            progress_idle,
            heartbeat_idle,
            True,
            True,
            "cooperative_yield_deadline_exceeded",
        )
    return _decision(
        policy,
        WatchdogDisposition.YIELD_REQUESTED,
        elapsed,
        progress_idle,
        heartbeat_idle,
        False,
        False,
        "cooperative_yield_requested",
    )


def _progress_health_decision(policy, elapsed, progress_idle, heartbeat_idle):
    stalled = (
        policy.no_progress_timeout_seconds is not None
        and elapsed >= policy.progress_start_grace_seconds
        and progress_idle >= policy.no_progress_timeout_seconds
    )
    if stalled:
        return _decision(
            policy,
            WatchdogDisposition.STALLED,
            elapsed,
            progress_idle,
            heartbeat_idle,
            True,
            False,
            "semantic_progress_stalled",
        )
    if policy.slow_after_seconds is not None and elapsed >= policy.slow_after_seconds:
        return _decision(
            policy,
            WatchdogDisposition.SLOW,
            elapsed,
            progress_idle,
            heartbeat_idle,
            False,
            False,
            "slow_threshold_exceeded",
        )
    return _decision(
        policy,
        WatchdogDisposition.CONTINUE,
        elapsed,
        progress_idle,
        heartbeat_idle,
        False,
        False,
        "within_runtime_policy",
    )


def _heartbeat_failure(policy, observation, elapsed, progress_idle, heartbeat_idle):
    if observation.last_heartbeat_at is None and elapsed >= policy.heartbeat_timeout_seconds:
        return _decision(
            policy,
            WatchdogDisposition.HEARTBEAT_LOST,
            elapsed,
            progress_idle,
            None,
            True,
            False,
            "executor_heartbeat_missing",
        )
    if heartbeat_idle is not None and heartbeat_idle >= policy.heartbeat_timeout_seconds:
        return _decision(
            policy,
            WatchdogDisposition.HEARTBEAT_LOST,
            elapsed,
            progress_idle,
            heartbeat_idle,
            True,
            False,
            "executor_heartbeat_stale",
        )
    return None


def _decision(policy, disposition, elapsed, idle, heartbeat_idle, terminate, output_fenced, reason):
    return WatchdogDecision(
        disposition=disposition,
        policy_revision=policy.policy_revision,
        elapsed_seconds=elapsed,
        progress_idle_seconds=idle,
        heartbeat_idle_seconds=heartbeat_idle,
        termination_required=terminate,
        output_fenced=output_fenced,
        reason=reason,
    )
