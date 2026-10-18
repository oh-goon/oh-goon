from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from oh_goon.runtime.execution import shards as subject

from ._shard_execution_support import context as _context
from ._shard_execution_support import plan as _plan


def test_cancellation_interrupts_admitted_retry_delay_without_launching_another_attempt(
    tmp_path: Path,
) -> None:
    cancel = threading.Event()
    plan = _plan(
        subject.ShardWork(
            shard_id="retry-cancel",
            work_identity="sha256:" + "7" * 64,
            argv=(sys.executable, "-S", "-c", "raise SystemExit(17)"),
        )
    )
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    started = time.monotonic()
    try:
        report = subject.execute_shard_plan(
            plan,
            cwd=tmp_path,
            workers=1,
            shard_timeout_seconds=5,
            retry_policy=subject.ShardRetryPolicy(
                max_attempts=2,
                retryable_exit_codes=(17,),
                reason="transient",
                delays_seconds=(2.0,),
            ),
            cancel_requested=cancel.is_set,
            execution_context=_context(),
        )
    finally:
        timer.cancel()
    elapsed = time.monotonic() - started

    result = report.results[0]
    assert elapsed < 1.2
    assert result.status == "CANCELLED"
    assert result.reason_code == "cancellation_requested_during_retry_delay"
    assert len(result.attempts) == 1
    assert result.attempts[0].returncode == 17
    assert result.attempts[0].retry_admitted is True
