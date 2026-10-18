from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from threading import Barrier, Lock

import pytest

from oh_goon.engine.command import CommandResult
from oh_goon.runtime.execution import shards as subject


from ._shard_execution_support import (
    context as _context,
    exact_command_result as _exact_command_result,
    plan as _plan,
    work as _work,
)

def test_retry_policy_requires_explicit_reason_condition_and_delays() -> None:
    with pytest.raises(ValueError, match="condition"):
        subject.ShardRetryPolicy(max_attempts=2, reason="retry", delays_seconds=(0,))
    with pytest.raises(ValueError, match="reason"):
        subject.ShardRetryPolicy(max_attempts=2, retry_environment_failures=True, delays_seconds=(0,))
    with pytest.raises(ValueError, match="delay"):
        subject.ShardRetryPolicy(max_attempts=2, retry_environment_failures=True, reason="pressure")


def test_explicit_retry_relaunches_only_admitted_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(returncode=75 if calls == 1 else 0, stdout="", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        _plan(_work()),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=10,
        retry_policy=subject.ShardRetryPolicy(
            max_attempts=2,
            retryable_exit_codes=(75,),
            reason="explicit transient exit",
            delays_seconds=(0,),
        ),
    )
    assert calls == 2
    assert report.results[0].status == "PASS"
    assert report.results[0].attempts[0].retry_admitted is True


def test_environment_failure_stops_new_launches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args[-1])
        return CommandResult(
            returncode=1,
            stdout="",
            stderr="RuntimeError: can't start new thread",
        )

    monkeypatch.setattr(subject, "run_command", fake_run)
    works = tuple(
        subject.ShardWork(
            shard_id=str(index),
            argv=("fake", str(index)),
            work_identity="sha256:" + str(index + 1) * 64,
        )
        for index in range(3)
    )
    report = subject.execute_shard_plan(
        _plan(*works), cwd=tmp_path, workers=1, shard_timeout_seconds=1
    )
    assert calls == ["0"]
    assert report.results[0].status == "ENVIRONMENT_BLOCKED"
    assert [result.status for result in report.results[1:]] == ["CANCELLED", "CANCELLED"]


def test_pass_resume_is_bound_to_generation_plan_and_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return _exact_command_result()

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    first = subject.execute_shard_plan(
        _plan(_work()), cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    )
    second = subject.execute_shard_plan(
        _plan(_work()), cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    )
    third = subject.execute_shard_plan(
        _plan(_work(), generation="2"),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=10,
        state_root=state,
        execution_context=_context(),
    )
    assert first.results[0].status == "PASS"
    assert second.results[0].status == "RESUMED_PASS"
    assert third.results[0].status == "PASS"
    assert calls == 2


def test_tampered_pass_evidence_is_not_resumed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return _exact_command_result()

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    plan = _plan(_work())
    subject.execute_shard_plan(plan, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context())
    evidence = next(state.rglob("*.json"))
    evidence.write_text(evidence.read_text().replace('"status":"PASS"', '"status":"FAIL"'))
    report = subject.execute_shard_plan(plan, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context())
    assert report.results[0].status == "PASS"
    assert calls == 2



def test_worker_limit_bounds_concurrent_launches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    active = 0
    maximum = 0
    launches = 0
    lock = Lock()
    first_pair = Barrier(2, timeout=2)

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal active, maximum, launches
        with lock:
            launches += 1
            launch_number = launches
            active += 1
            maximum = max(maximum, active)
        if launch_number <= 2:
            first_pair.wait()
        with lock:
            active -= 1
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    works = tuple(
        subject.ShardWork(
            shard_id=str(index),
            argv=("fake", str(index)),
            work_identity="sha256:" + f"{index + 1:064x}",
        )
        for index in range(5)
    )
    report = subject.execute_shard_plan(
        _plan(*works), cwd=tmp_path, workers=2, shard_timeout_seconds=1
    )
    assert report.passed
    assert maximum == 2


def test_real_subprocess_timeout_uses_oh_goon_command_owner(tmp_path: Path) -> None:
    work = subject.ShardWork(
        shard_id="slow",
        argv=(sys.executable, "-c", "import time; time.sleep(1)"),
        work_identity="sha256:" + "f" * 64,
    )
    report = subject.execute_shard_plan(
        _plan(work), cwd=tmp_path, workers=1, shard_timeout_seconds=0.1
    )
    assert report.results[0].status == "TIMEOUT"


