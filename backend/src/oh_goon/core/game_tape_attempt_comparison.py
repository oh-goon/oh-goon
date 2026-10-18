from __future__ import annotations

from oh_goon.core.models.fleet_plan_evidence import PlanStepAttemptEvidence
from oh_goon.core.models.game_tape import GameTapeAttemptTransition


def compare_adjacent_attempts(
    attempts: tuple[PlanStepAttemptEvidence, ...],
) -> tuple[GameTapeAttemptTransition, ...]:
    """Compare only adjacent canonical physical attempts; never reinterpret retry policy."""
    return tuple(
        _transition(previous, current)
        for previous, current in zip(attempts, attempts[1:], strict=False)
    )


def _transition(
    previous: PlanStepAttemptEvidence,
    current: PlanStepAttemptEvidence,
) -> GameTapeAttemptTransition:
    retry = previous.retry
    retry_linked = bool(
        retry is not None
        and retry.disposition == "SCHEDULED"
        and retry.next_attempt == current.attempt
    )
    return GameTapeAttemptTransition(
        from_attempt=previous.attempt,
        to_attempt=current.attempt,
        retry_linked=retry_linked,
        duration_delta_ms=current.duration_ms - previous.duration_ms,
        returncode_changed=current.returncode != previous.returncode,
        argv_changed=current.argv != previous.argv,
        repository_state_continuity=(
            previous.repository_state_after == current.repository_state_before
        ),
        tool_changed=_tool_identity(previous) != _tool_identity(current),
        egress_changed=previous.egress.policy_id != current.egress.policy_id,
        resource_policy_changed=_resource_policy(previous) != _resource_policy(current),
        cache_input_changed=previous.cache_input != current.cache_input,
        stdout_changed=previous.stdout_evidence.sha256 != current.stdout_evidence.sha256,
        stderr_changed=previous.stderr_evidence.sha256 != current.stderr_evidence.sha256,
    )


def _tool_identity(attempt: PlanStepAttemptEvidence) -> tuple[str, str, str, int] | None:
    tool = attempt.tool
    if tool is None:
        return None
    return (tool.name, tool.origin, tool.sha256, tool.byte_count)


def _resource_policy(attempt: PlanStepAttemptEvidence) -> str | None:
    return attempt.resources.policy_id if attempt.resources is not None else None
