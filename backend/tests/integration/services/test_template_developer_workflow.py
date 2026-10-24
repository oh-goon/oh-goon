"""Documented authoring journeys resolve actual sources and execute native tools."""

import json
import os
import select
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.core.product_identity import repository_id

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "template-certification/fixtures/python/standard"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


@pytest.fixture(scope="module")
def native_fixture_uv_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Prove the fixture can bootstrap without an ambient developer package cache."""
    return tmp_path_factory.mktemp("native-fixture-uv-cache")


def _prepare_fixture_project(project: Path) -> None:
    result = subprocess.run(
        ["uv", "sync", "--locked", "--project", str(project), "--group", "dev"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (result.stderr or result.stdout)[-8192:]


@pytest.mark.parametrize("project", [".", "my backend"])
def test_native_project_configuration_reports_and_extensions(
    tmp_path, monkeypatch, capsys, project, native_fixture_uv_cache
):
    monkeypatch.setenv("UV_CACHE_DIR", str(native_fixture_uv_cache))
    repository = tmp_path / "repositories/team/app"
    target = repository if project == "." else repository / project
    shutil.copytree(
        FIXTURE, target, ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache")
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    _prepare_fixture_project(target)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "plan.yml"
    # Start with the real existing Plan; ordinary verification is its default.
    from oh_goon.runtime.plan_templates import PlanTemplateStore

    source = PlanTemplateStore().source("python/quality").content
    path.write_text(source)
    local = tmp_path / "goons/general/project-check"
    local.mkdir(parents=True)
    (local / "README.md").write_text("# general/project-check\n\nCompile the selected project.\n")
    (local / "goon.yml").write_text(f"""uv:
  project: '{project}'
  group: dev
steps:
- run: uv run python -m compileall -q src
""")
    # Add one shipped capability and one project-owned capability to the same Plan.
    with path.open("a") as stream:
        stream.write(f"""- import: python/profile
  with: {{project: '{project}', dependency_group: dev}}
- import: general/project-check
  from: ./goons
""")
    checker = "ty" if project == "." else "pyright"
    from oh_goon.runtime.league_folders import LeagueFolderStore

    LeagueFolderStore().replace((tmp_path / "repositories",))
    selection = tmp_path / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": repository_id(repository)},
                "inputs": {"project": project, "typechecker": checker},
            }
        )
    )
    assert main(["lock", str(selection)]) == 0
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(tmp_path / "repositories")
        capsys.readouterr()
        assert main(["run", str(selection), "--json"]) == 0
        receipt = json.loads(capsys.readouterr().out)
        result = runtime.runs.get(receipt["fleet_run_id"])
        steps = result.operations[0].evidence.plan.steps
        assert all(step.status == "COMPLETED" and step.returncode == 0 for step in steps), result
        assert (target / "junit.xml").is_file()
        assert "tests=" in (target / "junit.xml").read_text()
        report = next(step for step in steps if step.step_id.endswith(".pytest")).artifacts[0]
        assert report.status == "PRESENT"
        assert report.path == ("junit.xml" if project == "." else f"{project}/junit.xml")
        assert (target / "pytest-profile.html").is_file()
        assert result.game_tape.status.value == "PASSED"
        # A real failed test must remain a failure through the same admitted path.
        (target / "tests/test_workload.py").write_text("def test_failure():\n    assert False\n")
        assert main(["lock", str(selection)]) == 0
        capsys.readouterr()
        assert main(["run", str(selection), "--json"]) == 1
        failed_receipt = json.loads(capsys.readouterr().out)
        failed = runtime.runs.get(failed_receipt["fleet_run_id"])
        pytest_step = next(
            step
            for step in failed.operations[0].evidence.plan.steps
            if step.step_id.endswith(".pytest")
        )
        assert pytest_step.status == "FAILED" and pytest_step.returncode == 1
        assert failed.game_tape.status.value != "PASSED"
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_local_custom_goon_cancels_a_real_native_process_before_next_step(
    tmp_path, monkeypatch, native_fixture_uv_cache
):
    monkeypatch.setenv("UV_CACHE_DIR", str(native_fixture_uv_cache))
    repository = tmp_path / "repositories/team/app"
    shutil.copytree(
        FIXTURE,
        repository,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    _prepare_fixture_project(repository)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    ready, hold = tmp_path / "ready", tmp_path / "hold"
    os.mkfifo(ready)
    os.mkfifo(hold)
    marker = repository / "unexpected.txt"
    (repository / "wait.py").write_text("""import os
import sys
from pathlib import Path

with Path(sys.argv[1]).open('wb', buffering=0) as stream:
    stream.write(str(os.getpid()).encode())
