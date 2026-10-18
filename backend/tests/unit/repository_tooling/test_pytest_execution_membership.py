from __future__ import annotations

from pathlib import Path

from oh_goon.repository_tooling.pytest_execution_membership import (
    ExecutionMembershipStore,
    execution_attempt_identity,
    execution_run_identity,
    logical_membership_identity,
)


def _store(tmp_path: Path) -> ExecutionMembershipStore:
    return ExecutionMembershipStore(
        tmp_path,
        suite_identity="sha256:" + "a" * 64,
        execution_plan_identity="sha256:" + "b" * 64,
        run_identity="sha256:" + "c" * 64,
    )


def _attempt(member: str, ordinal: int) -> str:
    return execution_attempt_identity(
        run_identity="sha256:" + "c" * 64,
        root_batch_identity="sha256:" + "d" * 64,
        root_attempt=1,
        launch_ordinal=ordinal,
        members=(member,),
    )


def test_logical_membership_identity_is_layout_and_order_independent() -> None:
    first = logical_membership_identity(("b", "a", "c"))
    second = logical_membership_identity(("c", "b", "a"))
    assert first == second
    assert first != logical_membership_identity(("a", "b"))


def test_execution_run_identity_is_unique_even_for_same_plan() -> None:
    kwargs = {
        "suite_identity": "sha256:" + "a" * 64,
        "execution_plan_identity": "sha256:" + "b" * 64,
    }
    assert execution_run_identity(**kwargs) != execution_run_identity(**kwargs)


def test_failed_attempt_then_retry_passes_without_duplicate_terminal_coverage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_a.py::test_one"
    first, second = _attempt(member, 1), _attempt(member, 2)
    store.record_started(member, attempt_identity=first, observed_batch_identity="sha256:" + "e" * 64)
    store.record_terminal(
        member,
        attempt_identity=first,
        observed_batch_identity="sha256:" + "e" * 64,
        status="FAILED",
    )
    store.record_started(member, attempt_identity=second, observed_batch_identity="sha256:" + "f" * 64)
    store.record_terminal(
        member,
        attempt_identity=second,
        observed_batch_identity="sha256:" + "f" * 64,
        status="PASSED",
    )
    lineage = ({"kind": "environment_retry", "action": "requeue_unproven_members"},)
    receipt, path = store.finalize(
        selected=(member,), resumed=(), suite_passed=True, recovery_lineage=lineage
    )
    assert receipt.terminal_success
    assert receipt.attempted == (member,)
    assert receipt.completed == (member,)
    assert receipt.passed == (member,)
    assert receipt.failed == (member,)
    assert receipt.duplicates == ()
    assert len(receipt.attempt_records) == 2
    assert receipt.retry_bisection_lineage == lineage
    assert receipt.terminal_membership_identity == receipt.selected_membership_identity
    assert path.exists()
    assert not list(path.parent.rglob("*.tmp"))


def test_started_but_nonterminal_member_is_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_timeout.py"
    store.record_started(
        member,
        attempt_identity=_attempt(member, 1),
        observed_batch_identity="sha256:" + "e" * 64,
    )
    receipt, _ = store.finalize(
        selected=(member,), resumed=(), suite_passed=False, recovery_lineage=()
    )
    assert receipt.attempted == (member,)
    assert receipt.completed == ()
    assert receipt.missing == (member,)
    assert receipt.terminal_membership_identity is None
    assert not receipt.terminal_success


def test_resumed_member_counts_as_completed_without_new_attempt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_resume.py"
    receipt, _ = store.finalize(
        selected=(member,), resumed=(member,), suite_passed=True, recovery_lineage=()
    )
    assert receipt.attempted == ()
    assert receipt.resumed == (member,)
    assert receipt.completed == (member,)
    assert receipt.passed == (member,)
    assert receipt.missing == ()
    assert receipt.terminal_success


def test_pytest_accepted_non_passed_member_closes_execution_membership(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_optional.py::test_platform_specific"
    store.record_terminal(
        member,
        attempt_identity=_attempt(member, 1),
        observed_batch_identity="sha256:" + "e" * 64,
        status="NON_PASSED",
    )
    receipt, _ = store.finalize(
        selected=(member,), resumed=(), suite_passed=True, recovery_lineage=()
    )
    assert receipt.completed == (member,)
    assert receipt.passed == ()
    assert receipt.non_passed == (member,)
    assert receipt.missing == ()
    assert receipt.terminal_membership_identity == receipt.selected_membership_identity
    assert receipt.terminal_success


def test_non_passed_member_cannot_override_failed_suite_status(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_optional.py::test_platform_specific"
    store.record_terminal(
        member,
        attempt_identity=_attempt(member, 1),
        observed_batch_identity="sha256:" + "e" * 64,
        status="NON_PASSED",
    )
    receipt, _ = store.finalize(
        selected=(member,), resumed=(), suite_passed=False, recovery_lineage=()
    )
    assert receipt.completed == (member,)
    assert receipt.non_passed == (member,)
    assert receipt.terminal_membership_identity is None
    assert not receipt.terminal_success


def test_two_successful_attempts_are_explicit_duplicate_coverage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_duplicate.py"
    for ordinal in (1, 2):
        attempt = _attempt(member, ordinal)
        store.record_terminal(
            member,
            attempt_identity=attempt,
            observed_batch_identity="sha256:" + str(ordinal) * 64,
            status="PASSED",
        )
    receipt, _ = store.finalize(
        selected=(member,), resumed=(), suite_passed=True, recovery_lineage=()
    )
    assert receipt.duplicates == (member,)
    assert receipt.terminal_membership_identity is None
    assert not receipt.terminal_success


def test_unexpected_collected_item_is_explicit_extra(tmp_path: Path) -> None:
    store = _store(tmp_path)
    member = "tests/test_expected.py"
    attempt = _attempt(member, 1)
    store.record_terminal(
        member,
        attempt_identity=attempt,
        observed_batch_identity="sha256:" + "e" * 64,
        status="PASSED",
    )
    store.record_extra("tests/test_other.py::test_unexpected", attempt_identity=attempt)
    receipt, _ = store.finalize(
        selected=(member,), resumed=(), suite_passed=True, recovery_lineage=()
    )
    assert receipt.extras == ("tests/test_other.py::test_unexpected",)
    assert not receipt.terminal_success
