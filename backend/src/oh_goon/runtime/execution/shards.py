from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock

from oh_goon.engine.command import (
    CommandCancelledError,
    CommandResult,
    RuntimeAuthorityBinding,
    run_command,
)
from oh_goon.runtime.execution._shard_refinement import refine_children as _refine_children
from oh_goon.runtime.execution._shard_resume import (
    _persist_pass,
    _persist_refinement,
    _resumable_pass,
    _resumable_refinement,
)
from oh_goon.runtime.execution.environment_failure import has_environment_failure_hint
from oh_goon.runtime.execution.shard_models import (
    _LOG_TAIL_LIMIT,
    ExecutionStatus,
    ShardAttemptEvidence,
    ShardExecutionContext,
    ShardExecutionPlan,
    ShardExecutionReport,
    ShardResult,
    ShardRetryPolicy,
    ShardWork,
    _identity,
    build_execution_report,
    execution_policy_identity,
)


def _tail(value: str) -> str:
    return value[-_LOG_TAIL_LIMIT:]


def _retry_admitted(
    *, policy: ShardRetryPolicy, status: ExecutionStatus, returncode: int | None, attempt: int
) -> bool:
    if attempt >= policy.max_attempts:
        return False
    if status == "TIMEOUT":
        return policy.retry_timeouts
    if status == "ENVIRONMENT_BLOCKED":
        return policy.retry_environment_failures
    if status == "FAIL" and returncode is not None:
        return returncode in policy.retryable_exit_codes
    return False


def _attempt_identity(
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    shard: ShardWork,
    attempt: int,
) -> str:
    return _identity(
        {
            "schema": "oh-goon.shard-physical-attempt/v1",
            "execution_context_identity": context.context_identity,
            "generation_identity": plan.generation_identity,
            "plan_identity": plan.plan_identity,
            "described_work_identity": shard.described_work_identity,
            "physical_work_identity": shard.work_identity,
            "attempt": attempt,
        }
    )


def _attempt(
    shard: ShardWork,
    *,
    timeout: float,
    cwd: Path,
    cancel_requested: Callable[[], bool] | None,
    context: ShardExecutionContext,
) -> tuple[ExecutionStatus, CommandResult | None, int, str, str]:
    started = time.monotonic()
    try:
        result = run_command(
            *shard.argv,
            timeout=timeout,
            cwd=cwd,
            cancel_requested=cancel_requested,
            preserve_native_executable_path=False,
            runtime_authority=RuntimeAuthorityBinding(
                runtime_identity=context.runner_toolchain_identity,
                environment_generation_identity=context.environment_identity,
                dependency_supply_identity=context.dependency_identity,
            ),
        )
    except subprocess.TimeoutExpired:
        return "TIMEOUT", None, int((time.monotonic() - started) * 1000), "", ""
    except CommandCancelledError:
        return "CANCELLED", None, int((time.monotonic() - started) * 1000), "", ""
    except (BlockingIOError, OSError, RuntimeError) as exc:
        rendered = f"{type(exc).__name__}: {exc}"
        if has_environment_failure_hint(rendered):
            return (
                "ENVIRONMENT_BLOCKED",
                None,
                int((time.monotonic() - started) * 1000),
                "",
                rendered,
            )
        raise
    rendered = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode == 0:
        status: ExecutionStatus = "PASS"
    elif has_environment_failure_hint(rendered):
        status = "ENVIRONMENT_BLOCKED"
    else:
        status = "FAIL"
    return status, result, result.duration_ms, result.stdout, result.stderr


