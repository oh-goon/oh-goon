from __future__ import annotations

from dataclasses import replace

from oh_goon.runtime.execution import executed_membership_identity as membership_identity
from oh_goon.runtime.execution.offline_proof import verify_completed_shard_proof
from oh_goon.runtime.execution.shard_models import (
    ShardExecutionContext,
    ShardExecutionPlan,
    ShardResult,
    ShardWork,
    build_execution_report,
    execution_policy_identity,
    ShardRetryPolicy,
)


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def _report(*, member: str = "tests/test_a.py"):
    work = ShardWork(
        shard_id="a",
        argv=("pytest", member),
        work_identity=_sha("a"),
        members=(member,),
        command_prefix=("pytest",),
    )
    plan = ShardExecutionPlan(
        plan_identity=_sha("b"),
        generation_identity=_sha("c"),
        shards=(work,),
        source_schema="test/v1",
        producer_version="0.14.0",
        selected_test_nodes=(member,),
        selected_membership_identity=_sha("d"),
        verification_selection_evidence_identity=_sha("e"),
        verification_revision_identity=_sha("f"),
    )
    context = ShardExecutionContext(
        runner_toolchain_identity=_sha("1"),
        dependency_identity=_sha("2"),
        environment_identity=_sha("3"),
    )
    result = ShardResult(
        shard_id="a",
        described_work_identity=work.work_identity,
        work_identity=work.work_identity,
        status="PASS",
        attempts=(),
        members=(member,),
    )
    return build_execution_report(
        plan=plan,
        context=context,
        workers=1,
        shard_timeout_seconds=10,
        suite_timeout_seconds=20,
        minimum_execution_window_seconds=1,
        execution_policy_identity=execution_policy_identity(
            workers=1,
            shard_timeout_seconds=10,
            suite_timeout_seconds=20,
            minimum_execution_window_seconds=1,
            retry_policy=ShardRetryPolicy(),
            timeout_refinement=True,
            resume=True,
        ),
        results=(result,),
        duration_ms=1,
    )


def test_report_and_offline_verifier_share_one_membership_identity_owner() -> None:
    report = _report(member="tests/test_a.py::test_case[param]")

    assert report.actual_executed_membership_identity == (
        membership_identity.executed_verification_membership_identity(
            report.actual_executed_members
        )
    )
    assert verify_completed_shard_proof(report).valid is True


def test_membership_schema_change_requires_only_owner_change(monkeypatch) -> None:
    baseline = _report()
    monkeypatch.setattr(
        membership_identity,
        "EXECUTED_VERIFICATION_MEMBERSHIP_SCHEMA",
        "oh-goon.executed-verification-membership/v99",
    )

    changed = _report()

    assert changed.actual_executed_membership_identity != (
        baseline.actual_executed_membership_identity
    )
    assert verify_completed_shard_proof(changed).valid is True


def test_offline_verifier_rejects_tampered_membership_identity() -> None:
    report = _report()
    tampered = replace(report, actual_executed_membership_identity=_sha("9"))

    verified = verify_completed_shard_proof(tampered)

    assert verified.valid is False
    assert "executed membership identity mismatch" in verified.reasons


def test_offline_verifier_rejects_tampered_member_content_even_with_matching_identity() -> None:
    report = _report()
    tampered_members = ("tests/test_other.py",)
    tampered = replace(
        report,
        actual_executed_members=tampered_members,
        actual_executed_membership_identity=(
            membership_identity.executed_verification_membership_identity(
                tampered_members
            )
        ),
    )

    verified = verify_completed_shard_proof(tampered)

    assert verified.valid is False
    assert "terminal membership differs from admitted selection" in verified.reasons
