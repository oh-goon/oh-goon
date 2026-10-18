from __future__ import annotations

from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.strict_executor_authority import EXECUTORS_FILE_ENV
from oh_goon.services.certification_bootstrap import materialize_local_executor_inventory


def configure_local_runtime_executor(
    *, state: Path, environment: dict[str, str]
) -> tuple[bool, str | None]:
    """Bind local acceptance to a real strict executor without faking host execution."""
    operator_configured = bool(environment.get(EXECUTORS_FILE_ENV, "").strip())
    if operator_configured:
        return True, None
    try:
        state.mkdir(parents=True, exist_ok=True)
        inventory = materialize_local_executor_inventory(state=state, environment=environment)
    except (FleetPlanInvalid, OSError) as exc:
        return False, str(exc)
    environment[EXECUTORS_FILE_ENV] = str(inventory)
    return False, None
