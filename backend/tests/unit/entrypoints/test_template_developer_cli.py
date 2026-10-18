"""Catalog discovery and caller overrides use existing owners without runtime startup."""

import json

import pytest

from oh_goon.cli import main
from oh_goon.core.execution_lock_identity import build_execution_lock
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.execution_lock_persistence import load_execution_lock
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver


def test_catalog_and_source_are_exact_and_do_not_start_runtime(monkeypatch, capsys):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli,
        "_build_application_runtime",
        lambda **kwargs: pytest.fail("discovery must not create runtime"),
    )
    assert main(["templates", "list", "--json"]) == 0
    entries = json.loads(capsys.readouterr().out)["templates"]
    assert {(item["kind"], item["template_key"]) for item in entries} == {
        *(("plan", key) for key in PlanTemplateStore().keys()),
        *(("goon", key) for key in GoonTemplateStore().keys()),
    }
    for kind, store in (("plan", PlanTemplateStore()), ("goon", GoonTemplateStore())):
        assert main(["templates", "show", "python/quality", "--kind", kind]) == 0
        assert capsys.readouterr().out == store.source("python/quality").content
    assert main(["templates", "show", "python/not-present", "--kind", "plan"]) == 2
    assert not capsys.readouterr().out


def test_lock_and_routing_use_the_same_cli_override_and_one_file_capture(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "plan.yml"
    path.write_text(PlanTemplateStore().source("python/quality").content)
    (tmp_path / "values.yml").write_text(
        "inputs:\n  typechecker: not-a-checker\n  project: backend\n"
    )
    flags = ["--inputs-file", "values.yml", "--input", "typechecker=ty"]
    assert main(["lock", str(path), *flags]) == 0
    capsys.readouterr()
    expected = FleetPlanResolver(goon_templates=GoonTemplateStore()).resolve(
        path, inputs={"project": "backend", "typechecker": "ty"}
    )
    assert load_execution_lock(tmp_path / "goon.lock") == build_execution_lock(expected)
    assert main(["plan", "explain", str(path), *flags, "--routes", "--json"]) == 0
    inputs = {item["name"]: item for item in json.loads(capsys.readouterr().out)["inputs"]}
    assert inputs["typechecker"]["origin"] == {"kind": "supplied"}
    assert inputs["project"]["origin"]["kind"] == "inputs_file"
    assert (
        main(
            [
                "plan",
                "validate",
                str(path),
                "--inputs-file",
                "values.yml",
                "--input",
                "typechecker=invalid",
            ]
        )
        == 2
    )
