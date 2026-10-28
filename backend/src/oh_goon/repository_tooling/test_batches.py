"""Run pytest surfaces as deterministic bounded batches with live heartbeats.

The runner is the repository-owned timeout firewall for known-large pytest surfaces.
It can shard by test file or by native collected test node and executes bounded batches in
parallel.  A caller therefore never needs to first try one monolithic pytest command
and discover an agent/tool timeout after wasting the full execution window.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oh_goon.repository_tooling.pytest_batch_coverage import BatchCoverageSession

from oh_goon.core.models.fleet_plan_evidence import PlanOutputEvidence
from oh_goon.engine.command import CommandTimeoutExpired, run_command
from oh_goon.engine.command_capture import TextCapture
from oh_goon.repository_tooling.pytest_batch_cli import parse_batch_args
from oh_goon.repository_tooling.pytest_batch_execution_policy import (
    admitted_execution_timeout,
)
from oh_goon.repository_tooling.pytest_batch_orchestration import (
    Batch,
    BatchCallbacks,
    BatchOutputCapture,
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

_CAPTURE_LIMIT_BYTES = 1024 * 1024


def _bounded_diagnostic_join(parts: list[str]) -> str:
    capture = TextCapture(_CAPTURE_LIMIT_BYTES, Event(), retain_tail=True)
    for part in parts:
        capture.append(part, stream="stdout")
    return capture.text


def _captured_output(
    output: str,
    evidence: PlanOutputEvidence | None,
    *,
    truncated: bool,
    complete: bool,
    members: tuple[str, ...],
    captures: list[BatchOutputCapture] | None,
) -> str:
    if evidence is None:
        return output
    capture: BatchOutputCapture = {
        "members": list(members),
        "sha256": evidence.sha256,
        "byte_count": evidence.byte_count,
        "line_count": evidence.line_count,
        "retained_utf8_bytes": len(output.encode("utf-8")),
        "limit_bytes": _CAPTURE_LIMIT_BYTES,
        "truncated": truncated,
        "complete": complete,
    }
    if captures is not None:
        captures.append(capture)
    if truncated or not complete:
        return f"PYTEST OUTPUT CAPTURE: {json.dumps(capture, sort_keys=True)}\n" + output
    return output


def _run_command(
    command: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    output_captures: list[BatchOutputCapture] | None = None,
    members: tuple[str, ...] = (),
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
            max_capture_bytes_per_stream=_CAPTURE_LIMIT_BYTES,
            retain_output_tail=True,
        )
        output = _captured_output(
            result.stdout,
            result.stdout_evidence,
            truncated=result.stdout_truncated,
            complete=True,
            members=members,
            captures=output_captures,
        )
        return result.returncode, output, time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        output = exc.output or ""
        text = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else output
        if isinstance(exc, CommandTimeoutExpired):
            text = _captured_output(
                text,
                exc.stdout_evidence,
                truncated=exc.stdout_truncated,
                complete=False,
                members=members,
                captures=output_captures,
            )
        return 124, text + f"\nBATCH TIMEOUT after {timeout}s\n", time.monotonic() - started


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
    head = max_lines // 3
    tail = max_lines - head
    first: list[str] = []
    last: deque[str] = deque(maxlen=tail)
    count = 0
    for raw_line in io.StringIO(output):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped and _PYTEST_PROGRESS_ONLY.fullmatch(stripped):
            continue
        if len(line) > 4096:
            line = line[:2048] + " ... diagnostic line truncated ... " + line[-2048:]
        count += 1
        if len(first) < head:
            first.append(line)
        else:
            last.append(line)
    if count <= max_lines:
        visible = [*first, *last]
    else:
        visible = [*first, f"... {count - max_lines} diagnostic lines omitted ...", *last]
    return "\n".join(visible) + ("\n" if visible else "")


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
    output_captures: list[BatchOutputCapture] | None = None,
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
    if output_captures is not None:
        return _run_command(
            command,
            timeout=timeout,
            env=env,
            cwd=invocation.cwd,
            output_captures=output_captures,
            members=members,
        )
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
    output_captures: list[BatchOutputCapture] | None = None,
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
                    return child_rc, _bounded_diagnostic_join(outputs)
            if all(code == 0 for code in child_codes):
                return 0, _bounded_diagnostic_join(outputs)
            if all(code == 5 for code in child_codes):
                return 5, _bounded_diagnostic_join(outputs)
            return next(
                (code for code in child_codes if code not in (0, 5)), 1
            ), _bounded_diagnostic_join(outputs)

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
        if output_captures is not None:
            rc, output, _elapsed = _run_members(
                members,
                timeout=effective_timeout,
                marker=marker,
                invocation=invocation,
                coverage_session=coverage_session,
                output_captures=output_captures,
                **prepared.receipt_kwargs,
            )
        elif invocation is None and coverage_session is None:
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
                return child_rc, _bounded_diagnostic_join(recovered_output)
        if all(code == 0 for code in child_codes):
            return 0, _bounded_diagnostic_join(recovered_output)
        if all(code == 5 for code in child_codes):
            return 5, _bounded_diagnostic_join(recovered_output)
        return next(
            (code for code in child_codes if code not in (0, 5)), 1
        ), _bounded_diagnostic_join(recovered_output)

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
    if not args.focused:
        return run_batch_cli(parser, args, callbacks)
    print("=== FOCUSED PYTEST FEEDBACK: NOT_FULLY_VERIFIED ===", flush=True)
    print(f"Selected paths: {json.dumps([str(path) for path in args.paths])}", flush=True)
    try:
        return run_batch_cli(parser, args, callbacks)
    finally:
        print("Full verification: NOT_FULLY_VERIFIED", flush=True)
        print(f"Next check: UNVERIFIED | {json.dumps(args.next_check)}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
