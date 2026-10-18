"""T1: read-only canonical Plan/Goon input discovery through the public CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.cli import main
from oh_goon.cli_parser import build_parser
from oh_goon.cli_plan_definition_commands import _inputs
from oh_goon.cli_runtime_plans import run_plan_command
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def _plan(key: str = "python/quality") -> Path:
    return PlanTemplateStore().source(key).path


def test_cli_inspection_prints_canonical_json_and_exact_root_digest(monkeypatch, capsys):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("read-only CLI must never create runtime/SQLite"),
    )
    source = _plan()
    assert main(["plan", "inputs", str(source), "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema"] == "oh-goon.plan-input-inspection/v1"
    assert parsed["root_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert parsed["status"] == "contract_inspected"
    inputs = {item["name"]: item for item in parsed["inputs"]}
    assert set(inputs) == {"project", "dependency_group", "typechecker"}
    assert inputs["typechecker"]["choices"] == ["pyright", "ty"]
    assert inputs["typechecker"]["owner_paths"] == [["python/quality", "python/typecheck"]]
    assert len(inputs["project"]["owner_paths"]) == 3
    assert inputs["project"]["description"]


def test_inspection_human_and_json_are_repeatable_and_read_only(monkeypatch, capsys):
    import oh_goon.cli as cli

    source = _plan("oci/release-deploy")
    before = source.read_bytes()
    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("inspection must not initialize application"),
    )
    assert main(["plan", "inputs", str(source)]) == 0
    human = capsys.readouterr().out
    assert "Read-only input contracts" in human
    assert "--input NAME=VALUE" in human
    assert "CLI values override file values" in human
    assert "kubernetes/set-image" in human
    assert "kubernetes/rollout-verify" in human
    assert main(["plan", "inputs", str(source), "--json"]) == 0
    first = capsys.readouterr().out
    assert main(["plan", "inputs", str(source), "--json"]) == 0
    assert capsys.readouterr().out == first
    parsed = json.loads(first)
    namespace = next(i for i in parsed["inputs"] if i["name"] == "namespace")
    assert len(namespace["owner_paths"]) == 2
    assert source.read_bytes() == before


def test_catalog_all_plans_and_direct_goons_match_canonical_inspector(capsys):
    plans, goons = PlanTemplateStore(), GoonTemplateStore()
    resolver = FleetPlanResolver(goon_templates=goons)
    keys = [("plan", key, plans.source(key).path) for key in plans.keys()]
    keys += [("goon", key, goons.source(key).path) for key in goons.keys()]
    assert keys
    for kind, key, source in keys:
        assert main(["plan", "inputs", str(source), "--json"]) == 0, (kind, key)
        report = json.loads(capsys.readouterr().out)
        expected = resolver.inspect_inputs(source)
        assert [i["name"] for i in report["inputs"]] == [i.definition.name for i in expected], key
        assert [i["owner_paths"] for i in report["inputs"]] == [
            [list(p) for p in item.owner_paths] for item in expected
        ], key
        assert report["root_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
        assert report["status"] == "contract_inspected"


def test_inputs_for_no_parameter_composite_does_not_invent_values(capsys):
    source = GoonTemplateStore().source("security/cve-combined").path
    assert main(["plan", "inputs", str(source), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["inputs"] == []


def test_cli_parser_accepts_source_closure_flags_but_rejects_plain_inspection_values(
    monkeypatch, capsys
):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("plain input inspection cannot start runtime"),
    )
    parser = build_parser()
    for unexpected in (("--input", "coverage_threshold=90"), ("--extensions", "other.yml")):
        if unexpected[0] == "--extensions":
            with pytest.raises(SystemExit) as exc:
                parser.parse_args(["plan", "inputs", "plan.yml", *unexpected])
            assert exc.value.code == 2
            continue
        # --input is legitimate for the existing AG-01 source-closure mode,
        # but cannot influence ordinary value-free Plan/Goon inspection.
        parser.parse_args(["plan", "inputs", "plan.yml", *unexpected])
        assert main(["plan", "inputs", "plan.yml", *unexpected]) == 2
        assert "require --source-closure" in capsys.readouterr().out


def test_invalid_or_missing_source_rejects_without_runtime(monkeypatch, capsys, tmp_path):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("rejected inspection must not start runtime"),
    )
    missing = tmp_path / "missing.yml"
    assert main(["plan", "inputs", str(missing)]) == 2
    assert capsys.readouterr().out
    malformed = tmp_path / "plan.yml"
    malformed.write_text("version: 1\ngoons:\n- import: missing/child\n")
    assert main(["plan", "inputs", str(malformed), "--json"]) == 2
    assert capsys.readouterr().out


def test_direct_service_dispatch_and_presentation_use_the_same_inspection(capsys):
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    args = SimpleNamespace(command="plan", plan_command="inputs", plan=_plan(), as_json=True)
    service = SimpleNamespace(inspect_inputs_with_source=resolver.inspect_inputs_with_source)
    runtime = SimpleNamespace(plans=service)
    assert _inputs(runtime, args) == 0
    through_service = capsys.readouterr().out
    assert run_plan_command(runtime, args) == 0
    assert capsys.readouterr().out == through_service


def test_inspection_does_not_call_runtime_resolution_or_bind_inputs(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        pytest.fail("value resolution is not allowed for contract inspection")

    monkeypatch.setattr(FleetPlanResolver, "resolve", forbidden)
    monkeypatch.setattr(FleetPlanResolver, "resolve_source_graph", forbidden)
    assert main(["plan", "inputs", str(_plan()), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["inputs"]
