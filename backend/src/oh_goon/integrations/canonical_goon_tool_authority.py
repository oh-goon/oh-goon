from __future__ import annotations

import json
from pathlib import Path

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    CompiledExecutionTemplate,
    CompiledGoonImportStep,
    PlanStep,
)

_SCHEMA = 1
_AUTHORITY_FILE = "canonical_goon_tool_authority.json"


def authority_path() -> Path:
    return Path(__file__).resolve().parents[1] / _AUTHORITY_FILE


def load_canonical_goon_tool_authority(
    path: Path | None = None,
) -> dict[str, dict[str, tuple[str, ...]]]:
    path = (path or authority_path()).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetPlanInvalid(f"Cannot load canonical Goon tool authority {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
        raise FleetPlanInvalid(f"Canonical Goon tool authority must use schema {_SCHEMA}: {path}")
    goons = payload.get("goons")
    if not isinstance(goons, dict):
        raise FleetPlanInvalid(f"Canonical Goon tool authority goons must be an object: {path}")
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for key, raw_steps in goons.items():
        if not isinstance(key, str) or not isinstance(raw_steps, dict):
            raise FleetPlanInvalid(f"Invalid canonical Goon tool authority entry: {key!r}")
        steps: dict[str, tuple[str, ...]] = {}
        for step_id, raw_tools in raw_steps.items():
            if (
                not isinstance(step_id, str)
                or not isinstance(raw_tools, list)
                or any(not isinstance(tool, str) or not tool for tool in raw_tools)
            ):
                raise FleetPlanInvalid(
                    f"Invalid canonical Goon tool authority entry: {key}/{step_id!r}"
                )
            tools = tuple(raw_tools)
            if tools != tuple(sorted(set(tools))):
                raise FleetPlanInvalid(
                    f"Canonical Goon tool authority must be unique and sorted: {key}/{step_id}"
                )
            steps[step_id] = tools
        result[key] = steps
    return result


def apply_canonical_goon_tool_authority(
    compiled: CompiledExecutionTemplate,
    *,
    template_key: str,
    authority: dict[str, dict[str, tuple[str, ...]]] | None = None,
) -> CompiledExecutionTemplate:
    """Transport generated exact tool need into canonical runtime Step authority.

    The generated file is derived evidence, not permission. Static admission proves it
    matches current Make/npm/native requirement discovery and stays within the
    independent permission ceiling before the repository can certify or release it.
    """

    authority_map = load_canonical_goon_tool_authority() if authority is None else authority
    template_authority = authority_map.get(template_key)
    if template_authority is None:
        return compiled
    steps: list[PlanStep] = []
    seen: set[str] = set()
    for step in compiled.steps:
        if isinstance(step, CompiledGoonImportStep):
            steps.append(step)
            continue
        tools = template_authority.get(step.step_id)
        if tools is None:
            steps.append(step)
            continue
        capabilities = getattr(step, "capabilities", None)
        if capabilities is None:
            if tools:
                raise FleetPlanInvalid(
                    "Canonical Goon tool authority cannot attach child-tool authority to "
                    f"typed Step without capabilities: {template_key}/{step.step_id}"
                )
            seen.add(step.step_id)
            steps.append(step)
            continue
        seen.add(step.step_id)
        capabilities = capabilities.model_copy(update={"tools": tools})
        steps.append(step.model_copy(update={"capabilities": capabilities}))
    stale = tuple(sorted(set(template_authority) - seen))
    if stale:
        raise FleetPlanInvalid(
            "Canonical Goon tool authority references missing Steps for "
            f"{template_key}: {', '.join(stale)}"
        )
    return compiled.model_copy(update={"steps": tuple(steps)})
