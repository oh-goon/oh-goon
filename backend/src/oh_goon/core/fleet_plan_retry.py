from __future__ import annotations

from oh_goon.core.models.fleet_plan_retry import (
    AttemptsExhaustedPlanRetryDecisionEvidence,
    ExitCodeNotDeclaredPlanRetryDecisionEvidence,
    PlanRetryDecisionEvidence,
    ScheduledPlanRetryDecisionEvidence,
)
from oh_goon.core.models.fleet_plan_step_primitives import StepRetryPolicy


def retry_decision(
    policy: StepRetryPolicy,
    *,
    attempt_number: int,
    exit_code: int,
) -> PlanRetryDecisionEvidence | None:
    """Classify one completed failed attempt from admitted policy only.

    Absence means retry was not configured. Exceptions, cancellation, and ambiguous
    external effects never reach this function and therefore cannot be replayed.
    """

    if policy.max_attempts == 1:
        return None
    if exit_code not in policy.known_transient_exit_codes:
        return ExitCodeNotDeclaredPlanRetryDecisionEvidence(exit_code=exit_code)
    if attempt_number >= policy.max_attempts:
        assert policy.reason is not None
        return AttemptsExhaustedPlanRetryDecisionEvidence(
            exit_code=exit_code,
            policy_reason=policy.reason,
        )
    assert policy.reason is not None
    return ScheduledPlanRetryDecisionEvidence(
        exit_code=exit_code,
        policy_reason=policy.reason,
        delay_seconds=policy.delay_after(attempt_number),
        next_attempt=attempt_number + 1,
    )


def validate_retry_attempt_sequence(result: object) -> None:
    """Prove that every additional attempt is explained by the prior retry fact."""
    for step in getattr(result, "steps", ()):
        attempts = step.attempts
        if not attempts:
            continue
        numbers = tuple(item.attempt for item in attempts)
        expected = tuple(range(1, len(attempts) + 1))
        if numbers != expected:
            raise ValueError(
                f"Fleet plan step {step.step_id} attempt evidence must be consecutive from 1"
            )
        for index, attempt in enumerate(attempts):
            scheduled = isinstance(attempt.retry, ScheduledPlanRetryDecisionEvidence)
            has_next = index + 1 < len(attempts)
            if scheduled != has_next:
                raise ValueError(
                    f"Fleet plan step {step.step_id} retry evidence must exactly explain every additional attempt"
                )
            if scheduled and attempt.retry.next_attempt != attempts[index + 1].attempt:
                raise ValueError(
                    f"Fleet plan step {step.step_id} retry next_attempt does not match canonical attempt evidence"
                )
