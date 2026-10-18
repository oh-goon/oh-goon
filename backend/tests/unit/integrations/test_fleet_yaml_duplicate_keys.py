from __future__ import annotations

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import parse_goon_template, parse_plan_template


def test_plan_rejects_duplicate_nested_input_key() -> None:
    with pytest.raises(FleetPlanInvalid, match="duplicate key 'pattern'"):
        parse_plan_template(
            """version: 1
inputs:
  image:
    type: string
    pattern: first
    pattern: second
goons:
- import: kubernetes/set-image
""",
            source="duplicate-plan.yml",
        )


def test_goon_rejects_duplicate_step_key() -> None:
    with pytest.raises(FleetPlanInvalid, match="duplicate key 'run'"):
        parse_goon_template(
            """version: 1
steps:
- id: check
  run: "true"
  run: "false"
""",
            source="duplicate-goon.yml",
        )
