from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.engine.command import (
    CommandAuthorityTerminatedError,
    CommandCancelledError,
    CommandProcessLifecycleEvent,
    CommandTimeoutExpired,
    run_command,
)
from oh_goon.runtime.termination import TerminationRequest, TerminationSignal


def test_native_signal_intent_preserves_existing_term_identity_and_distinguishes_sigint() -> None:
    payload = {
        "schema": "oh-goon.termination-request/v1",
        "execution_id": "physical-execution",
        "generation": 1,
        "cause": "cancel",
        "reason": "cancellation_requested",
        "grace_seconds": 1.0,
    }
    existing_identity = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    request = TerminationRequest(
        execution_id="physical-execution",
        generation=1,
        cause="cancel",
        reason="cancellation_requested",
    )

    assert request.request_identity == existing_identity
    assert replace(request, signal_number=signal.SIGTERM).request_identity == existing_identity
    assert replace(request, signal_number=signal.SIGINT).request_identity != existing_identity
    with pytest.raises(ValueError, match="SIGTERM or SIGINT"):
        replace(request, signal_number=9)


@pytest.mark.process
@pytest.mark.skipif(os.name == "nt", reason="POSIX native cancellation signals")
@pytest.mark.parametrize(
    "requested_signal,resistant",
    [(None, False), (signal.SIGTERM, False), (signal.SIGINT, False), (signal.SIGINT, True)],
    ids=["default-term", "explicit-term", "interrupt", "resistant-interrupt"],
)
def test_native_cancellation_delivers_requested_signal_before_bounded_escalation(
    tmp_path: Path, requested_signal: int | None, resistant: bool
) -> None:
    ready = tmp_path / "ready"
    observed_signal = tmp_path / "signal"
    program = """
import os, pathlib, signal, sys
def receive(number, frame):
    pathlib.Path(sys.argv[2]).write_text(str(number))
    if sys.argv[3] == 'cooperative':
        raise SystemExit(0)
signal.signal(signal.SIGINT, receive)
signal.signal(signal.SIGTERM, receive)
pathlib.Path(sys.argv[1]).write_text(str(os.getpid()))
while True:
    signal.pause()
"""
    events: list[CommandProcessLifecycleEvent] = []
    started = time.monotonic()
    with pytest.raises(CommandCancelledError) as raised:
        run_command(
            sys.executable,
            "-S",
            "-c",
            program,
            str(ready),
            str(observed_signal),
            "resistant" if resistant else "cooperative",
            timeout=5,
            cancel_requested=ready.exists if requested_signal is None else None,
            cancellation_signal=(
                (lambda: requested_signal if ready.exists() else None)
                if requested_signal is not None
                else None
            ),
            parent_death_signal=True,
            require_process_group_quiescence=True,
            process_observer=events.append,
        )

    expected_signal = signal.SIGTERM if requested_signal is None else requested_signal
    assert observed_signal.read_text() == str(expected_signal)
    assert raised.value.signal_number == expected_signal
    receipt = raised.value.supervisor.termination_receipt
    assert receipt is not None and receipt.quiescent
    assert receipt.escalated_to_kill is resistant
    assert raised.value.supervisor.final_process_group_quiescent
    birth = next(event for event in events if event.kind == "PROCESS_STARTED")
    request = TerminationRequest(
        execution_id=birth.physical_execution_id,
        generation=1,
        cause="cancel",
        reason="cancellation_requested",
        signal_number=expected_signal,
    )
    assert receipt.request_identity == request.request_identity
    assert time.monotonic() - started < 4


def test_timeout_records_termination_request_before_process_settlement() -> None:
    observed: list[CommandProcessLifecycleEvent] = []
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
    observed: list[CommandProcessLifecycleEvent] = []
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
    observed: list[CommandProcessLifecycleEvent] = []

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
