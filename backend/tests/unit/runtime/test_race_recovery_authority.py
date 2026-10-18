import pytest

from oh_goon.core.models.race_recovery import (
    RecoveryBudget, TerminalAuthority, TerminalClaim, WorkMembership,
)
from oh_goon.runtime.race_recovery import TerminalRaceAuthority


def _claim(authority: TerminalAuthority, sequence: int = 1, generation: int = 1) -> TerminalClaim:
    return TerminalClaim(execution_id="exec-1", generation=generation, authority=authority,
                         sequence=sequence, evidence_id=f"evidence-{authority.value}-{sequence}")


def test_completion_vs_timeout_has_exactly_one_winner() -> None:
    race = TerminalRaceAuthority()
    assert race.claim(_claim(TerminalAuthority.COMPLETION)).authority is TerminalAuthority.COMPLETION
    assert race.claim(_claim(TerminalAuthority.TIMEOUT, 2)).authority is TerminalAuthority.COMPLETION


def test_timeout_vs_late_completion_has_exactly_one_winner() -> None:
    race = TerminalRaceAuthority()
    assert race.claim(_claim(TerminalAuthority.TIMEOUT)).authority is TerminalAuthority.TIMEOUT
    assert race.claim(_claim(TerminalAuthority.COMPLETION, 2)).authority is TerminalAuthority.TIMEOUT


def test_exact_terminal_redelivery_is_idempotent() -> None:
    race = TerminalRaceAuthority(); claim = _claim(TerminalAuthority.CANCEL)
    assert race.claim(claim).claim_id == race.claim(claim).claim_id


def test_old_generation_cannot_rewrite_terminal_authority() -> None:
    race = TerminalRaceAuthority(); race.claim(_claim(TerminalAuthority.COMPLETION, generation=2))
    with pytest.raises(ValueError, match="another execution generation"):
        race.claim(_claim(TerminalAuthority.TIMEOUT, generation=1))


def test_recovery_budget_is_cumulative_across_attempts() -> None:
    budget = RecoveryBudget(lineage_id="lineage-1", max_attempts=3, attempts_consumed=0,
                            max_runtime_seconds=100, runtime_consumed_seconds=0)
    budget = budget.consume(runtime_seconds=40).consume(runtime_seconds=35)
    assert (budget.attempts_consumed, budget.runtime_consumed_seconds, budget.exhausted) == (2, 75, False)
    budget = budget.consume(runtime_seconds=25)
    assert budget.exhausted


def test_recovery_budget_cannot_be_reset_by_reconstruction() -> None:
    with pytest.raises(ValueError, match="cumulative recovery budget"):
        RecoveryBudget(lineage_id="lineage-1", max_attempts=2, attempts_consumed=3,
                       max_runtime_seconds=100, runtime_consumed_seconds=10)


def test_work_membership_conserves_exact_admitted_set() -> None:
    membership = WorkMembership(lineage_id="lineage-1", expected_work_ids=frozenset({"a", "b"}))
    membership = membership.commit("a").commit("a")
    assert membership.committed_work_ids == frozenset({"a"})
    assert not membership.complete
    assert membership.commit("b").complete


def test_work_membership_rejects_unadmitted_redelivery() -> None:
    membership = WorkMembership(lineage_id="lineage-1", expected_work_ids=frozenset({"a"}))
    with pytest.raises(ValueError, match="not admitted"):
        membership.commit("old-message")
