from __future__ import annotations

from oh_goon.core.effect_attempts import (
    AUTOMATIC_RECOVERY_EFFECT_STATUSES,
    EXTERNAL_OUTCOME_UNCERTAIN_STATUSES,
    LIVE_OWNED_EFFECT_STATUSES,
    OPERATOR_REQUIRED_EFFECT_STATUSES,
    RECONCILABLE_EFFECT_STATUSES,
    quarantines_repository,
    require_effect_attempt_transition,
    unresolved_status_for_reconciliation,
)
from oh_goon.core.vocabulary import CommandSafety, EffectAttemptStatus, ReconciliationOutcome


def test_effect_attempt_policy_keeps_only_unresolved_reconciliation_states() -> None:
    assert unresolved_status_for_reconciliation(ReconciliationOutcome.CONFIRMED_APPLIED) is None
    assert unresolved_status_for_reconciliation(ReconciliationOutcome.CONFIRMED_NOT_APPLIED) is None
    assert (
        unresolved_status_for_reconciliation(ReconciliationOutcome.PARTIALLY_APPLIED)
        is EffectAttemptStatus.PARTIALLY_APPLIED
    )
    assert (
        unresolved_status_for_reconciliation(ReconciliationOutcome.INDETERMINATE)
        is EffectAttemptStatus.INDETERMINATE
    )


def test_effect_attempt_policy_allows_rereconciliation_to_same_evidence_state() -> None:
    require_effect_attempt_transition(
        EffectAttemptStatus.PARTIALLY_APPLIED,
        EffectAttemptStatus.PARTIALLY_APPLIED,
        allow_same=True,
    )


def test_effect_attempt_policy_centralizes_quarantine_and_retry_rules() -> None:
    assert quarantines_repository(
        EffectAttemptStatus.OUTCOME_UNKNOWN,
        CommandSafety.WORKTREE_MUTATION,
    )
    assert not quarantines_repository(
        EffectAttemptStatus.OUTCOME_UNKNOWN,
        CommandSafety.READ_ONLY,
    )


def test_effect_attempt_policy_owns_named_lifecycle_status_groups() -> None:
    assert {
        EffectAttemptStatus.CLAIMED,
        EffectAttemptStatus.EFFECT_STARTED,
    } == LIVE_OWNED_EFFECT_STATUSES
    assert {
        *LIVE_OWNED_EFFECT_STATUSES,
        EffectAttemptStatus.OUTCOME_UNKNOWN,
    } == AUTOMATIC_RECOVERY_EFFECT_STATUSES
    assert {
        EffectAttemptStatus.OUTCOME_UNKNOWN,
        EffectAttemptStatus.PARTIALLY_APPLIED,
        EffectAttemptStatus.INDETERMINATE,
    } == RECONCILABLE_EFFECT_STATUSES
    assert {
        EffectAttemptStatus.PARTIALLY_APPLIED,
        EffectAttemptStatus.INDETERMINATE,
    } == OPERATOR_REQUIRED_EFFECT_STATUSES
    assert {
        EffectAttemptStatus.EFFECT_STARTED,
        *RECONCILABLE_EFFECT_STATUSES,
    } == EXTERNAL_OUTCOME_UNCERTAIN_STATUSES
