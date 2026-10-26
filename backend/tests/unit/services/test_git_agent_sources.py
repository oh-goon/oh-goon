from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.execution_lock_identity import build_execution_lock, verify_execution_lock
from oh_goon.core.models.git_sources import LockedExecutionSource
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.integrations.git_source_preparation import prepare_git_source
from oh_goon.runtime.goon_templates import GoonTemplateStore
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.agent_review import agent_review_case


def _commit(root: Path, *, initialize: bool = False) -> str:
    if initialize:
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "captured source",
        ],
        check=True,
    )
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    resolver, _, _, options = agent_review_case(tmp_path / "workflow")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    upstream = tmp_path / "upstream"
    root = upstream / "workflows/demo"
    package = root / "goons/agents/loop"
    package.mkdir(parents=True)
    store = GoonTemplateStore()
    composite = yaml.safe_load(store.source("agents/agentic-review-repair-loop").content)
    for edge in composite["goons"]:
        if edge.get("phase") == "review":
            edge["from"] = "../.."
    (package / "goon.yml").write_text(yaml.safe_dump(composite, sort_keys=False))
    (package / "README.md").write_text("# agents/loop\n")
    shutil.copytree(store.root / "agents/review", root / "goons/agents/review")
    (root / "plan.yml").write_text("goons:\n- import: agents/loop\n  from: goons\n")
    commit = _commit(upstream, initialize=True)
    pin = LockedExecutionSource(
        git=str(upstream),
        ref="main",
        path="workflows/demo",
        kind="plan",
        plan="plan.yml",
        commit=commit,
    )

    def captured():
        with prepare_git_source(pin.git, pin.ref, commit=pin.commit) as (checkout, _):
            authoring = checkout / pin.path
            return resolver.analyze_source(
                authoring / "plan.yml",
                containment_root=authoring,
                locked_sources=(pin,),
                plan_source=pin,
            )

    return resolver, captured, options, upstream, pin


def test_git_agent_capture_identity_survives_native_checkout_relocation(tmp_path, monkeypatch):
    resolver, captured, options, _, pin = _case(tmp_path, monkeypatch)
    first = captured()
    second = captured()
    assert first.root.path != second.root.path
    assert not first.root.path.exists() and not second.root.path.exists()
    plan, deliveries, _ = resolver.resolve_frozen_agents(
        first.root.path, source_graph=first, **options
    )
    again, repeated, _ = resolver.resolve_frozen_agents(
        second.root.path, source_graph=second, **options
    )
    verify_execution_lock(build_execution_lock(again), build_execution_lock(plan))
    assert repeated == deliveries
    assert first.immutable_source_closure_identity() == second.immutable_source_closure_identity()
    assert plan.execution_sources == (pin,)
    assert any(
        source.source.startswith(f"git:{pin.git}@{pin.commit}:") for source in plan.step_sources
    )


def test_git_agent_recapture_restores_exact_pin_and_holds_native_lease(tmp_path, monkeypatch):
    resolver, captured, options, upstream, pin = _case(tmp_path, monkeypatch)
    graph = captured()
    plan, deliveries, _ = resolver.resolve_frozen_agents(
        graph.root.path, source_graph=graph, **options
    )
    (upstream / pin.path / "plan.yml").write_text("goons:\n- python/quality\n")
    assert _commit(upstream) != pin.commit
    shutil.rmtree(tmp_path / "cache/oh-goon/skill-sources")

    from oh_goon.services import agent_authoring_sources

    analyzed = []
    original_analysis = resolver.analyze_source
    original_capture = agent_authoring_sources.capture_agent_sources

    def analyze(path, **kwargs):
        assert path.is_file()
        analyzed.append(path)
        return original_analysis(path, **kwargs)

    def capture(*args, **kwargs):
        assert analyzed and analyzed[-1].is_file(), "Git source lease ended before Agent capture"
        return original_capture(*args, **kwargs)

    monkeypatch.setattr(resolver, "analyze_source", analyze)
    monkeypatch.setattr(agent_authoring_sources, "capture_agent_sources", capture)
    again, repeated, _ = resolver.resolve_frozen_agents(
        graph.root.path,
        previous=deliveries,
        source_graph=graph,
        locked_sources=plan.execution_sources,
        **options,
    )
    assert again == plan and repeated == deliveries
    assert again.execution_sources == (pin,)
    assert len(analyzed) == 1 and not analyzed[0].exists()


