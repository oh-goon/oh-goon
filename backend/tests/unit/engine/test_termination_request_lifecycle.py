from __future__ import annotations

import sys

import pytest

from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandCancelledError,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.runtime.termination import TerminationSignal


def test_timeout_records_termination_request_before_process_settlement() -> None:
    observed = []
    with pytest.raises(CommandTimeoutExpired) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(5)",
            timeout=0.15,
            process_observer=observed.append,
        )
    kinds = [event.kind for event in observed]
    assert (
        kinds.index("TERMINATION_REQUESTED")
        < kinds.index("PROCESS_EXITED")
        < kinds.index("DESCENDANTS_QUIESCENT")
    )
    receipt = raised.value.supervisor.termination_receipt
    assert receipt is not None and receipt.term_requested and receipt.quiescent


def test_cancellation_records_termination_request_before_process_settlement() -> None:
    observed = []
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(CommandCancelledError) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(5)",
            timeout=5,
            cancel_requested=cancelled,
            process_observer=observed.append,
        )
    kinds = [event.kind for event in observed]
    assert (
        kinds.index("TERMINATION_REQUESTED")
        < kinds.index("PROCESS_EXITED")
        < kinds.index("DESCENDANTS_QUIESCENT")
    )
    receipt = raised.value.supervisor.termination_receipt
    assert receipt is not None and receipt.term_requested and receipt.quiescent


def test_authority_loss_binds_termination_to_exact_physical_execution() -> None:
    observed = []

    with pytest.raises(CommandAuthorityTerminatedError) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            "import time; time.sleep(5)",
            timeout=5,
            termination_requested=lambda: TerminationSignal(
                cause="trust_revoke",
                reason="exact_executor_authority_lost",
            ),
            process_observer=observed.append,
        )

    request = raised.value.request
    assert request.cause == "trust_revoke"
    assert request.reason == "exact_executor_authority_lost"
    assert request.generation == 1
    started = next(event for event in observed if event.kind == "PROCESS_STARTED")
    assert request.execution_id == started.physical_execution_id
    kinds = [event.kind for event in observed]
    assert kinds.index("TERMINATION_REQUESTED") < kinds.index("PROCESS_EXITED")
    receipt = raised.value.supervisor.termination_receipt
    assert receipt is not None and receipt.term_requested and receipt.quiescent
