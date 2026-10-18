from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from oh_goon.engine.command import CommandCancelledError, CommandResult, run_command
from oh_goon.engine.errors import OperationCancelledError

Cancel = Callable[[], bool] | None


def run_operation_command(
    repository: Path,
    *argv: str,
    timeout: float = 3600.0,
    cancel_requested: Cancel = None,
) -> CommandResult:
    """Run one cancellable engine command and translate process cancellation to operation semantics."""
    try:
        return run_command(
            *argv,
            cwd=repository,
            timeout=timeout,
            cancel_requested=cancel_requested,
        )
    except CommandCancelledError as exc:
        raise OperationCancelledError(str(exc)) from exc
