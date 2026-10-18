from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.engine.command import CommandResult, RuntimeAuthorityReceipt
from oh_goon.runtime.execution import shards as subject

from ._shard_execution_support import context, plan, work


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def test_durable_pass_is_not_persisted_without_exact_runtime_materialization(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    def fake_run(*args: str, **kwargs: object) -> CommandResult:
        nonlocal calls
        calls += 1
        return CommandResult(
            returncode=0,
            stdout="ok",
            stderr="",
            runtime_authority=RuntimeAuthorityReceipt(
                executable_path="/observed/test-runner",
                executable_identity=_sha("1"),
                executable_byte_count=1,
                path_authority_identity=_sha("2"),
                environment_identity=_sha("3"),
                runtime_identity=_sha("4"),
            ),
        )

    monkeypatch.setattr(subject, "run_command", fake_run)
    state = tmp_path / "state"
    shard_plan = plan(work())
    first = subject.execute_shard_plan(
        shard_plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=context(),
    )
    second = subject.execute_shard_plan(
        shard_plan,
        cwd=tmp_path,
        workers=1,
        shard_timeout_seconds=1,
        state_root=state,
        execution_context=context(),
    )

    assert first.results[0].status == second.results[0].status == "PASS"
    assert first.results[0].attempts[0].runtime_authority_receipt_identity is None
    assert second.results[0].resumed is False
    assert calls == 2
    assert not tuple(state.rglob("*.json"))
