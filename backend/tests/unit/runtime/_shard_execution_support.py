from __future__ import annotations

import sys

from oh_goon.engine.command import CommandResult, RuntimeAuthorityReceipt
from oh_goon.runtime.execution import shards as subject


def _sha(char: str) -> str:
    return "sha256:" + char * 64


def exact_command_result(*, stdout: str = "ok") -> CommandResult:
    return CommandResult(
        returncode=0,
        stdout=stdout,
        stderr="",
        runtime_authority=RuntimeAuthorityReceipt(
            executable_path="/sealed/test-runner",
            executable_identity=_sha("1"),
            executable_byte_count=1,
            path_authority_identity=_sha("2"),
            environment_identity=_sha("3"),
            runtime_identity=_sha("4"),
            execution_materialization_identity=_sha("5"),
        ),
    )


def work(name: str = "a") -> subject.ShardWork:
    return subject.ShardWork(
        shard_id=name,
        argv=(sys.executable, "-c", "print('ok')"),
        work_identity="sha256:" + (name[0] * 64),
    )


def plan(*works: subject.ShardWork, generation: str = "1") -> subject.ShardExecutionPlan:
    return subject.ShardExecutionPlan(
        plan_identity="sha256:" + "a" * 64,
        generation_identity="sha256:" + generation * 64,
        shards=tuple(works),
        source_schema="test.schema/v1",
        producer_version="test",
    )


def context(seed: str = "1") -> subject.ShardExecutionContext:
    return subject.ShardExecutionContext(
        runner_toolchain_identity="sha256:" + seed * 64,
        dependency_identity="sha256:" + "d" * 64,
        environment_identity="sha256:" + "e" * 64,
    )
