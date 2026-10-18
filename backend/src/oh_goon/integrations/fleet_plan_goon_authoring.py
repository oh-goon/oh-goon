from __future__ import annotations

import re
from typing import Any

from oh_goon.core.errors import FleetPlanInvalid

_GOON_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def normalize_goon_authoring_header(
    payload: dict[str, Any], *, source: str, expected_name: str | None
) -> tuple[str, str, tuple[str, ...]]:
    """Normalize readable Goon metadata and explicit executable admission in-place."""

    name = _name(payload.pop("name", None), source=source, expected=expected_name)
    description = _description(payload.pop("description", ""), source=source)
    tools = _tools(payload.pop("tools", None), source=source)
    shell = _shell(payload.get("execution"), source=source)
    if shell is not None:
        tools = tuple(sorted(set((*tools, shell))))
    _strip_authoring_shell(payload)
    return name, description, tools


def apply_goon_tool_authority(
    normalized: dict[str, Any], *, tools: tuple[str, ...], source: str
) -> None:
    """Compile Goon-level positive admission into canonical per-Step capabilities."""

    if not tools:
        return
    steps = normalized.get("steps")
    if not isinstance(steps, list) or not steps:
        raise FleetPlanInvalid(f"Invalid Goon {source}: tools belong only to Atomic Goon Steps")
    if len(steps) != 1:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: top-level tools.allow is intentionally single-Step; "
            "split mixed executable authority into smaller Atomic Goons"
        )
    for index, step in enumerate(steps, start=1):
        _apply_step_tool_authority(step, tools=tools, source=source, index=index)


def _name(value: Any, *, source: str, expected: str | None) -> str:
    if value is None:
        return expected or "goon"
    if not isinstance(value, str) or _GOON_NAME.fullmatch(value) is None:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: name must use lowercase letters, numbers, and '-'"
        )
    if expected is not None and value != expected:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: authored name {value!r} must match package name {expected!r}"
        )
    return value


def _description(value: Any, *, source: str) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: description must be text no longer than 500 characters"
        )
    return value.strip()


def _tools(value: Any, *, source: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or set(value) != {"allow"}:
        raise FleetPlanInvalid(f"Invalid Goon {source}: tools must contain exactly one field: allow")
    allow = value.get("allow")
    if not isinstance(allow, list) or not allow or not all(isinstance(item, str) for item in allow):
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: tools.allow must be a non-empty list of executable names"
        )
    tools = tuple(allow)
    _validate_tool_names(tools, source=source)
    return tools


def _validate_tool_names(tools: tuple[str, ...], *, source: str) -> None:
    if len(tools) != len(set(tools)):
        raise FleetPlanInvalid(f"Invalid Goon {source}: tools.allow entries must be unique")
    if tools != tuple(sorted(tools)):
        raise FleetPlanInvalid(f"Invalid Goon {source}: tools.allow must use canonical sorted order")
    invalid = next((tool for tool in tools if _TOOL_NAME.fullmatch(tool) is None), None)
    if invalid is not None:
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: tools.allow contains invalid executable name {invalid!r}"
        )


def _shell(execution: Any, *, source: str) -> str | None:
    if not isinstance(execution, dict) or "shell" not in execution:
        return None
    shell = execution.get("shell")
    if shell not in {"bash", "sh"}:
        raise FleetPlanInvalid(f"Invalid Goon {source}: execution.shell must be 'bash' or 'sh'")
    # Selecting the shell is already explicit author intent. Compiling that same
    # selection into Step executable authority avoids forcing authors to state the
    # identical permission twice while keeping the resulting capability frozen.
    return str(shell)


def _strip_authoring_shell(payload: dict[str, Any]) -> None:
    execution = payload.get("execution")
    if not isinstance(execution, dict) or "shell" not in execution:
        return
    canonical = dict(execution)
    canonical.pop("shell")
    if canonical:
        payload["execution"] = canonical
    else:
        payload.pop("execution", None)


def _apply_step_tool_authority(
    step: Any,
    *,
    tools: tuple[str, ...],
    source: str,
    index: int,
) -> None:
    if not isinstance(step, dict) or step.get("kind") == "goon":
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: tools cannot be applied to composed Goon edge {index}"
        )
    capabilities = dict(step.get("capabilities") or {})
    if capabilities.get("tools") not in (None, [], ()):
        raise FleetPlanInvalid(
            f"Invalid Goon {source}: Step {index} must choose top-level tools.allow or "
            "internal capabilities.tools, never both"
        )
    capabilities["tools"] = list(tools)
    step["capabilities"] = capabilities
