from __future__ import annotations

import os
import subprocess
import sys

import pytest

from oh_goon.core.models.runtime_supervision import WatchdogDecision, WatchdogDisposition
from oh_goon.engine.command import terminate_owned_process_tree
from oh_goon.runtime.termination import TerminationRequest, termination_request_from_watchdog


def test_observational_watchdog_decision_does_not_create_termination_request():
    decision = WatchdogDecision(
        disposition=WatchdogDisposition.SLOW,
        policy_revision="v1",
        elapsed_seconds=5,
        progress_idle_seconds=5,
        termination_required=False,
        reason="slow_threshold_exceeded",
    )
    assert termination_request_from_watchdog(decision, execution_id="exec-1", generation=2) is None


def test_watchdog_termination_request_binds_execution_generation_and_reason():
    decision = WatchdogDecision(
        disposition=WatchdogDisposition.HARD_TIMEOUT,
        policy_revision="v1",
        elapsed_seconds=60,
        progress_idle_seconds=60,
        termination_required=True,
        reason="max_runtime_exceeded",
    )
    request = termination_request_from_watchdog(
        decision, execution_id="exec-1", generation=2, grace_seconds=0.25
    )
    assert request is not None
    assert (request.execution_id, request.generation, request.cause, request.reason) == (
        "exec-1",
        2,
        "watchdog",
        "max_runtime_exceeded",
    )
    assert request.request_identity.startswith("sha256:")


def test_termination_request_rejects_invalid_generation_and_unbounded_grace():
    with pytest.raises(ValueError, match="generation"):
        TerminationRequest(execution_id="exec", generation=0, cause="cancel", reason="cancelled")
    with pytest.raises(ValueError, match="grace"):
        TerminationRequest(
            execution_id="exec", generation=1, cause="cancel", reason="cancelled", grace_seconds=61
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-tree escalation proof")
def test_stubborn_process_tree_escalates_and_returns_quiescence_receipt():
    process = subprocess.Popen(
        [
            sys.executable,
            "-S",
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('READY', flush=True); time.sleep(30)",
        ],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdout.readline().strip() == "READY"
    request = TerminationRequest(
        execution_id="exec-stubborn",
        generation=1,
        cause="watchdog",
        reason="max_runtime_exceeded",
        grace_seconds=0.05,
    )
    receipt = terminate_owned_process_tree(process, request)
    assert receipt.request_identity == request.request_identity
    assert receipt.term_requested is True
    assert receipt.escalated_to_kill is True
    assert receipt.quiescent is True
    assert receipt.final_state == "quiescent"
    assert receipt.receipt_identity.startswith("sha256:")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-tree grace proof")
def test_cooperative_process_tree_stops_during_grace_without_kill():
    process = subprocess.Popen(
        [sys.executable, "-S", "-c", "import time; time.sleep(30)"],
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    request = TerminationRequest(
        execution_id="exec-cooperative",
        generation=1,
        cause="cancel",
        reason="cancelled",
        grace_seconds=1,
    )
    receipt = terminate_owned_process_tree(process, request)
    assert receipt.escalated_to_kill is False
    assert receipt.quiescent is True
