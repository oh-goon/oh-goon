from __future__ import annotations

import os
from pathlib import Path

from oh_goon.core.strict_executor_authority import (
    EXECUTORS_FILE_ENV,
    STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT,
)
from oh_goon.repository_tooling.acceptance.local_executor import configure_local_runtime_executor
from oh_goon.repository_tooling.acceptance.shared_plan_parallel import (
    exercise_parallel_shared_plan_cancellation,
    exercise_parallel_shared_plan_failure_repair,
    exercise_parallel_shared_plan_stale_preview_recovery,
)


def _isolated_local_runtime_environment(*, temp: Path, state: Path, config: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(temp / "home"),
            "XDG_CONFIG_HOME": str(temp / "xdg-config"),
            "XDG_DATA_HOME": str(temp / "xdg-data"),
            "XDG_CACHE_HOME": str(temp / "xdg-cache"),
            "LOCALAPPDATA": str(temp / "local-app-data"),
            "OH_GOON_DB": str(state / "oh-goon.db"),
            "OH_GOON_LEAGUE_FILE": str(config / "league.yml"),
            "OH_GOON_SHARED_PLAN_DIR": str(config / "shared-plans"),
            "OH_GOON_LINEUP_DIR": str(config / "lineups"),
        }
    )
    return env


def prepare_verification_local_runtime_environment(
    *, temp: Path, state: Path, config: Path
) -> dict[str, str]:
    """Prepare APP runtime verification without importing strict executor authority."""

    env = _isolated_local_runtime_environment(temp=temp, state=state, config=config)
    for name in STRICT_EXECUTOR_AUTHORITY_ENVIRONMENT:
        env.pop(name, None)
    return env


def prepare_local_runtime_environment(
    *, temp: Path, state: Path, config: Path
) -> tuple[dict[str, str], bool, str | None]:
    """Prepare the production-shaped strict local-runtime acceptance environment."""

    env = _isolated_local_runtime_environment(temp=temp, state=state, config=config)
    operator_executor_configured = bool(env.get(EXECUTORS_FILE_ENV, "").strip())
    _, executor_issue = configure_local_runtime_executor(
        state=config / "runtime-authority", environment=env
    )
    return env, operator_executor_configured, executor_issue


def exercise_operator_parallel_acceptance(
    *,
    enabled: bool,
    environment: dict[str, str],
    frontend_base: str,
    repositories: Path,
    access_token: str,
) -> tuple[str | None, str | None, str | None, str | None, str | None, str | None]:
    if not enabled:
        return (None, None, None, None, None, None)
    if not environment.get(EXECUTORS_FILE_ENV, "").strip():
        raise RuntimeError("resolved operator executor authority is missing from runtime environment")
    cancelled, success = exercise_parallel_shared_plan_cancellation(
        frontend_base, repositories, access_token=access_token
    )
    failed, repaired = exercise_parallel_shared_plan_failure_repair(
        frontend_base, repositories, access_token=access_token
    )
    stale_rejected, stale_recovered = exercise_parallel_shared_plan_stale_preview_recovery(
        frontend_base, repositories, access_token=access_token
    )
    return cancelled, success, failed, repaired, stale_rejected, stale_recovered