with Path(sys.argv[2]).open('rb') as stream:
    stream.read(1)
Path('unexpected.txt').write_text('must not reach here')
""")
    next_marker = repository / "unexpected-next-step.txt"
    (repository / "complete.py").write_text(
        "from pathlib import Path\nPath('unexpected-next-step.txt').write_text('ran')\n"
    )
    directory = tmp_path / "goons/general/wait"
    directory.mkdir(parents=True)
    (directory / "README.md").write_text("# general/wait\n\nWait for explicit release.\n")
    (directory / "goon.yml").write_text(f"""uv: {{group: dev}}
steps:
- run: uv run python wait.py {shlex.quote(str(ready))} {shlex.quote(str(hold))}
- run: uv run python complete.py
""")
    path = tmp_path / "plan.yml"
    path.write_text("goons:\n- import: general/wait\n  from: goons\n")
    from oh_goon.runtime.league_folders import LeagueFolderStore
    from oh_goon.services.run_configuration import load_run_configuration

    LeagueFolderStore().replace((tmp_path / "repositories",))
    selection = tmp_path / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": repository_id(repository)},
            }
        )
    )
    assert main(["lock", str(selection)]) == 0
    captured = load_run_configuration(selection)
    runtime = ApplicationRuntime.build(standard_local=True)
    started = None
    try:
        runtime.locations.add(tmp_path / "repositories")
        with os.fdopen(os.open(ready, os.O_RDWR | os.O_NONBLOCK), "rb", buffering=0) as channel:
            started = runtime.plans.run(
                path,
                repository_ids={repository_id(repository)},
                lock_path=captured.lock_path,
                caller_input_file=captured.authority(),
                external_artifacts=captured.external_artifacts(),
            )
            assert select.select([channel], [], [], 15)[0], runtime.runs.get(started.fleet_run_id)
            process_id = int(channel.read(100))
            runtime.runs.cancel(started.fleet_run_id)
            cancelled = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=15)
        assert cancelled.run.status.value == "CANCELLED"
        assert not marker.exists()
        assert not next_marker.exists()
        with pytest.raises(ProcessLookupError):
            os.kill(process_id, 0)
    finally:
        if started is not None and not runtime.runs.get(started.fleet_run_id).run.status.terminal:
            runtime.runs.cancel(started.fleet_run_id)
        runtime.shutdown(wait_for_runs=True)


def test_actual_local_search_example_projects_inputs_and_executes_its_frozen_tool(
    tmp_path: Path,
) -> None:
    """Prove the checked-in fixture's source/Step boundary, without claiming Fleet admission."""
    from oh_goon.core.models.fleet_plan import CompiledGoonImportStep
    from oh_goon.integrations.fleet_plan_step_execution import (
        StepRuntime,
        execute_direct_step,
        prepare_frozen_readonly_skill_search,
    )
    from oh_goon.runtime.goon_templates import GoonTemplateStore
    from oh_goon.services.fleet_plan_input_inspection import inspect_projected_inputs
    from oh_goon.services.fleet_plan_resolver import FleetPlanResolver

    original = ROOT / "examples/agent-skill-local-search"
    workflow = tmp_path / "local-search"
    shutil.copytree(original, workflow)
    source = workflow / "plan.yml"
    resolver = FleetPlanResolver(goon_templates=GoonTemplateStore())
    graph = resolver.analyze_source(source)
    contract = {item.definition.name: item for item in inspect_projected_inputs(graph)}
    assert set(contract) == {"repo", "skill", "acceptance"}
    assert all(item.definition.required for item in contract.values())
    edge = graph.root.compiled.steps[0]
    assert isinstance(edge, CompiledGoonImportStep)
    assert edge.goon == "agent/search"
    assert graph.goon(source, edge).path == workflow / "goons/agent/search/goon.yml"
    assert graph.containment_root == workflow

    frozen, handoff, _ = resolver.resolve_frozen_local_search_agent(
        source,
        source_graph=graph,
        inputs={"repo": "local:team/app", "skill": "local:team/search", "acceptance": "review"},
        repository_root=workflow / "repos",
        skill_root=workflow / "skills",
        allowed_acceptance=("review",),
    )
    assert handoff.harness == "local-search"
    assert len(frozen.steps) == 1 and frozen.steps[0].kind == "agent"
    with prepare_frozen_readonly_skill_search(handoff) as (workspace, tool):
        result = execute_direct_step(
            workspace,
            tool,
            StepRuntime(git=None, extensions=None, tools=None, cancel_requested=None),
            step_order=1,
        )
    assert result.returncode == 0
    assert "needle" in result.stdout
    assert result.supervisor is not None
    assert not workspace.exists()
