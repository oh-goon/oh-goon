"""Explicit input exports retain exact values and the original execution lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.core.models.fleet_plan import ResolvedCommandStep
from oh_goon.core.models.fleet_plan_values import LiteralEnvironmentPart
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _goon(root: Path, *, source: str = "payload") -> Path:
    path = root / "goon.yml"
    path.write_text(
        "inputs:\n  payload: {type: string}\n"
        "env:\n  PAYLOAD:\n    from: input\n"
        f"    name: {source}\n"
        "  LITERAL: {value: '${{ inputs.payload }}'}\n"
        "steps:\n- run: 'true'\n  imports: {env: [PAYLOAD, LITERAL]}\n"
    )
    return path


def test_named_input_export_is_exact_data_and_changes_execution_identity(tmp_path: Path) -> None:
    path = _goon(tmp_path)
    resolver = FleetPlanResolver()
    first_value = '{"description":"literal ${{ env.HOST_SECRET }} data"}'
    first = resolver.resolve(path, inputs={"payload": first_value})
    second = resolver.resolve(path, inputs={"payload": '{"description":"different"}'})
    first_step, second_step = first.steps[0], second.steps[0]
    assert isinstance(first_step, ResolvedCommandStep) and isinstance(
        second_step, ResolvedCommandStep
    )
    exported = {item.name: item.parts for item in first_step.environment}
    assert exported["PAYLOAD"] == (LiteralEnvironmentPart(value=first_value),)
    assert exported["LITERAL"] == (LiteralEnvironmentPart(value="${{ inputs.payload }}"),)
    assert first_step.argv == second_step.argv == ("true",)
    assert first.plan_id != second.plan_id
    assert build_execution_lock(first).digest != build_execution_lock(second).digest


def test_missing_named_input_export_refuses_during_resolution(tmp_path: Path) -> None:
    path = _goon(tmp_path, source="missing")
    with pytest.raises(FleetPlanInvalid, match="undeclared input"):
        FleetPlanResolver().resolve(path, inputs={"payload": "reviewed"})