def test_suite_deadline_marks_unstarted_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        timeout = float(kwargs["timeout"])
        time.sleep(timeout + 0.02)
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(subject, "run_command", fake_run)
    works = tuple(
        subject.ShardWork(
            shard_id=str(index),
            argv=("fake", str(index)),
            work_identity="sha256:" + f"{index + 1:064x}",
        )
        for index in range(3)
    )
    report = subject.execute_shard_plan(
        _plan(*works),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        suite_timeout_seconds=0.08,
    )
    assert report.results[0].status == "SUITE_TIMEOUT"
    assert [result.status for result in report.results[1:]] == [
        "DEADLINE_RESERVED",
        "DEADLINE_RESERVED",
    ]
    assert all(
        result.reason_code == "minimum_execution_window_unavailable"
        for result in report.results[1:]
    )


def test_pass_resume_is_bound_to_repository_evidence_and_producer_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return _exact_command_result()

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    work = _work()
    base = dict(
        plan_identity="sha256:" + "a" * 64,
        generation_identity="sha256:" + "1" * 64,
        shards=(work,),
        source_schema="hashmarks.test-shards.v3",
        producer_version="0.14.0",
        repository_content_identity="sha256:" + "f" * 64 + ":1",
        selection_input_identity="sha256:" + "3" * 64,
        producer_implementation_identity="sha256:" + "4" * 64,
        producer_algorithm="hashmarks.pytest-test-node-shards.v3",
    )
    first_plan = subject.ShardExecutionPlan(
        **base,
        repository_evidence_identity="sha256:" + "5" * 64,
        producer_artifact_identity="sha256:" + "6" * 64,
        producer_contract_identity="sha256:" + "9" * 64,
    )
    same_plan = subject.ShardExecutionPlan(
        **base,
        repository_evidence_identity="sha256:" + "5" * 64,
        producer_artifact_identity="sha256:" + "6" * 64,
        producer_contract_identity="sha256:" + "9" * 64,
    )
    different_envelope = subject.ShardExecutionPlan(
        **base,
        repository_evidence_identity="sha256:" + "7" * 64,
        producer_artifact_identity="sha256:" + "6" * 64,
        producer_contract_identity="sha256:" + "9" * 64,
    )
    different_producer = subject.ShardExecutionPlan(
        **base,
        repository_evidence_identity="sha256:" + "5" * 64,
        producer_artifact_identity="sha256:" + "8" * 64,
        producer_contract_identity="sha256:" + "9" * 64,
    )
    different_contract = subject.ShardExecutionPlan(
        **base,
        repository_evidence_identity="sha256:" + "5" * 64,
        producer_artifact_identity="sha256:" + "6" * 64,
        producer_contract_identity="sha256:" + "7" * 64,
    )

    assert subject.execute_shard_plan(
        first_plan, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    ).results[0].status == "PASS"
    assert subject.execute_shard_plan(
        same_plan, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    ).results[0].status == "RESUMED_PASS"
    assert subject.execute_shard_plan(
        different_envelope, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    ).results[0].status == "PASS"
    assert subject.execute_shard_plan(
        different_producer, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    ).results[0].status == "PASS"
    assert subject.execute_shard_plan(
        different_contract, cwd=tmp_path, workers=1, shard_timeout_seconds=1, state_root=state, execution_context=_context()
    ).results[0].status == "PASS"
    assert calls == 4



def _refinable_work() -> subject.ShardWork:
    members = ("a", "b", "c", "d")
    return subject.ShardWork(
        shard_id="root",
        argv=("fake", *members),
        work_identity="sha256:" + "9" * 64,
        isolated_process=True,
        members=members,
        command_prefix=("fake",),
    )


def test_durable_resume_requires_exact_execution_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return _exact_command_result()

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    plan = _plan(_work())
    first = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context("1"),
    )
    same = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context("1"),
    )
    changed_runner = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context("2"),
    )
    changed_dependencies = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=subject.ShardExecutionContext(
            runner_toolchain_identity="sha256:" + "1" * 64,
            dependency_identity="sha256:" + "a" * 64,
            environment_identity="sha256:" + "e" * 64,
        ),
    )
    changed_environment = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=subject.ShardExecutionContext(
            runner_toolchain_identity="sha256:" + "1" * 64,
            dependency_identity="sha256:" + "d" * 64,
            environment_identity="sha256:" + "b" * 64,
        ),
    )
    assert first.results[0].status == "PASS"
    assert same.results[0].status == "RESUMED_PASS"
    assert changed_runner.results[0].status == "PASS"
    assert changed_dependencies.results[0].status == "PASS"
    assert changed_environment.results[0].status == "PASS"
    assert calls == 4


