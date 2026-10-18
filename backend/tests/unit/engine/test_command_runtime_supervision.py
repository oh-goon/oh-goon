from __future__ import annotations

import sys
import pytest

from oh_goon.core.models.runtime_supervision import RuntimeSupervisionPolicy, WatchdogDisposition
from oh_goon.engine.command import CommandSupervisionTerminated, CommandTimeoutExpired, run_command
from oh_goon.engine.command_supervision import CommandRuntimeSupervision


def policy(**changes):
    values = dict(
        max_runtime_seconds=2,
        heartbeat_interval_seconds=1,
        heartbeat_timeout_seconds=2,
        no_progress_timeout_seconds=None,
        termination_grace_seconds=0,
        quiescence_timeout_seconds=1,
        yield_deadline_seconds=1,
        policy_revision="test-policy-v1",
    )
    values.update(changes)
    return RuntimeSupervisionPolicy(**values)


def supervision(*, runtime_policy=None, lease_valid=lambda _e, _g: True):
    return CommandRuntimeSupervision(
        policy=runtime_policy or policy(),
        generation=7,
        heartbeat=lambda _e, _g: None,
        lease_valid=lease_valid,
        semantic_progress=lambda _e, _g: None,
    )


def test_lease_loss_terminates_real_process_and_preserves_exact_reason() -> None:
    observed = []
    with pytest.raises(CommandSupervisionTerminated) as raised:
        run_command(
            sys.executable, "-S", "-c", "import time; time.sleep(30)",
            timeout=None,
            runtime_supervision=supervision(lease_valid=lambda _e, _g: False),
            process_observer=observed.append,
        )
    assert raised.value.decision.disposition is WatchdogDisposition.LEASE_LOST
    assert raised.value.decision.output_fenced
    assert raised.value.supervisor.termination_receipt is not None
    assert raised.value.supervisor.termination_receipt.quiescent
    termination = next(event for event in observed if event.kind == "TERMINATION_REQUESTED")
    assert termination.termination_reason == "execution_lease_lost"


def test_resolved_watchdog_deadline_owns_hard_timeout() -> None:
    observed = []
    with pytest.raises(CommandTimeoutExpired) as raised:
        run_command(
            sys.executable, "-S", "-c", "import time; time.sleep(30)",
            timeout=None,
            runtime_supervision=supervision(runtime_policy=policy(max_runtime_seconds=1, heartbeat_timeout_seconds=2)),
            process_observer=observed.append,
        )
    assert raised.value.supervisor.termination_receipt is not None
    termination = next(event for event in observed if event.kind == "TERMINATION_REQUESTED")
    assert termination.termination_reason == "max_runtime_exceeded"


def test_scalar_timeout_cannot_compete_with_resolved_supervision_authority() -> None:
    with pytest.raises(ValueError, match="cannot both own"):
        run_command(sys.executable, "-S", "-c", "pass", timeout=1, runtime_supervision=supervision())



def test_semantic_stall_terminates_even_while_process_is_alive() -> None:
    observed = []
    stalled_policy = policy(
        max_runtime_seconds=2,
        heartbeat_timeout_seconds=2,
        no_progress_timeout_seconds=1,
    )
    with pytest.raises(CommandSupervisionTerminated) as raised:
        run_command(
            sys.executable, "-S", "-c", "import time; time.sleep(30)",
            timeout=None,
            runtime_supervision=supervision(runtime_policy=stalled_policy),
            process_observer=observed.append,
        )
    assert raised.value.decision.disposition is WatchdogDisposition.STALLED
    assert raised.value.decision.reason == "semantic_progress_stalled"
    termination = next(event for event in observed if event.kind == "TERMINATION_REQUESTED")
    assert termination.termination_reason == "semantic_progress_stalled"

def test_noisy_stdout_does_not_count_as_semantic_progress() -> None:
    noisy = "import time\nfor i in range(100):\n print(i, flush=True); time.sleep(0.05)"
    stalled_policy = policy(max_runtime_seconds=2, heartbeat_timeout_seconds=2, no_progress_timeout_seconds=1)
    with pytest.raises(CommandSupervisionTerminated) as raised:
        run_command(sys.executable, "-S", "-c", noisy, timeout=None, runtime_supervision=supervision(runtime_policy=stalled_policy))
    assert raised.value.decision.disposition is WatchdogDisposition.STALLED


def test_repeated_same_progress_sequence_does_not_refresh_stall_clock() -> None:
    from datetime import datetime, timezone
    from oh_goon.core.models.runtime_supervision import SemanticProgress

    progress = None
    def same_progress(execution_id: str, generation: int):
        nonlocal progress
        if progress is None:
            progress = SemanticProgress(execution_id=execution_id, generation=generation, sequence=1, observed_at=datetime.now(timezone.utc), work_unit="unit-1")
        return progress

    stalled_policy = policy(max_runtime_seconds=2, heartbeat_timeout_seconds=2, no_progress_timeout_seconds=1)
    authority = CommandRuntimeSupervision(
        policy=stalled_policy, generation=7, heartbeat=lambda _e, _g: None,
        lease_valid=lambda _e, _g: True, semantic_progress=same_progress,
    )
    with pytest.raises(CommandSupervisionTerminated) as raised:
        run_command(sys.executable, "-S", "-c", "import time; time.sleep(30)", timeout=None, runtime_supervision=authority)
    assert raised.value.decision.reason == "semantic_progress_stalled"
    assert raised.value.decision.progress_idle_seconds >= 1


def test_process_completion_before_hard_deadline_is_not_reclassified_as_timeout() -> None:
    result = run_command(
        sys.executable, "-S", "-c", "import time; time.sleep(0.2)",
        timeout=None,
        runtime_supervision=supervision(runtime_policy=policy(max_runtime_seconds=1, heartbeat_timeout_seconds=2)),
    )
    assert result.returncode == 0
    assert result.supervisor.termination_receipt is None


def test_cooperative_yield_deadline_terminates_real_process_distinctly() -> None:
    from datetime import datetime, timezone

    requested_at = None

    def yield_request(_execution_id: str, _generation: int):
        nonlocal requested_at
        if requested_at is None:
            requested_at = datetime.now(timezone.utc)
        return requested_at

    authority = CommandRuntimeSupervision(
        policy=policy(max_runtime_seconds=3, heartbeat_timeout_seconds=3, yield_deadline_seconds=1),
        generation=7,
        heartbeat=lambda _e, _g: None,
        lease_valid=lambda _e, _g: True,
        semantic_progress=lambda _e, _g: None,
        yield_requested_at=yield_request,
    )
    observed = []
    with pytest.raises(CommandSupervisionTerminated) as raised:
        run_command(
            sys.executable, "-S", "-c", "import time; time.sleep(30)",
            timeout=None,
            runtime_supervision=authority,
            process_observer=observed.append,
        )
    assert raised.value.decision.disposition is WatchdogDisposition.YIELD_DEADLINE_EXCEEDED
    assert raised.value.decision.output_fenced is True
    termination = next(event for event in observed if event.kind == "TERMINATION_REQUESTED")
    assert termination.termination_reason == "cooperative_yield_deadline_exceeded"
