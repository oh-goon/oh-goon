"""T3 command selection: versioned new presentation; legacy explain untouched."""

from __future__ import annotations

import json
from types import SimpleNamespace

from oh_goon.cli_parser import build_parser
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _args(*tail: str):
    return build_parser().parse_args(["plan", "explain", *tail])


def test_parser_explain_routes_opt_in_does_not_break_existing_flags():
    old = _args("demo/plan.yml", "--json")
    assert old.routes is False
    new = _args("demo/plan.yml", "--routes", "--json")
    assert new.routes is True and new.as_json is True


def test_routes_cli_json_uses_single_service_report_and_stays_redacted(capsys):
    plan = PlanTemplateStore().source("python/quality").path
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    runtime = SimpleNamespace(plans=SimpleNamespace(explain_routing=resolver.explain_routing))
    assert (
        run_plan_command(
            runtime, _args(str(plan), "--routes", "--json", "--input", "typechecker=ty")
        )
        == 0
    )
    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert parsed["schema"] == "oh-goon.plan-input-routing/v1"
    assert parsed["status"] == "resolved_not_executed"
    assert (
        next(item for item in parsed["inputs"] if item["name"] == "typechecker")["origin"]["kind"]
        == "supplied"
    )
    assert all("value" not in item for item in parsed["bindings"])


def test_routes_cli_human_mentions_occurrence_not_an_implicit_goon_only(capsys):
    plan = PlanTemplateStore().source("python/quality").path
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    runtime = SimpleNamespace(plans=SimpleNamespace(explain_routing=resolver.explain_routing))
    assert run_plan_command(runtime, _args(str(plan), "--routes")) == 0
    printed = capsys.readouterr().out
    assert "python-quality.python-test" in printed
    assert "resolved_not_executed" in printed
    assert "root file only" in printed


def test_routes_invalid_input_is_nonzero_and_cannot_report_readiness(capsys):
    plan = PlanTemplateStore().source("python/quality").path
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    runtime = SimpleNamespace(plans=SimpleNamespace(explain_routing=resolver.explain_routing))
    assert (
        run_plan_command(
            runtime, _args(str(plan), "--routes", "--json", "--input", "typechecker=invalid")
        )
        == 2
    )
    assert "resolved_not_executed" not in capsys.readouterr().out


def test_routes_cli_file_identity_is_from_collector_not_undocumented_read(
    tmp_path, monkeypatch, capsys
):
    plan = PlanTemplateStore().source("python/quality").path
    (tmp_path / "values.yml").write_text("inputs:\n  typechecker: ty\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    runtime = SimpleNamespace(plans=SimpleNamespace(explain_routing=resolver.explain_routing))
    args = _args(str(plan), "--routes", "--json", "--inputs-file", "values.yml")
    assert run_plan_command(runtime, args) == 0
    first = json.loads(capsys.readouterr().out)
    assert (
        next(item for item in first["inputs"] if item["name"] == "typechecker")["origin"]["kind"]
        == "inputs_file"
    )
    assert (
        next(item for item in first["inputs"] if item["name"] == "typechecker")["origin"]["path"]
        == "values.yml"
    )
    assert (
        len(
            next(item for item in first["inputs"] if item["name"] == "typechecker")["origin"][
                "sha256"
            ]
        )
        == 64
    )
    # Bound Namespace repeats use one file snapshot, never silently refresh it.
    (tmp_path / "values.yml").write_text("inputs:\n  typechecker: 99\n", encoding="utf-8")
    assert run_plan_command(runtime, args) == 0
    again = json.loads(capsys.readouterr().out)
    assert first == again
