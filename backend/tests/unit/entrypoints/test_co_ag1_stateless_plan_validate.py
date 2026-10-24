"""Configuration validation uses the canonical owner without runtime startup."""

from __future__ import annotations

import json

import pytest

from oh_goon.cli import main
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.run_configuration import write_run_configuration


def _no_runtime(monkeypatch):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("diagnostic started runtime")
    )


def test_shipped_selection_validation_matches_canonical_steps(tmp_path, monkeypatch, capsys):
    _no_runtime(monkeypatch)
    config = write_run_configuration(tmp_path, monkeypatch, inputs={"typechecker": "ty"})
    expected = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve(
        PlanTemplateStore().source("python/quality").path, inputs={"typechecker": "ty"}
    )
    assert main(["config", "validate", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "inputs-valid"
    assert [step["id"] for step in report["steps"]] == [step.step_id for step in expected.steps]
    assert report["proof"]["admission"] == "not-started"
    assert report["proof"]["execution"] == "not-started"
    assert not (tmp_path / "goon.lock").exists()


def test_invalid_plan_rejects_without_runtime_source_mutation_or_lock(
    tmp_path, monkeypatch, capsys
):
    _no_runtime(monkeypatch)
    config = write_run_configuration(
        tmp_path, monkeypatch, plan_content="version: 1\ngoons:\n- missing/does-not-exist\n"
    )
    before = (tmp_path / "plan.yml").read_bytes()
    assert main(["config", "validate", str(config), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert (tmp_path / "plan.yml").read_bytes() == before
    assert not (tmp_path / "goon.lock").exists()


def test_unknown_input_refuses_without_runtime_or_value_echo(tmp_path, monkeypatch, capsys):
    _no_runtime(monkeypatch)
    config = write_run_configuration(tmp_path, monkeypatch, inputs={"not-owned": "SECRET_CANARY"})
    assert main(["config", "validate", str(config), "--json"]) == 2
    output = capsys.readouterr().out
    assert "SECRET_CANARY" not in output
    assert not (tmp_path / "goon.lock").exists()


def test_missing_configuration_rejects_without_runtime(tmp_path, monkeypatch, capsys):
    _no_runtime(monkeypatch)
    assert main(["config", "validate", str(tmp_path / "absent.yml")]) == 2
    assert capsys.readouterr().out