def _attempt_evidence(
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    shard: ShardWork,
    attempt_number: int,
    status: ExecutionStatus,
    command: CommandResult | None,
    duration_ms: int,
    stdout: str,
    stderr: str,
    retry: bool,
    policy: ShardRetryPolicy,
) -> ShardAttemptEvidence:
    runtime = command.runtime_authority if command is not None else None
    supervisor = command.supervisor if command is not None else None
    return ShardAttemptEvidence(
        attempt_identity=_attempt_identity(
            plan=plan, context=context, shard=shard, attempt=attempt_number
        ),
        physical_work_identity=shard.work_identity,
        attempt=attempt_number,
        status=status,
        returncode=command.returncode if command is not None else None,
        duration_ms=duration_ms,
        stdout_tail=_tail(stdout),
        stderr_tail=_tail(stderr),
        retry_admitted=retry,
        retry_reason=policy.reason if retry else None,
        runtime_authority_receipt_identity=(
            runtime.receipt_identity
            if runtime is not None and runtime.execution_materialization_identity is not None
            else None
        ),
        supervisor_receipt_identity=supervisor.receipt_identity if supervisor is not None else None,
        process_group_quiescent=(
            supervisor.final_process_group_quiescent if supervisor is not None else None
        ),
        forced_termination=supervisor.forced_termination if supervisor is not None else None,
    )


def _shard_result(
    shard: ShardWork,
    status: ExecutionStatus,
    attempts: list[ShardAttemptEvidence],
    *,
    reason_code: str | None = None,
) -> ShardResult:
    return ShardResult(
        shard_id=shard.shard_id,
        described_work_identity=shard.described_work_identity or shard.work_identity,
        work_identity=shard.work_identity,
        status=status,
        attempts=tuple(attempts),
        isolated_process=shard.isolated_process,
        parent_work_identity=shard.parent_work_identity,
        refinement_depth=shard.refinement_depth,
        members=shard.members,
        pytest_members=shard.pytest_members,
        reason_code=reason_code,
    )


def _execute_one(
    shard: ShardWork,
    *,
    plan: ShardExecutionPlan,
    context: ShardExecutionContext,
    timeout: float,
    cwd: Path,
    policy: ShardRetryPolicy,
    cancel_requested: Callable[[], bool] | None,
) -> ShardResult:
    attempts: list[ShardAttemptEvidence] = []
    for attempt_number in range(1, policy.max_attempts + 1):
        status, command, duration_ms, stdout, stderr = _attempt(
            shard, timeout=timeout, cwd=cwd, cancel_requested=cancel_requested, context=context
        )
        retry = _retry_admitted(
            policy=policy,
            status=status,
            returncode=command.returncode if command is not None else None,
            attempt=attempt_number,
        )
        attempts.append(
            _attempt_evidence(
                plan=plan,
                context=context,
                shard=shard,
                attempt_number=attempt_number,
                status=status,
                command=command,
                duration_ms=duration_ms,
                stdout=stdout,
                stderr=stderr,
                retry=retry,
                policy=policy,
            )
        )
        if not retry:
            return _shard_result(shard, status, attempts)
        if _wait_for_retry_delay(policy.delays_seconds[attempt_number - 1], cancel_requested):
            return _shard_result(
                shard,
                "CANCELLED",
                attempts,
                reason_code="cancellation_requested_during_retry_delay",
            )
    raise AssertionError("unreachable retry loop")


def _wait_for_retry_delay(
    delay_seconds: float, cancel_requested: Callable[[], bool] | None
) -> bool:
    deadline = time.monotonic() + delay_seconds
    while True:
        if cancel_requested is not None and cancel_requested():
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.1, remaining))


def _nonresumable_context() -> ShardExecutionContext:
    return ShardExecutionContext(
        runner_toolchain_identity=_identity({"nonresumable": "runner"}),
        dependency_identity=_identity({"nonresumable": "dependencies"}),
        environment_identity=_identity({"nonresumable": "environment"}),
    )


def _cancelled_result(shard: ShardWork, *, reason_code: str) -> ShardResult:
    return ShardResult(
        shard_id=shard.shard_id,
        described_work_identity=shard.described_work_identity or shard.work_identity,
        work_identity=shard.work_identity,
        status="CANCELLED",
        attempts=(),
        isolated_process=shard.isolated_process,
        parent_work_identity=shard.parent_work_identity,
        refinement_depth=shard.refinement_depth,
        members=shard.members,
        pytest_members=shard.pytest_members,
        reason_code=reason_code,
    )


