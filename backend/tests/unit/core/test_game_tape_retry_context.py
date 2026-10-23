from __future__ import annotations

from oh_goon.core.game_tape_retry_context import summarize_retry_context
from oh_goon.core.models.game_tape import GameTapeAttemptTransition


def _transition(**changes: bool) -> GameTapeAttemptTransition:
    values = {
        "from_attempt": 1,
        "to_attempt": 2,
        "retry_linked": True,
        "duration_delta_ms": 100,
        "returncode_changed": False,
        "argv_changed": False,
        "repository_state_continuity": True,
        "tool_changed": False,
        "egress_changed": False,
        "resource_policy_changed": False,
        "cache_input_changed": False,
        "stdout_changed": True,
        "stderr_changed": False,
    }
    values.update(changes)
    return GameTapeAttemptTransition(**values)


def test_retry_context_ignores_output_only_changes() -> None:
    summary = summarize_retry_context(2, (_transition(stdout_changed=True),))

    assert summary.classification == "SAME_PHYSICAL_CONTEXT"
    assert summary.retry_count == 1
    assert summary.linked_retry_count == 1
    assert summary.physical_context_changed is False


def test_retry_context_reports_physical_change() -> None:
    summary = summarize_retry_context(2, (_transition(tool_changed=True),))

    assert summary.classification == "CHANGED_PHYSICAL_CONTEXT"
    assert summary.physical_context_changed is True


def test_retry_context_reports_incomplete_linkage() -> None:
    summary = summarize_retry_context(2, (_transition(retry_linked=False),))

    assert summary.classification == "INCOMPLETE_RETRY_EVIDENCE"
    assert summary.linked_retry_count == 0


def test_retry_context_reports_not_retried() -> None:
    summary = summarize_retry_context(1, ())

    assert summary.classification == "NOT_RETRIED"
    assert summary.retry_count == 0


def test_retry_context_preserves_missing_attempt_evidence() -> None:
    summary = summarize_retry_context(0, ())

    assert summary.classification == "NO_ATTEMPT_EVIDENCE"
    assert summary.retry_count == 0


def test_retry_context_reports_changed_isolation_evidence() -> None:
    summary = summarize_retry_context(2, (_transition(isolation_changed=True),))

    assert summary.classification == "CHANGED_PHYSICAL_CONTEXT"
    assert summary.physical_context_changed is True
