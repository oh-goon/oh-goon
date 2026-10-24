"""Configuration reports preserve occurrence routing and captured selection identity."""

from __future__ import annotations

import json

from oh_goon.cli import main
from oh_goon.cli_configuration import prepare_configuration
from oh_goon.cli_parser import build_parser
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from oh_goon.services.run_configuration_inspection import inspect_run_configuration
from tests.factories.run_configuration import write_run_configuration


def test_repeated_imports_have_distinct_occurrence_routes(tmp_path, monkeypatch, capsys):
    config = write_run_configuration(
        tmp_path,
        monkeypatch,
        plan_content="""version: 1
goons:
- import: python/typecheck
  as: first
  pass: [project, dependency_group, typechecker]
- import: python/typecheck
  as: second
""",
        inputs={"typechecker": "ty"},
    )
    assert main(["config", "inspect", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    occurrences = report["occurrences"]
    assert [item["occurrence"] for item in occurrences] == [["first"], ["second"]]
    assert all(item["goon"] == "python/typecheck" for item in occurrences)
    assert report["inputs"][2]["origin"] == "run.yml"
    assert report["proof"]["execution"] == "not-started"
    assert not config.with_name("goon.lock").exists()


def test_one_captured_config_is_not_reinterpreted_during_report(tmp_path, monkeypatch):
    config = write_run_configuration(tmp_path, monkeypatch, inputs={"typechecker": "ty"})
    args = build_parser().parse_args(["config", "inspect", str(config), "--json"])
    prepare_configuration(args)
    captured = args._run_configuration
    before = inspect_run_configuration(
        captured, FleetPlanResolver(goon_templates=GoonTemplateStore())
    )
    config.write_text("version: 999\n", encoding="utf-8")
    after = inspect_run_configuration(
        captured, FleetPlanResolver(goon_templates=GoonTemplateStore())
    )
    assert after == before
    assert next(item for item in after["inputs"] if item["name"] == "typechecker")["value"] == "ty"