def test_durable_resume_rejects_missing_execution_context(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact execution_context"):
        subject.execute_shard_plan(
            _plan(_work()),
            cwd=tmp_path,
            workers=1,
            shard_timeout_seconds=1,
            state_root=tmp_path / "state",
        )


def test_timeout_refinement_is_persisted_and_restart_skips_coarse_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        if args == ("fake", "a", "b", "c", "d"):
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return _exact_command_result()

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    plan = _plan(_refinable_work())
    first = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    first_call_count = len(calls)
    second = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=_context(),
    )
    assert first.results[0].status == "REFINED_PASS"
    assert [child.status for child in first.results[0].children] == ["PASS", "PASS"]
    assert second.results[0].status == "REFINED_PASS"
    assert second.results[0].reason_code == "resumed_durable_timeout_refinement"
    assert [child.status for child in second.results[0].children] == [
        "RESUMED_PASS",
        "RESUMED_PASS",
    ]
    assert len(calls) == first_call_count
    assert calls.count(("fake", "a", "b", "c", "d")) == 1


def test_environment_retry_after_refinement_retries_exact_child_not_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []
    first_child_attempts = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal first_child_attempts
        calls.append(args)
        if args == ("fake", "a", "b", "c", "d"):
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        if args == ("fake", "a", "b"):
            first_child_attempts += 1
            if first_child_attempts == 1:
                return CommandResult(
                    returncode=1,
                    stdout="",
                    stderr="RuntimeError: can't start new thread",
                )
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        _plan(_refinable_work()),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        retry_policy=subject.ShardRetryPolicy(
            max_attempts=2,
            retry_environment_failures=True,
            reason="environment recovery",
            delays_seconds=(0,),
        ),
        execution_context=_context(),
    )
    assert report.passed
    assert calls.count(("fake", "a", "b", "c", "d")) == 1
    assert calls.count(("fake", "a", "b")) == 2
    child = report.results[0].children[0]
    assert child.attempts[0].retry_admitted is True
    assert child.attempts[0].physical_work_identity == child.work_identity
    assert child.attempts[1].physical_work_identity == child.work_identity


def test_deadline_reserve_never_launches_work_without_minimum_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    called = False

    def forbidden(*args: str, **kwargs: object) -> CommandResult:
        nonlocal called
        called = True
        raise AssertionError("deadline-reserved work must not launch")

    monkeypatch.setattr(subject, "run_command", forbidden)
    report = subject.execute_shard_plan(
        _plan(_work()),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        suite_timeout_seconds=0.01,
        minimum_execution_window_seconds=0.05,
        execution_context=_context(),
    )
    assert called is False
    assert report.results[0].status == "DEADLINE_RESERVED"
    assert report.results[0].reason_code == "minimum_execution_window_unavailable"


def test_work_remains_eligible_when_minimum_window_is_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        _plan(_work()),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=10,
        suite_timeout_seconds=6,
        minimum_execution_window_seconds=5,
        execution_context=_context(),
    )
    assert calls
    assert report.results[0].status == "PASS"


def test_deadline_becoming_unsafe_between_units_does_not_start_next_physical_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []
    remaining = iter((10.0, 10.0, 0.0, 0.0))

    def fake_remaining(_self) -> float:
        return next(remaining, 0.0)

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject._ShardTreeExecution, "remaining_budget", fake_remaining)
    monkeypatch.setattr(subject, "run_command", fake_run)
    second = subject.ShardWork(
        shard_id="second",
        argv=("fake", "second"),
        work_identity="sha256:" + "2" * 64,
    )
    plan = subject.ShardExecutionPlan(
        plan_identity="sha256:" + "a" * 64,
        generation_identity="sha256:" + "b" * 64,
        shards=(_work(), second),
        source_schema="tests",
        producer_version="tests",
    )
    report = subject.execute_shard_plan(
        plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=10,
        suite_timeout_seconds=10,
        minimum_execution_window_seconds=5,
        execution_context=_context(),
    )
    assert len(calls) == 1
    assert report.results[1].status == "DEADLINE_RESERVED"
    assert report.results[1].reason_code == "minimum_execution_window_unavailable"



