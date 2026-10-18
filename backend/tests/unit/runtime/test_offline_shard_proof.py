from __future__ import annotations

from dataclasses import replace

from oh_goon.runtime.execution.offline_proof import verify_completed_shard_proof
from oh_goon.runtime.execution.shard_models import ShardExecutionContext, ShardExecutionPlan, ShardResult, ShardWork, build_execution_report, ShardRetryPolicy, execution_policy_identity


def _sha(c: str) -> str:
    return "sha256:" + c * 64


def _report():
    work = ShardWork(shard_id="a", argv=("pytest", "tests/test_a.py"), work_identity=_sha("a"), members=("tests/test_a.py",), command_prefix=("pytest",))
    plan = ShardExecutionPlan(plan_identity=_sha("b"), generation_identity=_sha("c"), shards=(work,), source_schema="test/v1", producer_version="0.14.0", producer_artifact_identity=_sha("d"), producer_implementation_identity=_sha("e"), producer_contract_identity=_sha("4"), selected_test_nodes=("tests/test_a.py",), selected_membership_identity=_sha("f"), verification_selection_evidence_identity=_sha("5"), verification_revision_identity=_sha("6"))
    context = ShardExecutionContext(runner_toolchain_identity=_sha("1"), dependency_identity=_sha("2"), environment_identity=_sha("3"))
    result = ShardResult(shard_id="a", described_work_identity=_sha("a"), work_identity=_sha("a"), status="PASS", attempts=(), members=("tests/test_a.py",))
    return build_execution_report(plan=plan, context=context, workers=1, shard_timeout_seconds=10, suite_timeout_seconds=20, minimum_execution_window_seconds=1, execution_policy_identity=execution_policy_identity(workers=1, shard_timeout_seconds=10, suite_timeout_seconds=20, minimum_execution_window_seconds=1, retry_policy=ShardRetryPolicy(), timeout_refinement=True, resume=True), results=(result,), duration_ms=1)


def test_offline_proof_accepts_exact_terminal_authority_without_rerun() -> None:
    report = _report()
    verified = verify_completed_shard_proof(report, expected_plan_identity=report.plan_identity, expected_generation_identity=report.generation_identity, expected_execution_policy_identity=report.execution_policy_identity, expected_producer_artifact_identity=report.producer_artifact_identity, expected_producer_implementation_identity=report.producer_implementation_identity)
    assert verified.valid is True
    assert verified.reasons == ()
    assert verified.proof_identity.startswith("sha256:")


def test_offline_proof_rejects_each_bound_authority_tamper() -> None:
    report = _report()
    cases = (
        replace(report, execution_context_identity=_sha("9")),
        replace(report, actual_executed_membership_identity=_sha("8")),
        replace(report, membership_complete=False),
    )
    for tampered in cases:
        assert verify_completed_shard_proof(tampered).valid is False


def test_offline_proof_rejects_cross_paired_expected_producer() -> None:
    report = _report()
    verified = verify_completed_shard_proof(report, expected_producer_implementation_identity=_sha("9"))
    assert verified.valid is False
    assert "producer implementation identity mismatch" in verified.reasons


def test_offline_proof_result_is_inspection_only_not_execution_admission() -> None:
    report = _report()
    verified = verify_completed_shard_proof(report)
    assert verified.valid is True
    assert not hasattr(verified, "execute")
    assert not hasattr(verified, "admit")
    assert not hasattr(verified, "reuse")
    assert not hasattr(verified, "execution_plan")


def test_offline_proof_rejects_expected_execution_policy_mismatch() -> None:
    report = _report()
    verified = verify_completed_shard_proof(
        report, expected_execution_policy_identity=_sha("9")
    )
    assert verified.valid is False
    assert "execution policy identity mismatch" in verified.reasons
