"""UX-A: authoring evidence uses the canonical stores and never decides execution."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.integrations.fleet_plan_compilation import (
    compile_goon_document,
    compile_plan_document,
    parse_fleet_yaml_document,
)

ROOT = Path(__file__).resolve().parents[4]


def test_frozen_shipped_catalog_compiles_and_occurrences_do_not_change() -> None:
    # The tool delegates *all* source acceptance to original catalog stores and compiler.
    collect = runpy.run_path(
        str(ROOT / "backend/src/oh_goon/repository_tooling/template_ux_a_inventory.py")
    )["collect"]
    actual = collect(ROOT)
    baseline = json.loads(
        (ROOT / "backend/tests/fixtures/template_authoring/catalog.json").read_text()
    )
    assert actual == baseline
    assert actual["metrics"]["goon_packages"] == 41
    assert actual["metrics"]["plan_packages"] == 24
    assert actual["metrics"]["unresolved_readme_links"] == 0
    assert len(actual["packages"]) == 65
    assert all(len(x["root_source_sha256"]) == 64 for x in actual["packages"])
    assert all(x["readme_present"] for x in actual["packages"])


@pytest.mark.parametrize(
    "kind,yaml_text",
    [
        ("plan", "version: 1\ninputs: []\nsteps:\n- run: echo hello\n"),
        ("goon", "version: 1\ninputs:\n- coverage_threshold\nsteps:\n- run: echo hello\n"),
        (
            "goon",
            "version: 1\ninputs:\n  coverage_threshold:\n    type: integer\ngoons:\n- python/test\n",
        ),
    ],
)
def test_invalid_authoring_shaped_data_rejected_by_original_compiler(kind, yaml_text):
    parsed = parse_fleet_yaml_document(yaml_text, source="ux-a-negative")
    compiler = compile_plan_document if kind == "plan" else compile_goon_document
    with pytest.raises(FleetPlanInvalid):
        compiler(parsed, source="ux-a-negative")


def test_authoring_inventory_is_readonly_not_an_authority_owner():
    code = (ROOT / "backend/src/oh_goon/repository_tooling/template_ux_a_inventory.py").read_text()
    assert "GoonTemplateStore" in code and "PlanTemplateStore" in code
    assert "store.source(key)" in code
    for forbidden in (
        "FleetRunLifecycle",
        "FleetPlanResolver(",
        "start_run(",
        "RUN_STARTED",
        "os.system",
        "subprocess.run",
        "yaml.safe_load",
    ):
        assert forbidden not in code