def test_canonical_evidence_separates_described_work_from_physical_attempt_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        _plan(_work()),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        execution_context=_context(),
    )
    canonical = report.canonical_evidence()
    result = canonical.results[0]
    assert result.described_work_identity == _work().work_identity
    assert result.physical_work_identity == _work().work_identity
    assert result.attempts[0].attempt_identity.startswith("sha256:")
    assert result.attempts[0].attempt_identity != result.described_work_identity
    assert canonical.execution_context_identity == _context().context_identity


def _verification_plan(work: subject.ShardWork) -> subject.ShardExecutionPlan:
    return subject.ShardExecutionPlan(
        plan_identity="sha256:" + "a" * 64,
        generation_identity="sha256:" + "1" * 64,
        shards=(work,),
        source_schema="hashmarks.verification-selection.v1",
        producer_version="0.14.0",
        repository_content_identity="sha256:" + "2" * 64 + ":4",
        producer_artifact_identity="sha256:" + "3" * 64,
        producer_implementation_identity="sha256:" + "8" * 64,
        producer_contract_identity="sha256:" + "4" * 64,
        verification_selection_evidence_identity="sha256:" + "5" * 64,
        selected_membership_identity="sha256:" + "6" * 64,
        verification_revision_identity="sha256:" + "7" * 64,
        selected_test_nodes=("a", "b", "c", "d"),
        unproven_owner_members=("c",),
    )


def test_hashmarks_verification_bisection_preserves_exact_final_membership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        if args == ("fake", "a", "b", "c", "d"):
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    report = subject.execute_shard_plan(
        _verification_plan(_refinable_work()),
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        execution_context=_context(),
    )
    assert report.passed
    assert report.membership_complete is True
    assert report.actual_executed_members == ("a", "b", "c", "d")
    assert report.selected_membership_identity == "sha256:" + "6" * 64
    assert report.unproven_owner_members == ("c",)
    canonical = report.canonical_evidence()
    assert canonical.membership_complete is True
    assert canonical.actual_executed_members == ("a", "b", "c", "d")
    assert canonical.selected_membership_identity == "sha256:" + "6" * 64
    assert canonical.verification_selection_evidence_identity == "sha256:" + "5" * 64
    assert canonical.repository_content_identity == "sha256:" + "2" * 64 + ":4"
    assert canonical.verification_revision_identity == "sha256:" + "7" * 64
    assert canonical.unproven_owner_members == ("c",)


def test_verification_plan_rejects_omitted_or_duplicate_membership() -> None:
    omitted = subject.ShardWork(
        shard_id="root",
        argv=("fake", "a", "b", "c"),
        work_identity="sha256:" + "9" * 64,
        members=("a", "b", "c"),
        command_prefix=("fake",),
    )
    with pytest.raises(ValueError, match="conserve admitted verification membership"):
        _verification_plan(omitted)

    left = subject.ShardWork(
        shard_id="left",
        argv=("fake", "a", "b"),
        work_identity="sha256:" + "8" * 64,
        members=("a", "b"),
        command_prefix=("fake",),
    )
    right = subject.ShardWork(
        shard_id="right",
        argv=("fake", "b", "c", "d"),
        work_identity="sha256:" + "7" * 64,
        members=("b", "c", "d"),
        command_prefix=("fake",),
    )
    with pytest.raises(ValueError, match="conserve admitted verification membership"):
        subject.ShardExecutionPlan(
            plan_identity="sha256:" + "a" * 64,
            generation_identity="sha256:" + "1" * 64,
            shards=(left, right),
            source_schema="hashmarks.verification-selection.v1",
            producer_version="0.14.0",
            producer_artifact_identity="sha256:" + "3" * 64,
            producer_contract_identity="sha256:" + "4" * 64,
            verification_selection_evidence_identity="sha256:" + "5" * 64,
            selected_membership_identity="sha256:" + "6" * 64,
            verification_revision_identity="sha256:" + "7" * 64,
            selected_test_nodes=("a", "b", "c", "d"),
        )
