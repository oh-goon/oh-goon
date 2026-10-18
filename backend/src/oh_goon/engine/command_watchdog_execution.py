from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from threading import Thread

from oh_goon.core.models.runtime_supervision import WatchdogDecision
from oh_goon.engine.command_process_tree import _finish_process_output, _process_group_exists, terminate_owned_process_tree
from oh_goon.engine.command_supervision import CommandRuntimeSupervision, CommandWatchdog
from oh_goon.runtime.termination import ProcessTreeTerminationReceipt, termination_request_from_watchdog


@dataclass(frozen=True, slots=True)
class WatchdogTermination:
    decision: WatchdogDecision
    receipt: ProcessTreeTerminationReceipt
    forced_termination: bool
    final_quiescent: bool


def evaluate_owned_process_watchdog(
    watchdog: CommandWatchdog,
    supervision: CommandRuntimeSupervision,
    process: subprocess.Popen[str],
    readers: tuple[Thread, Thread],
    *,
    termination_requested: Callable[[str], None],
) -> WatchdogTermination | None:
    """Evaluate policy and, when required, terminate the owned tree after intent is recorded."""
    decision = watchdog.evaluate(process_alive=True)
    request = termination_request_from_watchdog(
        decision,
        execution_id=watchdog.execution_id,
        generation=supervision.generation,
        grace_seconds=supervision.policy.termination_grace_seconds,
    )
    if request is None:
        return None
    termination_requested(request.reason)
    receipt = terminate_owned_process_tree(process, request)
    forced = _finish_process_output(process, readers) or receipt.escalated_to_kill
    return WatchdogTermination(
        decision=decision,
        receipt=receipt,
        forced_termination=forced,
        final_quiescent=receipt.quiescent and (os.name == "nt" or not _process_group_exists(process.pid)),
    )
