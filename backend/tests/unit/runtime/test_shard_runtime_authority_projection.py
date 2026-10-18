from __future__ import annotations

import sys
from pathlib import Path

from oh_goon.runtime.execution import shards as subject


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def test_successful_shard_preserves_native_runtime_and_quiescence_receipt_identities(tmp_path: Path) -> None:
    work = subject.ShardWork(shard_id="a", argv=(sys.executable, "-c", "print('ok')"), work_identity=_sha("b"))
    plan = subject.ShardExecutionPlan(plan_identity=_sha("a"), generation_identity=_sha("1"), shards=(work,), source_schema="test.schema/v1", producer_version="test")
    context = subject.ShardExecutionContext(runner_toolchain_identity=_sha("c"), dependency_identity=_sha("d"), environment_identity=_sha("e"))

    report = subject.execute_shard_plan(plan, cwd=tmp_path, workers=1, shard_timeout_seconds=5, execution_context=context)

    attempt = report.canonical_evidence().results[0].attempts[0]
    assert attempt.runtime_authority_receipt_identity is not None
    assert attempt.supervisor_receipt_identity is not None
    assert attempt.process_group_quiescent is True
    assert attempt.forced_termination is False
