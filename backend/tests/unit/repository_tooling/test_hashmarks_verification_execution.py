from __future__ import annotations

import pytest

pytest.importorskip("hashmarks")

from hashmarks.producer_identity import native_producer_implementation_identity
from hashmarks.verification_selection import verification_member, verification_membership
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_VERSION,
)

from oh_goon.integrations.hashmarks.verification_selection import (
    HashmarksVerificationMember,
    VerifiedHashmarksVerificationSelection,
)
from oh_goon.repository_tooling.hashmarks_verification_execution import (
    VerificationExecutionGroupingPolicy,
    build_verification_execution_plan,
    build_verification_execution_receipt,
)
from oh_goon.runtime.execution.shard_models import (
    ShardAttemptEvidence,
    ShardExecutionContext,
    ShardResult,
    ShardRetryPolicy,
    build_execution_report,
    execution_policy_identity,
)

pytestmark = pytest.mark.dependency_complete


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _selection(count: int = 5) -> VerifiedHashmarksVerificationSelection:
    native = verification_membership(
        [
            verification_member("tests/test_x.py", test_symbol=f"test_{index}")
            for index in range(count)
        ]
    )
    members = tuple(
        HashmarksVerificationMember(
            path=str(row["path"]),
            test_symbol=str(row["test_symbol"]) if row["test_symbol"] is not None else None,
            member_id=str(row["member_id"]),
        )
        for row in native["members"]
    )
    return VerifiedHashmarksVerificationSelection(
        producer_version=TEST_HASHMARKS_VERSION,
        producer_artifact_identity=TEST_HASHMARKS_ARTIFACT_IDENTITY,
        producer_implementation_identity=native_producer_implementation_identity(),
        repository_identity="git-tree:abc",
        source_identity=_sha("d"),
        repository_provenance_identity=_sha("e"),
        membership_identity=str(native["membership_identity"]),
        producer_contract_identity=_sha("f"),
        codemap_generation=9,
        identity_generation=4,
        evidence_hashes=(("verification", _sha("a")),),
        members=members,
    )


def _context() -> ShardExecutionContext:
    return ShardExecutionContext(_sha("1"), _sha("2"), _sha("3"))


def _attempt(work_identity: str, number: int = 1, *, retry: bool = False) -> ShardAttemptEvidence:
    return ShardAttemptEvidence(
        attempt_identity=_sha(hex(number)[-1]),
        physical_work_identity=work_identity,
        attempt=number,
        status="PASS" if not retry else "TIMEOUT",
        returncode=0 if not retry else None,
        duration_ms=1,
        stdout_tail="",
        stderr_tail="",
        retry_admitted=retry,
        retry_reason="bounded retry" if retry else None,
    )


def test_oh_goon_owns_batch_grouping_not_hashmarks() -> None:
    selection = _selection(5)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(2),
    )
    assert [shard.members for shard in plan.shards] == [
        selection.selected_test_nodes[:2],
        selection.selected_test_nodes[2:4],
        selection.selected_test_nodes[4:],
    ]
    assert plan.selected_membership_identity == selection.membership_identity
    assert plan.source_schema == "hashmarks.downstream-verification-contract.v2"


def test_execution_plan_references_admitted_repository_provenance_without_recomputing() -> None:
    selection = _selection(2)
    admitted_repository_provenance = selection.repository_provenance_identity
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )

    assert plan.repository_evidence_identity == admitted_repository_provenance
    assert plan.verification_selection_evidence_identity == admitted_repository_provenance
    assert plan.producer_repository_generation == selection.codemap_generation
    assert plan.producer_identity_generation == selection.identity_generation
    assert plan.producer_evidence_hashes == selection.evidence_hashes
    assert plan.selected_membership_identity == selection.membership_identity
    assert plan.verification_revision_identity == selection.source_identity
    assert plan.producer_artifact_identity == selection.producer_artifact_identity
    assert plan.producer_implementation_identity == selection.producer_implementation_identity
    assert plan.producer_contract_identity == selection.producer_contract_identity


