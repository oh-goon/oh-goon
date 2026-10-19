from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from oh_goon.engine.command import (
    CommandOutputObserver,
    CommandProcessLifecycleEvent,
    CommandProcessObserver,
    CommandResult,
)
from oh_goon.engine.command_supervision import CommandRuntimeSupervision


def lifecycle_command_runner(
    runner: Callable[..., CommandResult],
    *,
    step_id: str,
    step_order: int,
    attempt_number: int,
    observer: Callable[..., None],
) -> Callable[..., CommandResult]:
    def command_runner(
        *argv: str,
        timeout: float | None = 10.0,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        stream_output: CommandOutputObserver | None = None,
        process_observer: CommandProcessObserver | None = None,
        runtime_supervision: CommandRuntimeSupervision | None = None,
    ) -> CommandResult:
        def observe_process(event: CommandProcessLifecycleEvent) -> None:
            observer(
                step_id,
                step_order,
                event.kind,
                attempt_number=attempt_number,
                physical_execution_id=event.physical_execution_id,
                pid=event.pid,
                process_identity=event.process_identity,
                returncode=event.returncode,
                termination_reason=event.termination_reason,
            )
            if process_observer is not None:
                process_observer(event)

        return runner(
            *argv,
            timeout=timeout,
            cwd=cwd,
            env=env,
            cancel_requested=cancel_requested,
            stream_output=stream_output,
            process_observer=observe_process,
            runtime_supervision=runtime_supervision,
        )

    return command_runner
