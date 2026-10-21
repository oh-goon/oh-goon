from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.fleet_plan import ResolvedPlanStep
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.engine.command import CommandOutputObserver, CommandProcessObserver, CommandResult
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.integrations.fleet_plan_secret_redaction import streaming_host_secret_redactor


def observed_command_runner(
    runner: Callable[..., CommandResult],
    *,
    step: ResolvedPlanStep,
    attempt_number: int,
    observer: StepOutputObserver,
    diagnostics: DiagnosticEmitter | None,
    host_environment: Mapping[str, str],
    fleet_run_id: str | None,
    repository_id: str | None,
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
        **native_options: Any,
    ) -> CommandResult:
        redactors = {
            "stdout": streaming_host_secret_redactor(step, host_environment),
            "stderr": streaming_host_secret_redactor(step, host_environment),
        }

        def observe(stream: Literal["stdout", "stderr"], text: str) -> None:
            if stream_output is not None:
                try:
                    stream_output(stream, text)
                except Exception as exc:
                    emit_output_observer_failure(
                        diagnostics,
                        exc,
                        step_id=step.step_id,
                        operation=f"provided_stream_{stream}",
                        fleet_run_id=fleet_run_id,
                        repository_id=repository_id,
                    )
            try:
                redacted = redactors[stream].feed(text)
                if redacted:
                    observer(step.step_id, attempt_number, stream, redacted)
            except Exception as exc:
                emit_output_observer_failure(
                    diagnostics,
                    exc,
                    step_id=step.step_id,
                    operation=f"stream_{stream}",
                    fleet_run_id=fleet_run_id,
                    repository_id=repository_id,
                )

        try:
            return runner(
                *argv,
                timeout=timeout,
                cwd=cwd,
                env=env,
                cancel_requested=cancel_requested,
                stream_output=observe,
                process_observer=process_observer,
                runtime_supervision=runtime_supervision,
                **native_options,
            )
        finally:
            for stream, redactor in redactors.items():
                tail = redactor.flush()
                if not tail:
                    continue
                try:
                    observer(step.step_id, attempt_number, stream, tail)
                except Exception as exc:
                    emit_output_observer_failure(
                        diagnostics,
                        exc,
                        step_id=step.step_id,
                        operation=f"flush_{stream}",
                        fleet_run_id=fleet_run_id,
                        repository_id=repository_id,
                    )

    return command_runner


def emit_output_observer_failure(
    diagnostics: DiagnosticEmitter | None,
    exc: Exception,
    *,
    step_id: str,
    operation: str,
    fleet_run_id: str | None,
    repository_id: str | None,
) -> None:
    if diagnostics is None:
        return
    diagnostics.emit(
        DiagnosticEvent.from_exception(
            exc,
            source="step_output_observer",
            owner="FleetPlanStepExecution",
            operation=operation,
            execution_affected=False,
            diagnostic_capability_affected="live_output_capture",
            failure_class="diagnostic_degraded",
            step_id=step_id,
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
        )
    )
