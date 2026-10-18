from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.engine.command import CommandResult
from oh_goon.runtime.execution import shards as subject


def _plan(work: subject.ShardWork) -> subject.ShardExecutionPlan:
    return subject.ShardExecutionPlan(
        plan_identity="sha256:" + "a" * 64,
        generation_identity="sha256:" + "1" * 64,
        shards=(work,),
        source_schema="test.schema/v1",
        producer_version="test",
    )


def _context() -> subject.ShardExecutionContext:
    return subject.ShardExecutionContext(
        runner_toolchain_identity="sha256:" + "1" * 64,
        dependency_identity="sha256:" + "d" * 64,
        environment_identity="sha256:" + "e" * 64,
    )


def _work() -> subject.ShardWork:
    return subject.ShardWork(
        shard_id="a", argv=("fake", "a"), work_identity="sha256:" + "a" * 64
    )


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


def _policy_identity(**overrides: object) -> str:
    values: dict[str, object] = {
        "workers": 1,
        "shard_timeout_seconds": 10,
        "suite_timeout_seconds": 20,
        "minimum_execution_window_seconds": 1,
        "retry_policy": subject.ShardRetryPolicy(),
        "timeout_refinement": True,
        "resume": True,
    }
    values.update(overrides)
    return subject.execution_policy_identity(**values)  # type: ignore[arg-type]


def test_execution_policy_identity_changes_with_result_meaning_inputs() -> None:
    base = _policy_identity()
    changed = (
        _policy_identity(workers=2),
        _policy_identity(shard_timeout_seconds=11),
        _policy_identity(suite_timeout_seconds=21),
        _policy_identity(minimum_execution_window_seconds=2),
        _policy_identity(
            retry_policy=subject.ShardRetryPolicy(
                max_attempts=2,
                retry_timeouts=True,
                reason="retry",
                delays_seconds=(0,),
            )
        ),
        _policy_identity(timeout_refinement=False),
        _policy_identity(resume=False),
    )
    assert all(value != base for value in changed)
    assert len(set(changed)) == len(changed)


def test_durable_pass_resume_is_bound_to_execution_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    plan = _plan(_work())
    first = subject.execute_shard_plan(
        plan, cwd=tmp_path, workers=1, shard_timeout_seconds=1,
        state_root=state, execution_context=_context(),
    )
    second = subject.execute_shard_plan(
        plan, cwd=tmp_path, workers=1, shard_timeout_seconds=2,
        state_root=state, execution_context=_context(),
    )
    assert first.results[0].status == second.results[0].status == "PASS"
    assert second.results[0].resumed is False
    assert calls == 2
    assert first.execution_policy_identity != second.execution_policy_identity


def test_durable_refinement_resume_is_bound_to_execution_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        calls.append(args)
        if args == ("fake", "a", "b", "c", "d"):
            raise subprocess.TimeoutExpired(args, float(kwargs["timeout"]))
        return CommandResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    plan = _plan(_refinable_work())
    first = subject.execute_shard_plan(
        plan, cwd=tmp_path, workers=1, shard_timeout_seconds=1,
        state_root=state, execution_context=_context(),
    )
    first_count = len(calls)
    second = subject.execute_shard_plan(
        plan, cwd=tmp_path, workers=1, shard_timeout_seconds=2,
        state_root=state, execution_context=_context(),
    )
    assert first.results[0].status == second.results[0].status == "REFINED_PASS"
    assert second.results[0].reason_code == "timeout_refinement"
    assert len(calls) > first_count
    assert first.execution_policy_identity != second.execution_policy_identity


def test_canonical_evidence_retains_execution_policy_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subject, "run_command",
        lambda *args, **kwargs: CommandResult(returncode=0, stdout="ok", stderr=""),
    )
    report = subject.execute_shard_plan(
        _plan(_work()), cwd=tmp_path, workers=1, shard_timeout_seconds=1,
        execution_context=_context(),
    )
    canonical = report.canonical_evidence()
    assert canonical.execution_policy_identity == report.execution_policy_identity
    assert canonical.contract_schema == type(canonical).model_fields["contract_schema"].default
