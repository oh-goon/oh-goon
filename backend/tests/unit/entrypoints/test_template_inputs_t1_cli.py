"""The selected config projects original contracts without native or Run effects."""

from __future__ import annotations

import hashlib
import json
import shutil

import pytest

from oh_goon.cli import main
from oh_goon.core.models.fleet_plan import AgentPlanStep, CompiledGoonImportStep
from oh_goon.integrations.python_goon_projects import materialize_python_goon_project
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.runtime.plan_templates import PlanTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.run_configuration import write_run_configuration


def test_every_catalog_contract_is_discoverable_from_selected_config(tmp_path, monkeypatch, capsys):
    import oh_goon.cli as cli

    monkeypatch.setattr(
        cli, "_build_application_runtime", lambda **kw: pytest.fail("inspection started runtime")
    )
    plans, goons = PlanTemplateStore(), GoonTemplateStore()
    resolver = FleetPlanResolver(goon_templates=goons)
    sources = [("plan", key, plans.source(key)) for key in plans.keys()]
    sources += [("goon", key, goons.source(key)) for key in goons.keys()]
    for kind, key, snapshot in sources:
        graph = resolver.analyze_source(snapshot.path)
        native, phases = False, False

        def selected_groups(node, graph=graph):
            nonlocal native, phases
            for step in node.compiled.steps:
                if isinstance(step, CompiledGoonImportStep):
                    phases = phases or step.phase is not None
                    selected_groups(graph.goon(node.path, step))
                elif isinstance(step, AgentPlanStep):
                    native = native or step.harness == "native"

        selected_groups(graph.root)
        config = write_run_configuration(
            tmp_path / kind / key.replace("/", "-"),
            monkeypatch,
            source={"kind": "catalog-plan", "name": key} if kind == "plan" else None,
            goon_content=snapshot.content if kind == "goon" else None,
            agent=graph.has_agent_source_contract(),
            native_agent=native,
            repair=phases,
        )
        if graph.root.project is not None:
            project = graph.root.project
            frozen = tmp_path / "captured-project"
            materialize_python_goon_project(project.archive, project.identity, frozen)
            shutil.copytree(frozen, config.parent / "repository", dirs_exist_ok=True)
        assert main(["config", "inspect", str(config), "--json"]) == 0, (kind, key)
        report = json.loads(capsys.readouterr().out)
        expected = resolver.inspect_inputs(snapshot.path)
        assert [item["name"] for item in report["inputs"]] == [
            item.definition.name for item in expected
        ], key
        assert [item["owners"] for item in report["inputs"]] == [
            [list(path) for path in item.owner_paths] for item in expected
        ], key
        assert report["source"]["sha256"] == hashlib.sha256(snapshot.content.encode()).hexdigest()
        assert report["proof"]["admission"] == "not-started"
        assert report["proof"]["execution"] == "not-started"
        assert not config.with_name("goon.lock").exists()


@pytest.mark.parametrize("kind", ["inspect", "validate"])
def test_missing_or_malformed_source_refuses_without_lock(tmp_path, monkeypatch, capsys, kind):
    config = write_run_configuration(
        tmp_path, monkeypatch, source={"kind": "file-plan", "path": "missing.yml"}
    )
    assert main(["config", kind, str(config), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "rejected"
    assert not config.with_name("goon.lock").exists()


@pytest.mark.parametrize(
    "imports,accepted,unrouted",
    [
        ("- python/quality\n- python/profile\n", ["typechecker"], ["project", "dependency_group"]),
        (
            "- python/typecheck\n- python/typecheck\n",
            [],
            ["project", "dependency_group", "typechecker"],
        ),
        (
            "- import: python/typecheck\n  with: {project: PRIVATE_PROJECT, typechecker: ty}\n"
            "- import: python/typecheck\n  with: {project: PRIVATE_PROJECT, typechecker: ty}\n",
            [],
            ["dependency_group"],
        ),
    ],
)
def test_ambiguous_contracts_are_visible_and_fixed_literals_redacted(
    tmp_path, monkeypatch, capsys, imports, accepted, unrouted
):
    config = write_run_configuration(
        tmp_path, monkeypatch, plan_content=f"version: 1\ngoons:\n{imports}"
    )
    assert main(["config", "inspect", str(config), "--json"]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert [item["name"] for item in report["inputs"]] == accepted
    assert report["unrouted_inputs"] == unrouted
    assert "PRIVATE_PROJECT" not in output
    assert report["proof"]["admission"] == "not-started"


def test_explicit_recipient_routing_removes_ambiguity(tmp_path, monkeypatch, capsys):
    config = write_run_configuration(
        tmp_path,
        monkeypatch,
        plan_content="""version: 1
goons:
- import: python/typecheck
  pass: [project, dependency_group, typechecker]
- python/typecheck
""",
    )
    assert main(["config", "inspect", str(config), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["unrouted_inputs"] == []
    assert {item["name"] for item in report["inputs"]} == {
        "project",
        "dependency_group",
        "typechecker",
    }