def test_terminal_receipt_proves_exact_original_hashmarks_coverage_with_retry_lineage() -> None:
    selection = _selection(2)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    results = tuple(
        ShardResult(
            shard_id=shard.shard_id,
            described_work_identity=shard.work_identity,
            work_identity=shard.work_identity,
            status="PASS",
            attempts=(
                _attempt(shard.work_identity, 1, retry=True),
                _attempt(shard.work_identity, 2),
            )
            if index == 0
            else (_attempt(shard.work_identity),),
            members=shard.members,
        )
        for index, shard in enumerate(plan.shards)
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=2,
        shard_timeout_seconds=90,
        suite_timeout_seconds=600,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=2,
            shard_timeout_seconds=90,
            suite_timeout_seconds=600,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=results,
        duration_ms=10,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.schema == "oh-goon.hashmarks-verification-execution-receipt.v4"
    assert receipt.repository_provenance_identity == selection.repository_provenance_identity
    assert receipt.terminal_success is True
    assert receipt.terminal_membership_identity == selection.membership_identity
    assert receipt.selected == selection.selected_test_nodes
    assert receipt.attempted == tuple(sorted(selection.selected_test_nodes))
    assert receipt.resumed == ()
    assert receipt.completed == tuple(sorted(selection.selected_test_nodes))
    assert receipt.missing == ()
    assert receipt.duplicates == ()
    assert receipt.extras == ()
    assert receipt.retry_bisection_lineage[0]["attempts"][0]["retry_admitted"] is True  # type: ignore[index]


def test_bisection_children_preserve_terminal_membership_without_duplicate_coverage() -> None:
    selection = _selection(2)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(2),
    )
    parent = plan.shards[0]
    children = tuple(
        ShardResult(
            shard_id=f"{parent.shard_id}-child-{index}",
            described_work_identity=parent.work_identity,
            work_identity=_sha(str(index + 4)),
            status="PASS",
            attempts=(_attempt(_sha(str(index + 4))),),
            parent_work_identity=parent.work_identity,
            refinement_depth=1,
            members=(member,),
        )
        for index, member in enumerate(parent.members)
    )
    result = ShardResult(
        shard_id=parent.shard_id,
        described_work_identity=parent.work_identity,
        work_identity=parent.work_identity,
        status="REFINED_PASS",
        attempts=(_attempt(parent.work_identity, retry=True),),
        members=parent.members,
        children=children,
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=1,
        shard_timeout_seconds=90,
        suite_timeout_seconds=600,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=90,
            suite_timeout_seconds=600,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(result,),
        duration_ms=10,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.terminal_success is True
    assert receipt.missing == receipt.duplicates == receipt.extras == ()
    assert len(receipt.retry_bisection_lineage) == 3


def test_incomplete_terminal_receipt_names_missing_member_and_never_claims_hashmarks_identity() -> (
    None
):
    selection = _selection(2)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    passed = ShardResult(
        shard_id=plan.shards[0].shard_id,
        described_work_identity=plan.shards[0].work_identity,
        work_identity=plan.shards[0].work_identity,
        status="PASS",
        attempts=(_attempt(plan.shards[0].work_identity),),
        members=plan.shards[0].members,
    )
    failed = ShardResult(
        shard_id=plan.shards[1].shard_id,
        described_work_identity=plan.shards[1].work_identity,
        work_identity=plan.shards[1].work_identity,
        status="FAIL",
        attempts=(),
        members=plan.shards[1].members,
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=1,
        shard_timeout_seconds=90,
        suite_timeout_seconds=600,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=90,
            suite_timeout_seconds=600,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(passed, failed),
        duration_ms=10,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.terminal_success is False
    assert receipt.terminal_membership_identity is None
    assert receipt.missing == plan.shards[1].members


def test_resumed_terminal_member_is_completed_without_being_attempted() -> None:
    selection = _selection(1)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    shard = plan.shards[0]
    resumed = ShardResult(
        shard_id=shard.shard_id,
        described_work_identity=shard.work_identity,
        work_identity=shard.work_identity,
        status="RESUMED_PASS",
        attempts=(),
        resumed=True,
        members=shard.members,
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=1,
        shard_timeout_seconds=90,
        suite_timeout_seconds=600,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=90,
            suite_timeout_seconds=600,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(resumed,),
        duration_ms=1,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.terminal_success is True
    assert receipt.attempted == ()
    assert receipt.resumed == selection.selected_test_nodes
    assert receipt.completed == selection.selected_test_nodes


def test_attempted_unselected_member_is_explicit_extra_even_when_not_completed() -> None:
    selection = _selection(1)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    shard = plan.shards[0]
    extra = "tests/test_unexpected.py::test_extra"
    result = ShardResult(
        shard_id=shard.shard_id,
        described_work_identity=shard.work_identity,
        work_identity=shard.work_identity,
        status="FAIL",
        attempts=(_attempt(shard.work_identity),),
        members=(extra,),
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=1,
        shard_timeout_seconds=90,
        suite_timeout_seconds=600,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=90,
            suite_timeout_seconds=600,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(result,),
        duration_ms=1,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.terminal_success is False
    assert receipt.attempted == (extra,)
    assert receipt.completed == ()
    assert receipt.missing == selection.selected_test_nodes
    assert receipt.extras == (extra,)
    assert receipt.terminal_membership_identity is None


def test_killed_or_timed_out_attempt_is_not_terminal_completion() -> None:
    selection = _selection(1)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    shard = plan.shards[0]
    timed_out = ShardResult(
        shard_id=shard.shard_id,
        described_work_identity=shard.work_identity,
        work_identity=shard.work_identity,
        status="TIMEOUT",
        attempts=(_attempt(shard.work_identity, retry=True),),
        members=shard.members,
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=1,
        shard_timeout_seconds=1,
        suite_timeout_seconds=2,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=1,
            suite_timeout_seconds=2,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(timed_out,),
        duration_ms=1,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.attempted == selection.selected_test_nodes
    assert receipt.resumed == ()
    assert receipt.completed == ()
    assert receipt.missing == selection.selected_test_nodes
    assert receipt.terminal_membership_identity is None
    assert receipt.terminal_success is False


def test_two_terminal_pass_coverages_are_explicit_duplicates() -> None:
    selection = _selection(1)
    plan = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    shard = plan.shards[0]
    member = shard.members[0]
    results = tuple(
        ShardResult(
            shard_id=f"{shard.shard_id}-{index}",
            described_work_identity=shard.work_identity,
            work_identity=_sha(str(index + 6)),
            status="PASS",
            attempts=(_attempt(_sha(str(index + 6))),),
            members=(member,),
        )
        for index in range(2)
    )
    report = build_execution_report(
        plan=plan,
        context=_context(),
        workers=1,
        shard_timeout_seconds=90,
        suite_timeout_seconds=600,
        minimum_execution_window_seconds=0.05,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=90,
            suite_timeout_seconds=600,
            minimum_execution_window_seconds=0.05,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=results,
        duration_ms=1,
    )
    receipt = build_verification_execution_receipt(selection, report)
    assert receipt.completed == (member, member)
    assert receipt.duplicates == (member,)
    assert receipt.terminal_membership_identity is None
    assert receipt.terminal_success is False


def test_grouping_policy_is_oh_goon_identity_bound_and_conserves_admitted_order() -> None:
    selection = _selection(5)
    policy = VerificationExecutionGroupingPolicy(2)
    groups = policy.group(selection.members)
    assert tuple(member for group in groups for member in group) == selection.members
    assert [len(group) for group in groups] == [2, 2, 1]
    assert policy.policy_identity.startswith("sha256:")
    assert policy.policy_identity != VerificationExecutionGroupingPolicy(3).policy_identity


def test_physical_grouping_changes_plan_identity_not_hashmarks_membership_identity() -> None:
    selection = _selection(5)
    one = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(1),
    )
    three = build_verification_execution_plan(
        selection,
        command_prefix=("pytest",),
        grouping_policy=VerificationExecutionGroupingPolicy(3),
    )
    assert one.plan_identity != three.plan_identity
    assert (
        one.selected_membership_identity
        == three.selected_membership_identity
        == selection.membership_identity
    )
    assert (
        tuple(member for shard in one.shards for member in shard.members)
        == selection.selected_test_nodes
    )
    assert (
        tuple(member for shard in three.shards for member in shard.members)
        == selection.selected_test_nodes
    )


def test_grouping_policy_rejects_nonpositive_group_capacity() -> None:
    for value in (0, -1):
        try:
            VerificationExecutionGroupingPolicy(value)
        except ValueError as exc:
            assert "max_members_per_group" in str(exc)
        else:  # pragma: no cover - defensive
            raise AssertionError("nonpositive grouping capacity was admitted")
