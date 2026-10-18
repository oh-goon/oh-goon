from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oh_goon.engine.command import CommandCancelledError, run_command
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.migration_plan import AutomaticMigrationPlan


@dataclass(frozen=True, slots=True)
class MigrationStepResult:
    succeeded: bool
    output: tuple[str, ...]


def execute_migration_plan(
    repository: Path,
    plan: AutomaticMigrationPlan,
    cancel_requested: Callable[[], bool] | None = None,
) -> MigrationStepResult:
    """Execute only the filesystem/package-manager portion of an automatic migration plan."""
    venv = repository / ".venv"
    venv_existed = venv.exists()
    output: list[str] = []
    try:
        try:
            for argv in plan.steps:
                result = run_command(
                    *argv,
                    cwd=repository,
                    timeout=3600.0,
                    cancel_requested=cancel_requested,
                )
                output.append(result.stdout or result.stderr)
                if result.returncode != 0:
                    return MigrationStepResult(False, tuple(output))
        except CommandCancelledError as exc:
            raise OperationCancelledError(str(exc)) from exc
        return MigrationStepResult(True, tuple(output))
    finally:
        if not venv_existed and venv.exists():
            shutil.rmtree(venv, ignore_errors=True)