@pytest.mark.parametrize("changed", ["plan", "goon", "skill"])
def test_retained_agent_graph_does_not_hide_mutable_source_changes(tmp_path, changed):
    _, path, _, options = agent_review_case(tmp_path)
    catalog = tmp_path / "catalog"
    shutil.copytree(GoonTemplateStore().root / "agents", catalog / "agents")
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore(catalog))
    graph = resolver.analyze_source(path)
    plan, deliveries, _ = resolver.resolve_frozen_agents(path, source_graph=graph, **options)
    again, _, _ = resolver.resolve_frozen_agents(
        path, previous=deliveries, source_graph=graph, **options
    )
    assert again == plan
    selected = {
        "plan": path,
        "goon": catalog / "agents/review/goon.yml",
        "skill": tmp_path / "skills/repair/SKILL.md",
    }[changed]
    selected.write_text(selected.read_text() + "\n# Authored change\n")
    with pytest.raises(FleetPlanInvalid):
        resolver.resolve_frozen_agents(path, previous=deliveries, source_graph=graph, **options)


def test_cancelled_git_recapture_stops_before_native_acquisition(tmp_path, monkeypatch):
    resolver, captured, options, _, _ = _case(tmp_path, monkeypatch)
    graph = captured()
    _, deliveries, _ = resolver.resolve_frozen_agents(
        graph.root.path, source_graph=graph, **options
    )
    from oh_goon.integrations import git_source_preparation

    monkeypatch.setattr(
        git_source_preparation,
        "prepare_git_source",
        lambda *a, **k: pytest.fail("Cancelled recapture attempted native Git acquisition"),
    )
    with pytest.raises(OperationCancelledError):
        resolver.resolve_frozen_agents(
            graph.root.path,
            previous=deliveries,
            source_graph=graph,
            cancel_requested=lambda: True,
            **options,
        )


def _command_package(root: Path, relative: str, output: str) -> None:
    package = root / relative
    package.mkdir(parents=True)
    (package / "README.md").write_text("# agents/demo\n")
    (package / "goon.yml").write_text(f"steps:\n- run: printf {output}\n")


def test_git_ref_aliases_preserve_each_requested_source(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    _command_package(upstream, "goons/agents/demo", "SAME")
    commit = _commit(upstream, initialize=True)
    subprocess.run(["git", "-C", str(upstream), "branch", "alias"], check=True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    plan = tmp_path / "plan.yml"
    plan.write_text(
        yaml.safe_dump(
            {
                "goons": [
                    {
                        "import": "agents/demo",
                        "as": ref,
                        "from": {"git": str(upstream), "ref": ref, "path": "goons/agents/demo"},
                    }
                    for ref in ("main", "alias")
                ]
            }
        )
    )
    graph = FleetPlanResolver().analyze_source(
        plan, acquire_sources=True, allow_source_network=True
    )
    selected = [graph.goon(graph.root.path, edge) for edge in graph.root.compiled.steps]
    assert [node.git_source.ref for node in selected] == ["main", "alias"]
    assert {node.git_source.commit for node in selected} == {commit}
    assert selected[0].local_source == selected[1].local_source


@pytest.mark.skipif(
    os.name != "posix", reason="The reproduction uses valid POSIX Git path coordinates"
)
def test_display_uri_collision_cannot_replace_explicit_git_source(tmp_path, monkeypatch):
    second = tmp_path / "second"
    _command_package(second, "goons/agents/demo", "SECOND")
    second_commit = _commit(second, initialize=True)
    first = tmp_path / "first"
    first_path = f"prefix@{second_commit}:goons/agents/demo"
    _command_package(first, first_path, "FIRST")
    first_commit = _commit(first, initialize=True)
    second_origin = Path(f"{first}@{first_commit}:prefix")
    shutil.move(str(second), second_origin)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    plan = tmp_path / "plan.yml"
    plan.write_text(
        yaml.safe_dump(
            {
                "goons": [
                    {
                        "import": "agents/demo",
                        "as": alias,
                        "from": {"git": str(origin), "ref": "main", "path": path},
                    }
                    for alias, origin, path in (
                        ("first", first, first_path),
                        ("second", second_origin, "goons/agents/demo"),
                    )
                ]
            }
        )
    )
    resolver = FleetPlanResolver()
    graph = resolver.analyze_source(plan, acquire_sources=True, allow_source_network=True)
    selected = [graph.goon(graph.root.path, edge) for edge in graph.root.compiled.steps]
    assert selected[0].local_source == selected[1].local_source, (
        "Native coordinates intentionally share one display URI"
    )
    assert [node.git_source.git for node in selected] == [str(first), str(second_origin)]
    resolved = resolver.resolve_source_graph(graph)
    assert [step.argv for step in resolved.steps] == [("printf", "FIRST"), ("printf", "SECOND")]
