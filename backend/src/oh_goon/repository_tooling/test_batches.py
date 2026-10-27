"""Run pytest surfaces as deterministic bounded batches with live heartbeats.

The runner is the repository-owned timeout firewall for known-large pytest surfaces.
It can shard by test file or by native collected test node and executes bounded batches in
parallel.  A caller therefore never needs to first try one monolithic pytest command
and discover an agent/tool timeout after wasting the full execution window.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.repository_tooling.pytest_batch_coverage import BatchCoverageSession

from oh_goon.engine.command import run_command
from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args
from oh_goon.repository_tooling.pytest_batch_execution_policy import (
    admitted_execution_timeout,
)
from oh_goon.repository_tooling.pytest_batch_orchestration import (
    Batch,
    BatchCallbacks,
    RefinementHistory,
    run_batch_cli,
)
from oh_goon.repository_tooling.pytest_execution_membership import (
    PytestExecutionMembershipContext,
    PytestExecutionMembershipSession,
)
from oh_goon.repository_tooling.pytest_process_environment import (
    PytestInvocation,
    resolve_pytest_invocation,
)
from oh_goon.repository_tooling.test_batch_receipts import (
    batch_identity as receipt_batch_identity,
)
from oh_goon.runtime.recovery_evidence import ExecutionRecoveryEvent, recovery_work_identity


def _run_command(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[int, str, float]:
    started = time.monotonic()
    try:
        result = run_command(
            *command,
            timeout=float(timeout),
            cwd=cwd if cwd is not None else Path.cwd(),
            env=env,
            merge_stderr=True,
            require_process_group_quiescence=True,
            parent_death_signal=True,
        )
        return result.returncode, result.stdout, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        return 124, str(output) + f"\nBATCH TIMEOUT after {timeout}s\n", time.monotonic() - started


def _progress_fd() -> int | None:
    raw = os.environ.get("OH_GOON_PROGRESS_FD")
    if raw:
        try:
            fd = int(raw)
            if os.isatty(fd):
                return fd
        except (ValueError, OSError):
            pass
    try:
        return sys.stdout.fileno() if sys.stdout.isatty() else None
    except (AttributeError, OSError):
        return None


def _progress_refresh(message: str) -> None:
    fd = _progress_fd()
    if fd is None:
        return
    payload = f"\r\x1b[2K{message}".encode("utf-8", errors="replace")
    try:
        os.write(fd, payload)
    except OSError:
        pass


def _progress_clear() -> None:
    fd = _progress_fd()
    if fd is None:
        return
    try:
        os.write(fd, b"\r\x1b[2K")
    except OSError:
        pass


_PYTEST_PROGRESS_ONLY = re.compile(r"^[.sFxXEPr\s\[\]0-9%]+$")


def _bounded_failure_output(output: str, *, max_lines: int = 120) -> str:
    lines = [
        line
        for line in output.rstrip().splitlines()
        if not (line.strip() and _PYTEST_PROGRESS_ONLY.fullmatch(line.strip()))
    ]
    if len(lines) <= max_lines:
        return "\n".join(lines) + ("\n" if lines else "")
    head = max_lines // 3
    tail = max_lines - head
    omitted = len(lines) - head - tail
    visible = [*lines[:head], f"... {omitted} diagnostic lines omitted ...", *lines[-tail:]]
    return "\n".join(visible) + "\n"


_RESOURCE_PRESSURE_PATTERNS = (
    "BlockingIOError: [Errno 11] Resource temporarily unavailable",
    "RuntimeError: can't start new thread",
    "can't start new thread",
)

_SUITE_BUDGET_STOP_PREFIXES = (
    "BATCH RECOVERY STOPPED: suite deadline exhausted",
    "BATCH RECOVERY STOPPED: insufficient suite budget for full refinement proof",
)


def _resource_pressure_failure(returncode: int, output: str) -> bool:
    """Classify host process/thread exhaustion separately from product assertions."""

    if returncode in (0, 5, 124):
        return False
    return any(pattern in output for pattern in _RESOURCE_PRESSURE_PATTERNS)


def _suite_budget_stop(returncode: int, output: str) -> bool:
    if returncode != 124:
        return False
    return any(prefix in output for prefix in _SUITE_BUDGET_STOP_PREFIXES)


def _run_members(
    members: tuple[str, ...],
    *,
    timeout: int,
    marker: str | None,
    receipt_directory: Path | None = None,
    receipt_suite_identity: str | None = None,
    receipt_execution_plan_identity: str | None = None,
    receipt_run_identity: str | None = None,
    receipt_attempt_identity: str | None = None,
    invocation: PytestInvocation | None = None,
    coverage_session: BatchCoverageSession | None = None,
) -> tuple[int, str, float]:
    explicit_invocation = invocation is not None
    invocation = invocation or resolve_pytest_invocation()
    command = [*invocation.command_prefix, "-q", "--tb=short", "-c", str(invocation.config_file)]
    env = dict(invocation.environment)
    if coverage_session is not None:
        env = coverage_session.environment_for_attempt(
            env, receipt_attempt_identity or receipt_batch_identity(members)
        )
    if receipt_directory is not None and receipt_suite_identity:
        physical_batch_identity = receipt_batch_identity(members)
        command += ["-p", "oh_goon.repository_tooling.pytest_batch_receipts"]
        collection_path = receipt_directory / "collection.json"
        if collection_path.is_file():
            env["OH_GOON_PYTEST_RECEIPT_COLLECTION"] = str(collection_path)
        env.update(
            {
                "OH_GOON_PYTEST_RECEIPT_DIRECTORY": str(receipt_directory),
                "OH_GOON_PYTEST_RECEIPT_SUITE_IDENTITY": receipt_suite_identity,
                "OH_GOON_PYTEST_RECEIPT_BATCH_IDENTITY": physical_batch_identity,
                "OH_GOON_PYTEST_RECEIPT_EXECUTION_PLAN_IDENTITY": receipt_execution_plan_identity
                or "",
                "OH_GOON_PYTEST_RECEIPT_RUN_IDENTITY": receipt_run_identity or "",
                "OH_GOON_PYTEST_RECEIPT_ATTEMPT_IDENTITY": receipt_attempt_identity or "",
                "OH_GOON_PYTEST_RECEIPT_MEMBERS": json.dumps(list(members), separators=(",", ":")),
                "OH_GOON_PYTEST_RECEIPT_ROOT": str(invocation.cwd),
            }
        )
    command += invocation.pytest_args
    if marker:
        command += ["-m", marker]
    command += list(members)
    if explicit_invocation:
        return _run_command(command, timeout=timeout, env=env, cwd=invocation.cwd)
    return _run_command(command, timeout=timeout, env=env)


def _run(
    batch: Batch,
    *,
    timeout: int,
    marker: str | None,
    deadline: float | None = None,
    timeout_bisection: bool = True,
    refinement_history: RefinementHistory | None = None,
    receipt_directory: Path | None = None,
    receipt_suite_identity: str | None = None,
    receipt_execution_plan_identity: str | None = None,
    receipt_run_identity: str | None = None,
    root_attempt: int = 1,
    invocation: PytestInvocation | None = None,
    coverage_session: BatchCoverageSession | None = None,
) -> tuple[int, str, float, tuple[ExecutionRecoveryEvent, ...]]:
    started = time.monotonic()
    recovery_events: list[ExecutionRecoveryEvent] = []
    membership_session = PytestExecutionMembershipSession(
        PytestExecutionMembershipContext(
            receipt_directory,
            receipt_suite_identity,
            receipt_execution_plan_identity,
            receipt_run_identity,
            recovery_work_identity(batch.members),
            root_attempt,
        )
    )

    def run_group(members: tuple[str, ...], *, is_refinement: bool) -> tuple[int, str]:
        remembered = refinement_history.get(members) if refinement_history is not None else None
        if timeout_bisection and remembered is not None and len(members) > 1:
            parent_identity = recovery_work_identity(members)
            child_identities = tuple(recovery_work_identity(child) for child in remembered)
            recovery_events.append(
                ExecutionRecoveryEvent(
                    kind="timeout_refinement",
                    action="reuse_bisection",
                    reason_code="prior_coarse_shard_timeout",
                    outcome="succeeded",
                    work_identity=parent_identity,
                    parent_identity=parent_identity,
                    child_identities=child_identities,
                )
            )
            outputs = [
                f"BATCH TIMEOUT RECOVERY: reusing prior refinement for {len(members)} members "
                f"into {len(remembered[0])}+{len(remembered[1])}\n"
            ]
            child_codes: list[int] = []
            for child in remembered:
                child_rc, child_output = run_group(child, is_refinement=True)
                child_codes.append(child_rc)
                outputs.append(child_output)
                if child_rc not in (0, 5):
                    outputs.append(
                        "BATCH RECOVERY STOPPED: decisive refined child failure; "
                        "remaining sibling work deferred to the next run\n"
                    )
                    return child_rc, "".join(outputs)
            if all(code == 0 for code in child_codes):
                return 0, "".join(outputs)
            if all(code == 5 for code in child_codes):
                return 5, "".join(outputs)
            return next((code for code in child_codes if code not in (0, 5)), 1), "".join(outputs)

        prepared = membership_session.prepare(
            members,
            allow_current_run_reuse=(
                coverage_session is None and (is_refinement or root_attempt > 1)
            ),
        )
        if not prepared.members:
            return 0, prepared.reused_prefix
        members = prepared.members
        effective_timeout, budget_stop = admitted_execution_timeout(
            leaf_timeout=timeout,
            member_count=len(members),
            is_refinement=is_refinement,
            timeout_bisection=timeout_bisection,
            deadline=deadline,
        )
        if budget_stop is not None:
            return 124, prepared.reused_prefix + budget_stop
        if invocation is None and coverage_session is None:
            rc, output, _elapsed = _run_members(
                members, timeout=effective_timeout, marker=marker, **prepared.receipt_kwargs
            )
        else:
            rc, output, _elapsed = _run_members(
                members,
                timeout=effective_timeout,
                marker=marker,
                invocation=invocation,
                coverage_session=coverage_session,
                **prepared.receipt_kwargs,
            )
        output = prepared.reused_prefix + output
        if rc == 124 and coverage_session is not None:
            return rc, output + (
                "\nBATCH COVERAGE RECOVERY INCOMPLETE: a timed-out measured attempt "
                "has no admitted saved coverage; timeout refinement is disabled\n"
            )
        if rc != 124 or not timeout_bisection or len(members) <= 1:
            return rc, output

        midpoint = len(members) // 2
        groups = (members[:midpoint], members[midpoint:])
        if refinement_history is not None:
            refinement_history.remember(members, groups)
        parent_identity = recovery_work_identity(members)
        child_identities = tuple(recovery_work_identity(child) for child in groups)
        recovery_events.append(
            ExecutionRecoveryEvent(
                kind="timeout_refinement",
                action="bisect_shard",
                reason_code="coarse_shard_timeout",
                outcome="succeeded",
                work_identity=parent_identity,
                parent_identity=parent_identity,
                child_identities=child_identities,
            )
        )
        recovered_output = [
            output,
            f"BATCH TIMEOUT RECOVERY: bisecting {len(members)} members "
            f"into {len(groups[0])}+{len(groups[1])}\n",
        ]
        child_codes = []
        for child in groups:
            child_rc, child_output = run_group(child, is_refinement=True)
            child_codes.append(child_rc)
            recovered_output.append(child_output)
            if child_rc not in (0, 5):
                recovered_output.append(
                    "BATCH RECOVERY STOPPED: decisive refined child failure; "
                    "remaining sibling work deferred to the next run\n"
                )
                return child_rc, "".join(recovered_output)
        if all(code == 0 for code in child_codes):
            return 0, "".join(recovered_output)
        if all(code == 5 for code in child_codes):
            return 5, "".join(recovered_output)
        return next((code for code in child_codes if code not in (0, 5)), 1), "".join(
            recovered_output
        )

    rc, output = run_group(batch.members, is_refinement=False)
    return rc, output, time.monotonic() - started, tuple(recovery_events)


def main() -> int:
    parser, args = parse_batch_args()
    callbacks = BatchCallbacks(
        run_batch=_run,
        progress_refresh=_progress_refresh,
        progress_clear=_progress_clear,
        bounded_failure_output=_bounded_failure_output,
        resource_pressure_failure=_resource_pressure_failure,
        suite_budget_stop=_suite_budget_stop,
    )
    return run_batch_cli(parser, args, callbacks)


if __name__ == "__main__":
    raise SystemExit(main())
