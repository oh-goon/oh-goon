from __future__ import annotations

import traceback as traceback_module
from typing import Literal
from uuid import uuid4

from pydantic import Field

from oh_goon.core.diagnostic_redaction import redact_diagnostic_text
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.time import utc_now

DiagnosticSeverity = Literal["INFO", "WARNING", "ERROR"]
MAX_DIAGNOSTIC_MESSAGE_CHARS = 4_000
MAX_DIAGNOSTIC_TRACEBACK_CHARS = 20_000


class DiagnosticEvent(ImmutableModel):
    """Non-authoritative evidence emitted by the semantic owner of a diagnostic fact."""

    event_id: str = Field(default_factory=lambda: f"diagnostic-{uuid4().hex}")
    observed_at: str = Field(default_factory=lambda: utc_now().isoformat())
    severity: DiagnosticSeverity
    source: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    message: str = Field(min_length=1)
    execution_affected: bool
    diagnostic_capability_affected: str | None = None
    failure_class: str | None = None
    exception_type: str | None = None
    traceback: str | None = None
    fleet_run_id: str | None = None
    repository_id: str | None = None
    step_id: str | None = None
    request_id: str | None = None
    http_method: str | None = None
    http_path: str | None = None

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        severity: DiagnosticSeverity = "ERROR",
        source: str,
        owner: str,
        operation: str,
        execution_affected: bool,
        diagnostic_capability_affected: str | None = None,
        failure_class: str | None = None,
        fleet_run_id: str | None = None,
        repository_id: str | None = None,
        step_id: str | None = None,
        request_id: str | None = None,
        http_method: str | None = None,
        http_path: str | None = None,
        traceback_frames_only: bool = False,
    ) -> DiagnosticEvent:
        return cls(
            severity=severity,
            source=source,
            owner=owner,
            operation=operation,
            message=_exception_message(exc),
            execution_affected=execution_affected,
            diagnostic_capability_affected=diagnostic_capability_affected,
            failure_class=failure_class,
            exception_type=exc.__class__.__name__,
            traceback=_exception_traceback(exc, frames_only=traceback_frames_only),
            fleet_run_id=fleet_run_id,
            repository_id=repository_id,
            step_id=step_id,
            request_id=request_id,
            http_method=http_method,
            http_path=http_path,
        )


def _exception_message(exc: BaseException) -> str:
    try:
        message = str(exc) or exc.__class__.__name__
    except Exception:
        message = exc.__class__.__name__
    return redact_diagnostic_text(message)[:MAX_DIAGNOSTIC_MESSAGE_CHARS]


def _exception_traceback(exc: BaseException, *, frames_only: bool = False) -> str:
    try:
        if frames_only:
            # Private protocol failures may chain into transport/source data.
            # Keep original control frames without source lines, locals or chains.
            frames: list[tuple[traceback_module.FrameSummary, str]] = []
            omitted_frames = 0
            for frame, line_number in traceback_module.walk_tb(exc.__traceback__):
                if len(frames) >= 32:
                    omitted_frames += 1
                    continue
                module = frame.f_globals.get("__name__")
                module_name = module[:256] if isinstance(module, str) else "unknown_module"
                frames.append(
                    (
                        traceback_module.FrameSummary(
                            frame.f_code.co_filename,
                            line_number,
                            frame.f_code.co_name,
                            lookup_line=False,
                        ),
                        module_name,
                    )
                )
            rendered = "\n".join(
                f'  File "{frame.filename}", line {frame.lineno}, in {module}.{frame.name}'
                for frame, module in frames
            )
            if omitted_frames:
                rendered += f"\n[omitted {omitted_frames} additional control frames]"
            rendered += "\n" + exc.__class__.__name__ + ": " + _exception_message(exc)
        else:
            rendered = "".join(
                traceback_module.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip()
    except Exception:
        rendered = f"{exc.__class__.__module__}.{exc.__class__.__qualname__}"
    redacted = redact_diagnostic_text(rendered)
    if frames_only and len(redacted) > MAX_DIAGNOSTIC_TRACEBACK_CHARS:
        marker = "\n[additional diagnostic characters omitted]"
        return redacted[: MAX_DIAGNOSTIC_TRACEBACK_CHARS - len(marker)] + marker
    return redacted[:MAX_DIAGNOSTIC_TRACEBACK_CHARS]
