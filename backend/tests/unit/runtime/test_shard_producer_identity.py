from oh_goon.runtime.execution.shard_models import (
    ShardExecutionContext,
    ShardExecutionPlan,
    ShardResult,
    ShardWork,
    build_execution_report,
)


def test_canonical_shard_evidence_preserves_validated_producer_implementation_identity() -> None:
    work = ShardWork(shard_id="s", argv=("true",), work_identity="sha256:" + "1" * 64)
    producer_identity = "sha256:" + "2" * 64
    plan = ShardExecutionPlan(
        plan_identity="sha256:" + "3" * 64,
        generation_identity="sha256:" + "4" * 64,
        shards=(work,),
        source_schema="hashmarks.verification-selection.v1",
        producer_version="0.14.0",
        producer_implementation_identity=producer_identity,
    )
    context = ShardExecutionContext(
        runner_toolchain_identity="sha256:" + "5" * 64,
        dependency_identity="sha256:" + "6" * 64,
        environment_identity="sha256:" + "7" * 64,
    )
    report = build_execution_report(
        plan=plan,
        context=context,
        workers=1,
        shard_timeout_seconds=1.0,
        suite_timeout_seconds=None,
        minimum_execution_window_seconds=0.0,
        execution_policy_identity="sha256:" + "8" * 64,
        results=(
            ShardResult(
                shard_id="s",
                described_work_identity=work.work_identity,
                work_identity=work.work_identity,
                status="PASS",
                attempts=(),
            ),
        ),
        duration_ms=1,
    )

    assert report.canonical_evidence().producer_implementation_identity == producer_identity
