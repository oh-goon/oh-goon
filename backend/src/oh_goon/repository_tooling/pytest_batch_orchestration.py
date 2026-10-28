"""Planning and concurrent orchestration for the repository-owned pytest batch runner.

This module owns admission, resume planning, scheduling, completion accounting,
membership finalization, and execution-report projection. The public CLI and the
single timeout/refinement execution engine remain in ``test_batches``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict

from oh_goon.repository_tooling import pytest_execution_report as execution_report_contract
from oh_goon.repository_tooling.pytest_batch_coverage import BatchCoverageSession, CoverageOptions
from oh_goon.repository_tooling.pytest_execution_membership import (
    ExecutionMembershipReceipt,
    ExecutionMembershipStore,
    execution_run_identity,
)
from oh_goon.repository_tooling.pytest_member_selection import (
    NativePytestCollection,
    collect_native_pytest,
    write_native_collection,
)
from oh_goon.repository_tooling.pytest_process_environment import (
    PytestInvocation,
    resolve_pytest_invocation,
)
from oh_goon.repository_tooling.pytest_resource_budget import (
    PytestResourceBudget,
    PytestUsageSnapshot,
    pytest_cgroup_diagnostics,
    pytest_usage_diagnostics,
    read_pytest_cgroup_usage,
    read_pytest_usage,
    resolve_pytest_resource_budget,
)
from oh_goon.repository_tooling.test_batch_receipts import (
    MemberReceiptStore,
    ResumeContext,
    build_resume_context,
)
from oh_goon.repository_tooling.test_batch_receipts import (
    execution_plan_identity as receipt_execution_plan_identity,
)
from oh_goon.runtime.recovery_evidence import (
    ExecutionRecoveryEvent,
    recovery_work_identity,
)


class MembershipReceiptProjection(Protocol):
    def as_dict(self) -> dict[str, object]: ...


class BatchOutputCapture(TypedDict):
    members: list[str]
    sha256: str
    byte_count: int
    line_count: int
    retained_utf8_bytes: int
    limit_bytes: int
    truncated: bool
    complete: bool


class BatchExecutionReport(TypedDict):
    batch_index: int
    members: list[str]
    attempt_count: int
    returncode: int
    duration_ms: int
    environment_retry_count: int
    timeout_refinements: list[str]
    recovery_events: list[dict[str, object]]
    output_captures: NotRequired[list[BatchOutputCapture]]


@dataclass(frozen=True)
class BatchCallbacks:
    run_batch: Callable[..., tuple[int, str, float, tuple[ExecutionRecoveryEvent, ...]]]
    progress_refresh: Callable[[str], None]
    progress_clear: Callable[[], None]
    bounded_failure_output: Callable[..., str]
    resource_pressure_failure: Callable[[int, str], bool]
    suite_budget_stop: Callable[[int, str], bool]


@dataclass(frozen=True)
class Batch:
    index: int
    total: int
    members: tuple[str, ...]


class RefinementHistory:
    """Run-local memory of shards already proven too coarse.

    History never crosses invocations or repository generations.  It only prevents an
    environment retry in this runner process from paying the same parent probe again.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._children: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def get(self, members: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        identity = recovery_work_identity(members)
        with self._lock:
            return self._children.get(identity)

    def remember(
        self, members: tuple[str, ...], children: tuple[tuple[str, ...], tuple[str, ...]]
    ) -> None:
        identity = recovery_work_identity(members)
        with self._lock:
            self._children[identity] = children


def _chunks(members: tuple[str, ...], size: int) -> tuple[Batch, ...]:
    chunks = [members[i : i + size] for i in range(0, len(members), size)]
    return tuple(Batch(i + 1, len(chunks), tuple(chunk)) for i, chunk in enumerate(chunks))


@dataclass(frozen=True)
class _BatchPlan:
    discovered_members: tuple[str, ...]
    members: tuple[str, ...]
    native_deselected: tuple[str, ...]
    batches: tuple[Batch, ...]
    scheduled_batches: tuple[Batch, ...]
    reusable_members: frozenset[str]
    fully_reused_batches: int
    selected_members: tuple[str, ...]
    workers: int
    resume_context: ResumeContext
    execution_plan_identity: str
    run_identity: str
    execution_membership_store: ExecutionMembershipStore
    started: float
    resource_budget: PytestResourceBudget
    usage_started: PytestUsageSnapshot | None
    native_collection_duration: float
    invocation: PytestInvocation
    coverage_session: BatchCoverageSession | None = None


_BatchFuture = concurrent.futures.Future[tuple[int, str, float, tuple[ExecutionRecoveryEvent, ...]]]


@dataclass
class _BatchRunState:
    unscheduled: list[Batch]
    selected_batches: int
    suite_started: float
    deadline: float | None
    pending: dict[_BatchFuture, Batch]
    attempts: dict[int, int]
    failures: int = 0
    deselected_batches: int = 0
    suite_timed_out: bool = False
    batch_reports: list[BatchExecutionReport] = field(default_factory=list)
    recovery_events: list[ExecutionRecoveryEvent] = field(default_factory=list)
    refinement_history: RefinementHistory = field(default_factory=RefinementHistory)
    output_captures: dict[int, list[BatchOutputCapture]] = field(default_factory=dict)


def _discover_runnable_members(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    invocation: PytestInvocation,
    coverage_session: BatchCoverageSession | None,
) -> NativePytestCollection:
    try:
        collection = collect_native_pytest(
            tuple(args.paths),
            excludes=tuple(args.exclude),
            marker=args.marker,
            timeout=min(args.batch_timeout, args.suite_timeout or args.batch_timeout),
            invocation=invocation,
            environment=coverage_session.environment_for_attempt(
                invocation.environment, "collection"
            )
            if coverage_session
            else None,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not collection.selected:
        if invocation.qualified:
            print("Native pytest selected no tests (exit 5).", file=sys.stderr)
            raise SystemExit(5)
        parser.error("native pytest collection selected no tests")
    return collection


def _select_batches(
    parser: argparse.ArgumentParser, args: argparse.Namespace, members: tuple[str, ...]
) -> tuple[Batch, ...]:
    batches = _chunks(members, args.batch_size)
    if args.batch is None:
        return batches
    if args.batch < 1 or args.batch > len(batches):
        parser.error(f"batch must be between 1 and {len(batches)}")
    selected = batches[args.batch - 1]
    return (Batch(1, 1, selected.members),)


def _resume_schedule(
    args: argparse.Namespace, batches: tuple[Batch, ...], receipt_store: MemberReceiptStore
) -> tuple[frozenset[str], tuple[Batch, ...], int]:
    reusable: set[str] = set()
    scheduled: list[Batch] = []
    fully_reused = 0
    for batch in batches:
        remaining = []
        for member in batch.members:
            if args.resume and receipt_store.reusable(member):
                reusable.add(member)
            else:
                remaining.append(member)
        if remaining:
            scheduled.append(Batch(batch.index, batch.total, tuple(remaining)))
        else:
            fully_reused += 1
    return frozenset(reusable), tuple(scheduled), fully_reused


def _execution_authority(
    args: argparse.Namespace,
    resume_context: ResumeContext,
    scheduled: tuple[Batch, ...],
    reusable: frozenset[str],
    workers: int,
    resource_budget: PytestResourceBudget,
) -> tuple[str, str, ExecutionMembershipStore]:
    plan_identity = receipt_execution_plan_identity(
        suite_identity=resume_context.suite_identity,
        batch_size=args.batch_size,
        batch_timeout=args.batch_timeout,
        timeout_bisection=not args.no_timeout_bisection,
        workers=workers,
        suite_timeout=args.suite_timeout,
        environment_retries=args.environment_retries,
        selected_batch=args.batch,
        scheduled_members=tuple(member for batch in scheduled for member in batch.members),
        reused_members=tuple(sorted(reusable)),
        resource_budget=resource_budget.execution_policy(),
    )
    run_identity = execution_run_identity(
        suite_identity=resume_context.suite_identity,
        execution_plan_identity=plan_identity,
    )
    store = ExecutionMembershipStore(
        resume_context.receipt_directory,
        suite_identity=resume_context.suite_identity,
        execution_plan_identity=plan_identity,
        run_identity=run_identity,
    )
    return plan_identity, run_identity, store


def _build_batch_plan(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    *,
    resource_budget: PytestResourceBudget,
    usage_started: PytestUsageSnapshot | None,
    started: float,
    invocation: PytestInvocation,
    coverage_session: BatchCoverageSession | None = None,
) -> _BatchPlan:
    collection_started = time.monotonic()
    collection = _discover_runnable_members(parser, args, invocation, coverage_session)
    collection_duration = time.monotonic() - collection_started
    members = tuple(collection.groups(args.granularity))
    discovered = tuple(item["selector"] for item in (*collection.selected, *collection.deselected))
    pruned = tuple(item["selector"] for item in collection.deselected)
    collection_identity = hashlib.sha256(
        json.dumps(collection.payload(), sort_keys=True).encode()
    ).hexdigest()
    batches = _select_batches(parser, args, members)
    resume_context = build_resume_context(
        root=invocation.source_root,
        member_root=invocation.cwd,
        members=members,
        resume_key=args.resume_key,
        state_root=args.resume_state_root,
        granularity=args.granularity,
        marker=args.marker,
        extra_context=(
            *args.resume_context,
            "native-pytest-collection:" + collection_identity,
            invocation.correctness_context(),
        ),
    )
    resume_context.ensure_manifest()
    write_native_collection(
        resume_context.receipt_directory / "collection.json", collection.payload()
    )
    receipt_store = MemberReceiptStore(
        resume_context.receipt_directory, suite_identity=resume_context.suite_identity
    )
    reusable, scheduled, fully_reused = _resume_schedule(args, batches, receipt_store)
    workers = min(resource_budget.worker_ceiling, max(1, len(scheduled)))
    plan_identity, run_identity, membership_store = _execution_authority(
        args, resume_context, scheduled, reusable, workers, resource_budget
    )
    selected_members = tuple(member for batch in batches for member in batch.members)
    return _BatchPlan(
        discovered,
        members,
        pruned,
        batches,
        scheduled,
        reusable,
        fully_reused,
        selected_members,
        workers,
        resume_context,
        plan_identity,
        run_identity,
        membership_store,
        started,
        resource_budget,
        usage_started,
        collection_duration,
        invocation,
        coverage_session,
    )


def _print_batch_plan(args: argparse.Namespace, plan: _BatchPlan) -> None:
    print("=== PYTEST BATCH PLAN ===", flush=True)
    print(
        f"Granularity: {args.granularity} | members={len(plan.members)} | "
        f"batches={len(plan.batches)} | native-deselected={len(plan.native_deselected)}/"
        f"{len(plan.discovered_members)} | workers={plan.workers} | "
        f"per-batch-timeout={args.batch_timeout}s | "
        f"suite-timeout={args.suite_timeout if args.suite_timeout is not None else 'none'} | "
        f"environment-retries={args.environment_retries} | "
        f"timeout-bisection={'off' if args.no_timeout_bisection else 'on'} | "
        f"resume={'on' if args.resume else 'off'}",
        flush=True,
    )
    print(
        "=== PYTEST RESOURCE BUDGET === "
        f"requested-workers={plan.resource_budget.requested_workers} | "
        f"configured-cpus={plan.resource_budget.configured_cpus} | "
        f"effective-workers={plan.workers} | "
        f"affinity-cpus={plan.resource_budget.affinity_cpu_count} | "
        f"native-quota-cpus={plan.resource_budget.cgroup_cpu_capacity} | "
        f"memory-estimated-workers={plan.resource_budget.memory_worker_capacity} | "
        "initial worker ceiling; native OS owns process-tree CPU quotas",
        flush=True,
    )
    if args.resume:
        _print_resume_plan(args, plan)
    if plan.native_deselected:
        print(
            f"=== PYTEST NATIVE DESELECTION === members={len(plan.native_deselected)} "
            f"marker={args.marker!r}",
            flush=True,
        )


def _print_resume_plan(args: argparse.Namespace, plan: _BatchPlan) -> None:
    context = plan.resume_context
    print(
        "=== PYTEST RESUME PLAN === "
        f"key={args.resume_key!r} reusable-members={len(plan.reusable_members)}/"
        f"{len(plan.selected_members)} fully-reused-batches={plan.fully_reused_batches}/"
        f"{len(plan.batches)} source={context.source_identity[:19]}... "
        f"plan={context.test_plan_identity[:19]}...",
        flush=True,
    )
    if plan.reusable_members:
        print(
            "=== PYTEST RESUME TIMING === comparable=no "
            "reason=process-local-cache-state-differs-after-resume",
            flush=True,
        )


def _new_run_state(args: argparse.Namespace, plan: _BatchPlan) -> _BatchRunState:
    started = plan.started
    deadline = started + args.suite_timeout if args.suite_timeout is not None else None
    return _BatchRunState(
        unscheduled=list(plan.scheduled_batches),
        selected_batches=plan.fully_reused_batches,
        suite_started=started,
        deadline=deadline,
        pending={},
        attempts={},
    )


def _submit_next(
    pool: concurrent.futures.ThreadPoolExecutor,
    args: argparse.Namespace,
    plan: _BatchPlan,
    state: _BatchRunState,
    callbacks: BatchCallbacks,
) -> bool:
    if not state.unscheduled:
        return False
    if state.deadline is not None and state.deadline - time.monotonic() <= 0:
        state.suite_timed_out = True
        return False
    batch = state.unscheduled.pop(0)
    state.attempts[batch.index] = state.attempts.get(batch.index, 0) + 1
    state.pending[
        pool.submit(
            callbacks.run_batch,
            batch,
            timeout=args.batch_timeout,
            marker=args.marker,
            deadline=state.deadline,
            timeout_bisection=not args.no_timeout_bisection,
            refinement_history=state.refinement_history,
            receipt_directory=plan.resume_context.receipt_directory,
            receipt_suite_identity=plan.resume_context.suite_identity,
            receipt_execution_plan_identity=plan.execution_plan_identity,
            receipt_run_identity=plan.run_identity,
            root_attempt=state.attempts[batch.index],
            invocation=plan.invocation,
            coverage_session=plan.coverage_session,
            output_captures=state.output_captures.setdefault(batch.index, []),
        )
    ] = batch
    return True


def _wait_timeout(state: _BatchRunState) -> float:
    if state.deadline is None:
        return 5.0
    remaining = state.deadline - time.monotonic()
    if remaining <= 0:
        state.suite_timed_out = bool(state.unscheduled) or bool(state.pending)
        return 0.25
    return min(5.0, remaining)


def _record_batch_report(
    state: _BatchRunState,
    batch: Batch,
    attempt: int,
    rc: int,
    output: str,
    elapsed: float,
    events: tuple[ExecutionRecoveryEvent, ...],
) -> None:
    refinements = tuple(
        line for line in output.splitlines() if line.startswith("BATCH TIMEOUT RECOVERY:")
    )
    state.batch_reports.append(
        {
            "batch_index": batch.index,
            "members": list(batch.members),
            "attempt_count": attempt,
            "returncode": rc,
            "duration_ms": int(elapsed * 1000),
            "environment_retry_count": max(0, attempt - 1),
            "timeout_refinements": list(refinements),
            "recovery_events": [event.as_dict() for event in events],
            "output_captures": state.output_captures.pop(batch.index, []),
        }
    )


def _print_batch_failure(
    batch: Batch,
    rc: int,
    output: str,
    elapsed: float,
    callbacks: BatchCallbacks,
    *,
    show_output: bool = False,
) -> None:
    callbacks.progress_clear()
    print(
        f"=== PYTEST BATCH {batch.index}/{batch.total} FAIL === duration={elapsed:.1f}s exit={rc}",
        flush=True,
    )
    if output:
        print(
            output if show_output else callbacks.bounded_failure_output(output), end="", flush=True
        )
    print(f"=== PYTEST BATCH {batch.index}/{batch.total} MEMBERS ===", flush=True)
    for member in batch.members:
        print(f"  {member}", flush=True)


def _retry_resource_pressure(
    args: argparse.Namespace,
    state: _BatchRunState,
    batch: Batch,
    attempt: int,
    rc: int,
    output: str,
    callbacks: BatchCallbacks,
) -> bool:
    if not (
        callbacks.resource_pressure_failure(rc, output) and attempt <= args.environment_retries
    ):
        return False
    callbacks.progress_clear()
    print(
        f"=== PYTEST BATCH {batch.index}/{batch.total} ENVIRONMENT RETRY === "
        f"attempt={attempt}/{args.environment_retries + 1} reason=process-thread-exhaustion",
        flush=True,
    )
    event = ExecutionRecoveryEvent(
        kind="environment_retry",
        action="requeue_unproven_members",
        reason_code="process_thread_exhaustion",
        outcome="requested",
        work_identity=recovery_work_identity(batch.members),
    )
    state.recovery_events.append(event)
    state.unscheduled.append(batch)
    return True


def _consume_future(
    args: argparse.Namespace,
    state: _BatchRunState,
    future: _BatchFuture,
    batch: Batch,
    callbacks: BatchCallbacks,
    *,
    coverage: bool = False,
) -> bool:
    rc, output, elapsed, events = future.result()
    state.recovery_events.extend(events)
    if callbacks.suite_budget_stop(rc, output):
        state.suite_timed_out = True
    attempt = state.attempts.get(batch.index, 1)
    if coverage and callbacks.resource_pressure_failure(rc, output):
        output += (
            "\nBATCH COVERAGE RECOVERY INCOMPLETE: correctness receipts do not "
            "establish saved coverage; environment retry is disabled\n"
        )
    elif _retry_resource_pressure(args, state, batch, attempt, rc, output, callbacks):
        return True
    marker_deselected = rc == 5 and bool(args.marker)
    if marker_deselected:
        state.deselected_batches += 1
        callbacks.progress_clear()
        print(
            f"=== PYTEST BATCH {batch.index}/{batch.total} DESELECTED === "
            f"duration={elapsed:.1f}s marker={args.marker!r}",
            flush=True,
        )
        return False
    state.selected_batches += 1
    _record_batch_report(state, batch, attempt, rc, output, elapsed, events)
    if rc != 0:
        _print_batch_failure(batch, rc, output, elapsed, callbacks, show_output=args.show_output)
    elif args.show_output and output:
        print(
            f"=== PYTEST BATCH {batch.index}/{batch.total} NATIVE OUTPUT (per batch) ===",
            flush=True,
        )
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    state.failures += int(rc != 0)
    return False


def _consume_done_futures(
    args: argparse.Namespace,
    plan: _BatchPlan,
    state: _BatchRunState,
    pool: concurrent.futures.ThreadPoolExecutor,
    done: set[_BatchFuture],
    callbacks: BatchCallbacks,
) -> int:
    completed = 0
    for future in sorted(done, key=lambda item: state.pending[item].index):
        batch = state.pending.pop(future)
        retried = _consume_future(
            args,
            state,
            future,
            batch,
            callbacks,
            coverage=plan.coverage_session is not None,
        )
        completed += int(not retried)
        if not state.suite_timed_out:
            _submit_next(pool, args, plan, state, callbacks)
    return completed


def _run_batch_executor(
    args: argparse.Namespace, plan: _BatchPlan, callbacks: BatchCallbacks
) -> _BatchRunState:
    state = _new_run_state(args, plan)
    with concurrent.futures.ThreadPoolExecutor(max_workers=plan.workers) as pool:
        completed = plan.fully_reused_batches
        for _ in range(plan.workers):
            if not _submit_next(pool, args, plan, state, callbacks):
                break
        while state.pending:
            callbacks.progress_refresh(
                f"Pytest batches · {completed}/{len(plan.batches)} complete · "
                f"{len(state.pending)} active · {len(state.unscheduled)} queued"
            )
            done, _ = concurrent.futures.wait(
                state.pending,
                timeout=_wait_timeout(state),
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            completed += _consume_done_futures(args, plan, state, pool, done, callbacks)
            if (
                state.deadline is not None
                and time.monotonic() >= state.deadline
                and state.unscheduled
            ):
                state.suite_timed_out = True
        callbacks.progress_clear()
    return state


def _apply_terminal_failures(
    args: argparse.Namespace, plan: _BatchPlan, state: _BatchRunState
) -> None:
    if state.selected_batches == 0 and state.deselected_batches > 0:
        state.failures += 1
        print(
            f"=== PYTEST SELECTION ERROR === all {state.deselected_batches} batches were "
            f"deselected by marker={args.marker!r}",
            flush=True,
        )
    if state.suite_timed_out or state.unscheduled:
        state.suite_timed_out = True
        state.failures += 1
        print(
            f"=== PYTEST SUITE TIMEOUT === budget={args.suite_timeout}s "
            f"completed={len(plan.batches) - len(state.unscheduled)}/{len(plan.batches)} "
            f"unstarted={len(state.unscheduled)}",
            flush=True,
        )


def _finalize_membership(
    plan: _BatchPlan, state: _BatchRunState
) -> tuple[ExecutionMembershipReceipt, Path]:
    receipt, path = plan.execution_membership_store.finalize(
        selected=plan.selected_members,
        resumed=tuple(sorted(plan.reusable_members)),
        suite_passed=state.failures == 0,
        recovery_lineage=tuple(event.as_dict() for event in state.recovery_events),
    )
    if state.failures == 0 and not receipt.terminal_success:
        state.failures += 1
        print(
            "=== PYTEST MEMBERSHIP FAILURE === "
            f"missing={len(receipt.missing)} duplicates={len(receipt.duplicates)} "
            f"extras={len(receipt.extras)}",
            flush=True,
        )
        for label, members in (
            ("MISSING", receipt.missing),
            ("DUPLICATE", receipt.duplicates),
            ("EXTRA", receipt.extras),
        ):
            for member in members:
                print(f"  {label}: {member}", flush=True)
    return receipt, path


def _print_final_result(
    plan: _BatchPlan,
    state: _BatchRunState,
    receipt: ExecutionMembershipReceipt,
    receipt_path: Path,
    duration: float,
) -> None:
    print(
        "=== PYTEST MEMBERSHIP RECEIPT === "
        f"selected={len(receipt.selected)} attempted={len(receipt.attempted)} "
        f"completed={len(receipt.completed)} passed={len(receipt.passed)} "
        f"failed={len(receipt.failed)} non-passed={len(receipt.non_passed)} "
        f"resumed={len(receipt.resumed)} missing={len(receipt.missing)} "
        f"duplicates={len(receipt.duplicates)} extras={len(receipt.extras)} "
        f"identity={receipt.selected_membership_identity[:19]}...",
        flush=True,
    )
    print(f"Membership receipt: {receipt_path}", flush=True)
    print(
        f"=== PYTEST BATCH RESULT: {'PASS' if state.failures == 0 else 'FAIL'} === "
        f"batches={len(plan.batches)} failures={state.failures} "
        f"reused-members={len(plan.reusable_members)} duration={duration:.1f}s",
        flush=True,
    )


def _execution_report(
    args: argparse.Namespace,
    plan: _BatchPlan,
    state: _BatchRunState,
    receipt: MembershipReceiptProjection,
    receipt_path: Path,
    duration: float,
    *,
    diagnostics: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": execution_report_contract.execution_report_schema(),
        "status": "PASS" if state.failures == 0 else "FAIL",
        "granularity": args.granularity,
        "batch_timeout_seconds": args.batch_timeout,
        "suite_timeout_seconds": args.suite_timeout,
        "timeout_bisection": not args.no_timeout_bisection,
        "environment_retries": args.environment_retries,
        "suite_timed_out": state.suite_timed_out,
        "duration_ms": int(duration * 1000),
        "timing_comparable": not bool(plan.reusable_members),
        "resume_enabled": args.resume,
        "resume_key": args.resume_key if args.resume else None,
        "reused_member_count": len(plan.reusable_members),
        "executed_member_count": len(plan.selected_members) - len(plan.reusable_members),
        "source_identity": plan.resume_context.source_identity,
        "test_plan_identity": plan.resume_context.test_plan_identity,
        "toolchain_identity": plan.resume_context.toolchain_identity,
        "resume_suite_identity": plan.resume_context.suite_identity if args.resume else None,
        "execution_plan_identity": plan.execution_plan_identity,
        "execution_run_identity": plan.run_identity,
        "membership_receipt_path": str(receipt_path),
        "membership_receipt": receipt.as_dict(),
        "batches": sorted(state.batch_reports, key=lambda item: int(item["batch_index"])),
    }
    if diagnostics is not None:
        payload["diagnostics"] = diagnostics
    if getattr(args, "focused", False):
        payload["feedback"] = {
            "scope": "FOCUSED",
            "full_verification_status": "NOT_FULLY_VERIFIED",
            "next_check": {"command": args.next_check, "status": "UNVERIFIED"},
        }
    return payload


def _finish_batch_run(args: argparse.Namespace, plan: _BatchPlan, state: _BatchRunState) -> int:
    _apply_terminal_failures(args, plan, state)
    receipt, receipt_path = _finalize_membership(plan, state)
    coverage_result: dict[str, object] | None = None
    if plan.coverage_session is not None:
        status, output, elapsed = plan.coverage_session.finish(deadline=state.deadline)
        deadline_exhausted = state.deadline is not None and time.monotonic() >= state.deadline
        if deadline_exhausted:
            state.suite_timed_out = True
        coverage_result = {
            "returncode": status,
            "duration_ms": int(elapsed * 1000),
            "suite_deadline_exhausted": deadline_exhausted,
        }
        print("=== PYTEST NATIVE BRANCH COVERAGE (current-run data) ===", flush=True)
        if output:
            print(output, end="" if output.endswith("\n") else "\n", flush=True)
        state.failures += int(status != 0 or deadline_exhausted)
    duration = time.monotonic() - state.suite_started
    cgroup_started = plan.resource_budget.cgroup_usage_started
    cgroup_finished = (
        read_pytest_cgroup_usage(Path(cgroup_started.path)) if cgroup_started else None
    )
    observed_cgroup = pytest_cgroup_diagnostics(cgroup_started, cgroup_finished)
    usage = pytest_usage_diagnostics(plan.usage_started, read_pytest_usage())
    diagnostics: dict[str, object] = {
        "resource_budget": plan.resource_budget.report(effective_workers=plan.workers),
        "native_collection_duration_ms": int(plan.native_collection_duration * 1000),
        "native_collection_scope": "initial admission; execution children collect natively again",
        "usage_scope": "whole invocation including collection and waited children",
        "usage": usage,
        "observed_cgroup": observed_cgroup,
        "qualified": plan.invocation.qualified,
        "pytest_config": str(plan.invocation.config_file),
        "native_pytest_arg_count": len(plan.invocation.pytest_args),
        "native_pytest_arg_values": "omitted from diagnostics",
        "coverage": coverage_result,
    }
    cpu_text = f"{usage['cpu_seconds']['total_seconds']:.3f}s" if usage else "unavailable"
    rss_text = (
        f"{usage['peak_waited_child_rss_bytes'] / 1024**2:.1f}MiB" if usage else "unavailable"
    )
    print(
        "=== PYTEST RESOURCE USAGE === "
        f"cpu-self-and-waited-children={cpu_text} | "
        f"initial-native-collection={plan.native_collection_duration:.3f}s | "
        f"peak-waited-child-rss={rss_text} (process-lifetime high water; not aggregate)",
        flush=True,
    )
    cgroup_counters = observed_cgroup["cpu_stat_deltas"]
    throttled = cgroup_counters.get("throttled_usec") if isinstance(cgroup_counters, dict) else None
    print(
        "=== PYTEST OBSERVED CGROUP === "
        f"throttled-usec={throttled} | "
        f"memory-peak-bytes={observed_cgroup['memory_peak_bytes']} | "
        f"peak-concurrent-tasks={observed_cgroup['peak_tasks']} | "
        f"{observed_cgroup['availability']} "
        "(entire cgroup, possibly shared; lifetime peaks; not child creations)",
        flush=True,
    )
    _print_final_result(plan, state, receipt, receipt_path, duration)
    if args.report_path is not None:
        execution_report_contract.write_execution_report(
            args.report_path,
            _execution_report(
                args, plan, state, receipt, receipt_path, duration, diagnostics=diagnostics
            ),
        )
    return 0 if state.failures == 0 else 1


def run_batch_cli(
    parser: argparse.ArgumentParser, args: argparse.Namespace, callbacks: BatchCallbacks
) -> int:
    started = time.monotonic()
    usage_started = read_pytest_usage()
    try:
        resource_budget = resolve_pytest_resource_budget(args.workers)
        invocation = resolve_pytest_invocation(
            qualified=args.qualified,
            pytest_config=args.pytest_config,
            pytest_args=tuple(args.pytest_arg),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    with ExitStack() as owned:
        coverage_session = None
        coverage_requested = bool(
            args.coverage_source
            or args.coverage_data_file is not None
            or args.coverage_report
            or args.coverage_fail_under is not None
        )
        if coverage_requested:
            args.no_timeout_bisection = True
            args.environment_retries = 0
            print(
                "=== PYTEST COVERAGE RECOVERY POLICY === timeout refinement and "
                "environment retries disabled: correctness receipts do not establish "
                "saved native coverage",
                flush=True,
            )
            try:
                options = CoverageOptions(
                    data_file=args.coverage_data_file or Path(".coverage"),
                    sources=tuple(args.coverage_source),
                    reports=tuple(args.coverage_report or ("term",)),
                    fail_under=args.coverage_fail_under,
                    branch=True,
                )
                coverage_session = owned.enter_context(
                    BatchCoverageSession(
                        options,
                        cwd=invocation.cwd,
                        config_file=invocation.config_file,
                        environment=invocation.environment,
                        python_prefix=invocation.command_prefix[:-2],
                        timeout=float(args.batch_timeout),
                    )
                )
            except (OSError, RuntimeError, ValueError) as exc:
                parser.error(str(exc))
            intent = json.dumps(
                {
                    "sources": options.sources,
                    "data_file": str(options.data_file),
                    "reports": options.reports,
                    "fail_under": options.fail_under,
                    "branch": options.branch,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            args.resume_context = [
                *args.resume_context,
                "native-coverage-intent:" + hashlib.sha256(intent.encode()).hexdigest(),
            ]
            invocation = replace(
                invocation, pytest_args=(*invocation.pytest_args, *coverage_session.native_args)
            )
        plan = _build_batch_plan(
            parser,
            args,
            resource_budget=resource_budget,
            usage_started=usage_started,
            started=started,
            invocation=invocation,
            coverage_session=coverage_session,
        )
        _print_batch_plan(args, plan)
        state = _run_batch_executor(args, plan, callbacks)
        return _finish_batch_run(args, plan, state)
