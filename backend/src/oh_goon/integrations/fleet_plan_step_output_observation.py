from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol

from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.core.models.diagnostic import MAX_DIAGNOSTIC_MESSAGE_CHARS, DiagnosticEvent
from oh_goon.core.models.fleet_observation import AgentHarnessObservation
from oh_goon.core.models.fleet_plan import ResolvedLocalSearchAgentStep, ResolvedPlanStep
from oh_goon.core.ports.diagnostics import DiagnosticEmitter
from oh_goon.core.ports.step_output import StepOutputObserver
from oh_goon.engine.command import CommandOutputObserver, CommandProcessObserver, CommandResult
from oh_goon.engine.command_supervision import CommandRuntimeSupervision
from oh_goon.integrations.fleet_plan_secret_redaction import (
    StreamingSecretRedactor,
    redact_host_secret_text,
    streaming_host_secret_redactor,
)


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
        selected = step.native_agent if isinstance(step, ResolvedLocalSearchAgentStep) else None
        private_native_protocol = (
            selected is not None
            and (
                selected.profile.sandbox_owner == "execution_backend"
                or selected.profile.codex_service is not None
            )
            and bool(argv)
            and (
                argv[0] == selected.definition.executable
                or native_options.get("broker_directory") is not None
            )
        )
        # Protected native protocol may contain private source and task data.
        # Parse it internally without copying the raw stream into live output.
        redactors: dict[Literal["stdout", "stderr"], StreamingSecretRedactor] = (
            {}
            if private_native_protocol
            else {
                "stdout": streaming_host_secret_redactor(step, host_environment),
                "stderr": streaming_host_secret_redactor(step, host_environment),
            }
        )

        def observe(stream: Literal["stdout", "stderr"], text: str) -> None:
            if stream_output is not None:
                try:
                    stream_output(stream, text)
                except Exception as exc:
                    emit_output_observer_failure(
                        diagnostics,
                        RuntimeError("Protected native protocol observer failed")
                        if private_native_protocol
                        else exc,
                        step_id=step.step_id,
                        operation=f"provided_stream_{stream}",
                        fleet_run_id=fleet_run_id,
                        repository_id=repository_id,
                    )
            if private_native_protocol:
                return
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


class StepDiagnosticObserver(Protocol):
    def __call__(self, text: str, exception: BaseException | None = None) -> DiagnosticEvent: ...


def observed_step_diagnostic(
    *,
    step: ResolvedPlanStep,
    attempt_number: int,
    observer: StepOutputObserver | None,
    diagnostics: DiagnosticEmitter | None,
    host_environment: Mapping[str, str],
    fleet_run_id: str | None,
    repository_id: str | None,
) -> StepDiagnosticObserver:
    """Retain safe control failures independently of transient output capture."""

    def observe(text: str, exception: BaseException | None = None) -> DiagnosticEvent:
        redacted = redact_diagnostic_text(redact_host_secret_text(step, text, host_environment))
        bounded = redacted[:MAX_DIAGNOSTIC_MESSAGE_CHARS]
        # A fresh safe exception preserves only the actual owner's frames.
        # Original exception arguments, chained errors and locals stay private.
        safe = RuntimeError(bounded).with_traceback(
            exception.__traceback__ if exception is not None else None
        )
        event = DiagnosticEvent.from_exception(
            safe,
            source="native_agent_control",
            owner="FleetPlanStepExecution",
            operation=f"native_failure_attempt_{attempt_number}",
            execution_affected=True,
            failure_class="native_execution_failed",
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
            step_id=step.step_id,
            traceback_frames_only=True,
        )
        if exception is not None:
            event = event.model_copy(update={"exception_type": exception.__class__.__name__})
        if diagnostics is not None:
            diagnostics.emit(event)
        if observer is not None:
            try:
                observer(step.step_id, attempt_number, "stderr", bounded[:2048] + "\n")
            except Exception as exc:
                emit_output_observer_failure(
                    diagnostics,
                    exc,
                    step_id=step.step_id,
                    operation="control_diagnostic",
                    fleet_run_id=fleet_run_id,
                    repository_id=repository_id,
                )
        # The original native Step may retain this SAME sanitized fact in its
        # existing journal observation, independent of optional diagnostic sinks.
        return event

    return observe


def observed_native_visible_claim(
    *,
    step_id: str,
    attempt_number: int,
    diagnostics: DiagnosticEmitter | None,
    fleet_run_id: str | None,
    repository_id: str | None,
) -> Callable[[AgentHarnessObservation], None]:
    """Mirror already-redacted bounded visible claims to passive diagnostics.

    Incremental storage can survive interruption before a Step result exists.
    It never supplies Run completion, cleanup, verification or replay authority.
    """

    def observe(claim: AgentHarnessObservation) -> None:
        if diagnostics is None:
            return
        diagnostics.emit(
            DiagnosticEvent(
                severity="INFO",
                source="native_visible_claim",
                owner="FleetPlanStepExecution",
                operation=f"visible_native_claim_attempt_{attempt_number}",
                message=claim.summary,
                execution_affected=False,
                failure_class="untrusted_native_claim",
                fleet_run_id=fleet_run_id,
                repository_id=repository_id,
                step_id=step_id,
            )
        )

    return observe


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
