from __future__ import annotations

import subprocess
import sys

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


@pytest.mark.process
def test_shared_acyclic_yaml_alias_graph_is_checked_without_expansion() -> None:
    source = "node0: &node0 [leaf]\n" + "".join(
        f"node{index}: &node{index} [*node{index - 1}, *node{index - 1}]\n"
        for index in range(1, 46)
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "from oh_goon.integrations.fleet_plan_compilation import parse_fleet_yaml_document\n"
                "document = parse_fleet_yaml_document(sys.stdin.read(), source='shared.yml')\n"
                "assert document.payload['node45'][0] is document.payload['node44']\n"
            ),
        ],
        input=source,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
