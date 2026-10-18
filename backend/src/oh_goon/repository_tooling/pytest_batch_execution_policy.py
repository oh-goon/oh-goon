"""Small deterministic execution-budget policies for pytest batch launches."""
from __future__ import annotations

import math
import time


def refinement_probe_timeout(*, leaf_timeout: int, member_count: int) -> int:
    if member_count <= 1:
        return leaf_timeout
    levels = max(2, math.ceil(math.log2(member_count)) + 1)
    return max(10, math.ceil(leaf_timeout / levels))


def admitted_execution_timeout(
    *,
    leaf_timeout: int,
    member_count: int,
    is_refinement: bool,
    timeout_bisection: bool,
    deadline: float | None,
) -> tuple[int, str | None]:
    timeout = (
        refinement_probe_timeout(leaf_timeout=leaf_timeout, member_count=member_count)
        if timeout_bisection and is_refinement
        else leaf_timeout
    )
    if deadline is None:
        return timeout, None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return timeout, "BATCH RECOVERY STOPPED: suite deadline exhausted\n"
    if remaining < timeout:
        return (
            timeout,
            "BATCH RECOVERY STOPPED: insufficient suite budget for full "
            f"refinement proof required={timeout}s remaining={max(0, math.floor(remaining))}s\n",
        )
    return timeout, None