@dataclass
class _ShardTreeExecution:
    """Execute one immutable shard/refinement tree under a shared suite budget."""

    plan: ShardExecutionPlan
    context: ShardExecutionContext
    cwd: Path
    shard_timeout_seconds: float
    policy: ShardRetryPolicy
    execution_policy_identity: str
    state_root: Path | None
    resume: bool
    cancel_requested: Callable[[], bool] | None
    timeout_refinement: bool
    minimum_execution_window_seconds: float
    deadline: float | None
    state_lock: Lock
    stop: Event

    def remaining_budget(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def deadline_reserved_result(self, shard: ShardWork) -> ShardResult:
        return ShardResult(
            shard_id=shard.shard_id,
            described_work_identity=shard.described_work_identity or shard.work_identity,
            work_identity=shard.work_identity,
            status="DEADLINE_RESERVED",
            attempts=(),
            isolated_process=shard.isolated_process,
            parent_work_identity=shard.parent_work_identity,
            refinement_depth=shard.refinement_depth,
            members=shard.members,
            pytest_members=shard.pytest_members,
            reason_code="minimum_execution_window_unavailable",
        )

    def _resumed_refinement(self, shard: ShardWork) -> ShardResult | None:
        if not self.resume or self.state_root is None:
            return None
        durable_children = _resumable_refinement(
            self.state_root,
            plan=self.plan,
            context=self.context,
            execution_policy_identity=self.execution_policy_identity,
            parent=shard,
        )
        if durable_children is None:
            return None
        children = tuple(self.execute(child) for child in durable_children)
        passed = all(child.passed for child in children)
        return ShardResult(
            shard_id=shard.shard_id,
            described_work_identity=shard.described_work_identity or shard.work_identity,
            work_identity=shard.work_identity,
            status="REFINED_PASS" if passed else "REFINED_FAIL",
            attempts=(),
            resumed=all(child.resumed or child.passed for child in children),
            isolated_process=shard.isolated_process,
            parent_work_identity=shard.parent_work_identity,
            refinement_depth=shard.refinement_depth,
            members=shard.members,
            pytest_members=shard.pytest_members,
            reason_code="resumed_durable_timeout_refinement",
            children=children,
        )

    def _resumed_pass(self, shard: ShardWork) -> ShardResult | None:
        if not self.resume or self.state_root is None:
            return None
        if not _resumable_pass(
            self.state_root,
            plan=self.plan,
            context=self.context,
            execution_policy_identity=self.execution_policy_identity,
            shard=shard,
        ):
            return None
        return ShardResult(
            shard_id=shard.shard_id,
            described_work_identity=shard.described_work_identity or shard.work_identity,
            work_identity=shard.work_identity,
            status="RESUMED_PASS",
            attempts=(),
            resumed=True,
            isolated_process=shard.isolated_process,
            parent_work_identity=shard.parent_work_identity,
            refinement_depth=shard.refinement_depth,
            members=shard.members,
            pytest_members=shard.pytest_members,
            reason_code="exact_pass_evidence_reused",
        )

    def _refined_timeout(self, shard: ShardWork, result: ShardResult) -> ShardResult:
        children = _refine_children(shard)
        if self.state_root is not None:
            with self.state_lock:
                _persist_refinement(
                    self.state_root,
                    plan=self.plan,
                    context=self.context,
                    execution_policy_identity=self.execution_policy_identity,
                    parent=shard,
                    children=children,
                )
        child_results: list[ShardResult] = []
        for child in children:
            remaining = self.remaining_budget()
            if remaining is not None and remaining < self.minimum_execution_window_seconds:
                child_results.append(self.deadline_reserved_result(child))
                self.stop.set()
                continue
            child_results.append(self.execute(child))
            if child_results[-1].status in {
                "ENVIRONMENT_BLOCKED",
                "CANCELLED",
                "DEADLINE_RESERVED",
                "SUITE_TIMEOUT",
            }:
                self.stop.set()
        passed = all(child.passed for child in child_results)
        return ShardResult(
            shard_id=shard.shard_id,
            described_work_identity=shard.described_work_identity or shard.work_identity,
            work_identity=shard.work_identity,
            status="REFINED_PASS" if passed else "REFINED_FAIL",
            attempts=result.attempts,
            isolated_process=shard.isolated_process,
            parent_work_identity=shard.parent_work_identity,
            refinement_depth=shard.refinement_depth,
            members=shard.members,
            pytest_members=shard.pytest_members,
            reason_code="timeout_refinement",
            children=tuple(child_results),
        )

    def execute(self, shard: ShardWork) -> ShardResult:
        if self.cancel_requested is not None and self.cancel_requested():
            self.stop.set()
            return _cancelled_result(shard, reason_code="cancellation_requested")
        resumed = self._resumed_refinement(shard) or self._resumed_pass(shard)
        if resumed is not None:
            return resumed
        remaining = self.remaining_budget()
        if remaining is not None and remaining < self.minimum_execution_window_seconds:
            self.stop.set()
            return self.deadline_reserved_result(shard)
        timeout = (
            self.shard_timeout_seconds
            if remaining is None
            else min(self.shard_timeout_seconds, remaining)
        )
        suite_limited = remaining is not None and timeout < self.shard_timeout_seconds
        result = _execute_one(
            shard,
            plan=self.plan,
            context=self.context,
            timeout=timeout,
            cwd=self.cwd,
            policy=self.policy,
            cancel_requested=self.cancel_requested,
        )
        if (
            result.status == "PASS"
            and self.state_root is not None
            and result.attempts
            and result.attempts[-1].runtime_authority_receipt_identity is not None
        ):
            with self.state_lock:
                _persist_pass(
                    self.state_root,
                    plan=self.plan,
                    context=self.context,
                    execution_policy_identity=self.execution_policy_identity,
                    shard=shard,
                )
            return result
        if result.status == "TIMEOUT" and self.timeout_refinement and len(shard.members) > 1:
            return self._refined_timeout(shard, result)
        if result.status == "TIMEOUT" and suite_limited:
            result = ShardResult(
                shard_id=result.shard_id,
                described_work_identity=result.described_work_identity,
                work_identity=result.work_identity,
                status="SUITE_TIMEOUT",
                attempts=result.attempts,
                isolated_process=result.isolated_process,
                parent_work_identity=result.parent_work_identity,
                refinement_depth=result.refinement_depth,
                members=result.members,
                pytest_members=result.pytest_members,
                reason_code="suite_deadline_limited_attempt",
            )
        if result.status in {"ENVIRONMENT_BLOCKED", "CANCELLED", "SUITE_TIMEOUT"}:
            self.stop.set()
        return result


def _validate_execution_request(
    *,
    workers: int,
    shard_timeout_seconds: float,
    suite_timeout_seconds: float | None,
    minimum_execution_window_seconds: float,
    resume: bool,
    state_root: Path | None,
    execution_context: ShardExecutionContext | None,
) -> None:
    if workers < 1:
        raise ValueError("workers must be >= 1")
    if shard_timeout_seconds <= 0:
        raise ValueError("shard_timeout_seconds must be positive")
    if suite_timeout_seconds is not None and suite_timeout_seconds <= 0:
        raise ValueError("suite_timeout_seconds must be positive")
    if minimum_execution_window_seconds <= 0:
        raise ValueError("minimum_execution_window_seconds must be positive")
    if minimum_execution_window_seconds > shard_timeout_seconds:
        raise ValueError("minimum execution window cannot exceed per-shard timeout")
    if resume and state_root is not None and execution_context is None:
        raise ValueError("durable shard resume requires an exact execution_context")


def _policy_identity_for_run(
    workers: int,
    shard_timeout_seconds: float,
    suite_timeout_seconds: float | None,
    minimum_execution_window_seconds: float,
    policy: ShardRetryPolicy,
    timeout_refinement: bool,
    resume: bool,
) -> str:
    return execution_policy_identity(
        workers=workers,
        shard_timeout_seconds=shard_timeout_seconds,
        suite_timeout_seconds=suite_timeout_seconds,
        minimum_execution_window_seconds=minimum_execution_window_seconds,
        retry_policy=policy,
        timeout_refinement=timeout_refinement,
        resume=resume,
    )


def execute_shard_plan(
    plan: ShardExecutionPlan,
    *,
    cwd: Path,
    workers: int,
    shard_timeout_seconds: float,
    suite_timeout_seconds: float | None = None,
    retry_policy: ShardRetryPolicy | None = None,
    state_root: Path | None = None,
    resume: bool = True,
    cancel_requested: Callable[[], bool] | None = None,
    execution_context: ShardExecutionContext | None = None,
    timeout_refinement: bool = True,
    minimum_execution_window_seconds: float = 0.05,
) -> ShardExecutionReport:
    """Execute immutable described shards under Oh-Goon execution authority."""
    _validate_execution_request(
        workers=workers,
        shard_timeout_seconds=shard_timeout_seconds,
        suite_timeout_seconds=suite_timeout_seconds,
        minimum_execution_window_seconds=minimum_execution_window_seconds,
        resume=resume,
        state_root=state_root,
        execution_context=execution_context,
    )
    context = execution_context or _nonresumable_context()
    policy = retry_policy or ShardRetryPolicy()
    policy_identity = _policy_identity_for_run(
        workers,
        shard_timeout_seconds,
        suite_timeout_seconds,
        minimum_execution_window_seconds,
        policy,
        timeout_refinement,
        resume,
    )
    started = time.monotonic()
    deadline = started + suite_timeout_seconds if suite_timeout_seconds is not None else None
    tree = _ShardTreeExecution(
        plan=plan,
        context=context,
        cwd=cwd,
        shard_timeout_seconds=shard_timeout_seconds,
        policy=policy,
        execution_policy_identity=policy_identity,
        state_root=state_root,
        resume=resume,
        cancel_requested=cancel_requested,
        timeout_refinement=timeout_refinement,
        minimum_execution_window_seconds=minimum_execution_window_seconds,
        deadline=deadline,
        state_lock=Lock(),
        stop=Event(),
    )
    results: dict[str, ShardResult] = {}
    pending_roots = list(plan.shards)
    pending: dict[Future[ShardResult], ShardWork] = {}
    max_workers = min(workers, max(1, len(pending_roots)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:

        def submit_next() -> bool:
            if tree.stop.is_set() or not pending_roots:
                return False
            remaining = tree.remaining_budget()
            if remaining is not None and remaining < minimum_execution_window_seconds:
                tree.stop.set()
                return False
            shard = pending_roots.pop(0)
            pending[pool.submit(tree.execute, shard)] = shard
            return True

        for _ in range(max_workers):
            submit_next()
        while pending:
            done, _ = wait(tuple(pending), timeout=0.1, return_when=FIRST_COMPLETED)
            if not done:
                remaining = tree.remaining_budget()
                if remaining is not None and remaining < minimum_execution_window_seconds:
                    tree.stop.set()
                continue
            for future in done:
                shard = pending.pop(future)
                results[shard.shard_id] = future.result()
                if not tree.stop.is_set():
                    submit_next()
    for shard in pending_roots:
        remaining = tree.remaining_budget()
        if remaining is not None and remaining < minimum_execution_window_seconds:
            results[shard.shard_id] = tree.deadline_reserved_result(shard)
        else:
            results[shard.shard_id] = _cancelled_result(
                shard, reason_code="scheduling_stopped_after_terminal_failure"
            )
    ordered = tuple(results[shard.shard_id] for shard in plan.shards)
    return build_execution_report(
        plan=plan,
        context=context,
        workers=workers,
        shard_timeout_seconds=shard_timeout_seconds,
        suite_timeout_seconds=suite_timeout_seconds,
        minimum_execution_window_seconds=minimum_execution_window_seconds,
        execution_policy_identity=policy_identity,
        results=ordered,
        duration_ms=int((time.monotonic() - started) * 1000),
    )
