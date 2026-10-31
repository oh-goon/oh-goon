"""Documented authoring journeys resolve actual sources and execute native tools."""

import hashlib
import json
import os
import select
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.cli import main
from oh_goon.core.models.fleet_plan_isolation import CicdResourcePolicy
from oh_goon.core.product_identity import repository_id
from tests.factories.run_resources import require_native_run_resource_context

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "template-certification/fixtures/python/standard"
pytestmark = [
    pytest.mark.daily,
    pytest.mark.skipif(
        os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
    ),
]


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


def _workflow_cli(tmp_path: Path, environment: dict[str, str]) -> Callable[..., str]:
    executable = Path(sys.executable).with_name("oh-goon")

    def cli(*arguments: str, expected_returncode: int = 0) -> str:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == expected_returncode, (
            arguments,
            completed.stdout[-8192:],
            completed.stderr[-8192:],
        )
        return completed.stdout

    return cli


@pytest.mark.parametrize(
    "kind,key,step_count",
    [("plan", "python/quality", 4), ("goon", "python/test", 2), ("goon", "python/quality", 4)],
    ids=["initialized-plan", "initialized-atomic-goon", "initialized-composite-goon"],
)
def test_initialized_workflow_executes_and_reads_back_through_fresh_cli_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native_fixture_uv_cache: Path,
    kind: str,
    key: str,
    step_count: int,
) -> None:
    monkeypatch.setenv("UV_CACHE_DIR", str(native_fixture_uv_cache))
    repository = tmp_path / "repositories/team/app project"
    shutil.copytree(
        FIXTURE,
        repository,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    _prepare_fixture_project(repository)
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    cli = _workflow_cli(tmp_path, environment)

    cli("league", "add-folder", str(tmp_path / "repositories"))
    workflow = tmp_path / "workflow"
    cli(
        "templates",
        "init",
        key,
        "--kind",
        kind,
        "--output",
        str(workflow),
        "--repository",
        str(repository),
    )
    if key == "python/test":
        # Extend the initialized Atomic selection through ordinary authoring,
        # using a real project command as the bounded published-data producer.
        (repository / "report.py").write_text("print('native-cli-report')\n")
        package = workflow / "goons/general/native-report"
        package.mkdir(parents=True)
        (package / "README.md").write_text(
            "# general/native-report\n\nPublish one native result.\n"
        )
        (package / "goon.yml").write_text(
            "uv: {group: dev}\nsteps:\n- run: uv run python report.py\n"
            "  outputs: {RESULT: {stream: stdout, max_bytes: 128}}\n"
        )
        with (workflow / "plan.yml").open("a") as stream:
            stream.write("- import: general/native-report\n  from: ./goons\n")
    selection = workflow / "run.yml"
    config = yaml.safe_load(selection.read_text())
    assert config["target"]["repository_id"] == repository_id(repository)
    if key == "python/quality":
        config["inputs"] = {"typechecker": "ty"}
    selection.write_text(yaml.safe_dump(config, sort_keys=False))
    if kind == "goon":
        inspected = json.loads(cli("config", "inspect", str(selection), "--json"))
        validated = json.loads(cli("config", "validate", str(selection), "--json"))
        assert inspected == validated
        assert validated["status"] == "inputs-valid"
        assert validated["proof"]["admission"] == "not-started"
    assert not (workflow / "goon.lock").exists()
    cli("lock", str(selection))
    assert (workflow / "goon.lock").is_file()
    human_result = None
    if kind == "plan":
        human_result = cli("run", str(selection))
        run_ids = [
            line.removeprefix("Fleet Run ID: ")
            for line in human_result.splitlines()
            if line.startswith("Fleet Run ID: ")
        ]
        assert len(run_ids) == 1
        run_id = run_ids[0]
        assert "Execution status: COMPLETED" in human_result
        assert "Outcome: SUCCESS" in human_result
        assert "junit.xml [PRESENT]" in human_result
        assert f"Inspect Run: oh-goon plan handoff {run_id}" in human_result
        assert f"Inspect outputs: oh-goon plan outputs {run_id}" in human_result
        assert f"Read logs (while available): oh-goon plan logs {run_id}" in human_result
    else:
        receipt = json.loads(cli("run", str(selection), "--json"))
        run_id = receipt["fleet_run_id"]
        assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
    # Every readback crosses a fresh process and reopens the durable original
    # Journal; none can reuse the execution process's transient Run objects.
    views = {
        view: json.loads(cli("plan", view, run_id, "--json"))
        for view in ("status", "steps", "outputs", "tape")
    }
    assert all(report["fleet_run_id"] == run_id for report in views.values())
    assert views["status"]["status"] == "COMPLETED"
    assert views["status"]["outcome"] == "SUCCESS"
    tape = views["tape"]
    assert tape["status"] == "PASSED"
    steps = views["steps"]["repositories"][0]["steps"]
    assert steps == tape["repositories"][0]["steps"]
    assert len(steps) == step_count
    assert all(step["status"] == "PASSED" and step["exit_code"] == 0 for step in steps)
    report = next(
        artifact
        for step in views["outputs"]["repositories"][0]["steps"]
        for artifact in step["artifacts"]
        if artifact["path"] == "junit.xml"
    )
    junit = (repository / "junit.xml").read_bytes()
    assert b'tests="1"' in junit
    assert report["status"] == "PRESENT"
    assert report["sha256"] == hashlib.sha256(junit).hexdigest()
    assert report["byte_count"] == len(junit)
    if human_result is not None:
        assert f"Evidence: {len(junit)} bytes; sha256:{report['sha256']}" in human_result

    if key == "python/test":
        published = next(
            output
            for step in views["outputs"]["repositories"][0]["steps"]
            for output in step["outputs"]
            if output["name"] == "RESULT"
        )
        assert published["sha256"] == hashlib.sha256(b"native-cli-report").hexdigest()
        assert published["byte_count"] == len(b"native-cli-report")
        (repository / "tests/test_workload.py").write_text(
            "def test_native_failure():\n    assert False\n"
        )
        cli("lock", str(selection))
        failed = json.loads(cli("run", str(selection), "--json", expected_returncode=1))
        failed_status = json.loads(cli("plan", "status", failed["fleet_run_id"], "--json"))
        failed_tape = json.loads(cli("plan", "tape", failed["fleet_run_id"], "--json"))
        assert failed_status["outcome"] == "ATTENTION_REQUIRED"
        assert failed_tape["status"] == "ATTENTION_REQUIRED"
        failed_step = failed_tape["repositories"][0]["steps"][0]
        assert failed_step["status"] == "FAILED" and failed_step["exit_code"] == 1
        skipped = failed_tape["repositories"][0]["steps"][1]
        assert skipped["status"] == "SKIPPED" and skipped["outputs"] == []
        assert json.loads(cli("plan", "outputs", run_id, "--json")) == views["outputs"]


@pytest.mark.parametrize(
    "budgeted",
    (False, pytest.param(True, marks=pytest.mark.no_resume)),
    ids=("inherited", "whole-run-budget"),
)
def test_plural_configuration_runs_actual_applications_concurrently_and_keeps_target_history(
    tmp_path: Path,
    budgeted: bool,
) -> None:
    budget = CicdResourcePolicy(cpu_millis=2000, memory_mib=1024, max_processes=256)
    if budgeted:
        require_native_run_resource_context(budget)
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
    )
    cli = _workflow_cli(tmp_path, environment)
    applications = (tmp_path / "applications/one", tmp_path / "applications/two")
    barrier = tmp_path / "target rendezvous"
    barrier.mkdir()
    verify = r"""set -eu
barrier=$1
read -r application < application.txt
read -r quantity unit_price < order.txt
"""
    if budgeted:
        verify += 'cat /proc/self/cgroup > "$barrier/$application.cgroup"\n'
    verify += r"""
printf '%s\n' "$$" > "$barrier/$application.started"
attempt=0
while [ ! -f "$barrier/one.started" ] || [ ! -f "$barrier/two.started" ]; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 500 ]; then exit 9; fi
    sleep 0.02
done
total=$((quantity * unit_price))
printf '%s\n' "$total" > total.txt
printf '%s\n' "$total"
"""
    for application, order in zip(applications, ("7 9\n", "3 5\n"), strict=True):
        application.mkdir(parents=True)
        (application / "README.md").write_text("# Order total application\n")
        (application / "application.txt").write_text(application.name + "\n")
        (application / "order.txt").write_text(order)
        (application / "verify.sh").write_text(verify)
        (application / "complete.sh").write_text("printf 'completed\\n' > completed.txt\n")
        cli("league", "add-folder", str(application))
    workflow = tmp_path / "workflow"
    cli(
        "templates",
        "init",
        "python/test",
        "--kind",
        "goon",
        "--output",
        str(workflow),
        "--repository",
        str(applications[0]),
        "--repository",
        str(applications[1]),
    )
    selection = workflow / "run.yml"
    config = yaml.safe_load(selection.read_text())
    repository_ids = {repository_id(application) for application in applications}
    assert set(config["target"]["repository_ids"]) == repository_ids
    assert "repository_id" not in config["target"]
    if budgeted:
        config["resources"] = budget.model_dump(exclude_none=True)
        selection.write_text(yaml.safe_dump(config, sort_keys=False))
    package = workflow / "goons/general/verify-order"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/verify-order\n\nVerify each order.\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "steps": [
                    {
                        "id": "total",
                        "run": "/bin/sh verify.sh " + shlex.quote(str(barrier)),
                        "artifacts": [{"path": "total.txt"}],
                        "outputs": {"TOTAL": {"stream": "stdout", "max_bytes": 32}},
                    },
                    {"id": "complete", "run": "/bin/sh complete.sh"},
                ]
            },
            sort_keys=False,
        )
    )
    (workflow / "plan.yml").write_text(
        yaml.safe_dump(
            {
                "execution": {
                    "purpose": "verification",
                    "repositories": {"mode": "parallel", "max_parallel": 2},
                },
                "goons": [{"import": "general/verify-order", "from": "./goons"}],
            },
            sort_keys=False,
        )
    )
    cli("lock", str(selection))
    receipt = json.loads(cli("run", str(selection), "--json"))
    assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
    run_id = receipt["fleet_run_id"]
    tape = json.loads(cli("plan", "tape", run_id, "--json"))
    outputs = json.loads(cli("plan", "outputs", run_id, "--json"))
    events = json.loads(cli("plan", "events", run_id, "--json"))
    assert tape["status"] == "PASSED"
    assert {item["repository"]["repository_id"] for item in tape["repositories"]} == repository_ids
    assert len({item["operation_id"] for item in tape["repositories"]}) == 2
    assert all(
        [step["status"] for step in item["steps"]] == ["PASSED", "PASSED"]
        for item in tape["repositories"]
    )
    admissions = [event for event in events["events"] if event["event_type"] == "RUN_ADMITTED"]
    assert len(admissions) == 1
    assert set(admissions[0]["repository_scope"]["repository_ids"]) == repository_ids
    assert {item["repository_id"] for item in admissions[0]["targets"]} == repository_ids
    if budgeted:
        envelope = admissions[0]["execution"]["resource_envelope"]
        assert envelope["resources"] == budget.model_dump()
        assert envelope["fleet_run_id"] == run_id
        assert receipt["resource_observation_persistence"] == "RECORDED"
        assert receipt["resource_observation"]["cleanup"] == "VERIFIED"
        assert tape["resource_observation"] == receipt["resource_observation"]
        memberships = []
        for application in applications:
            rows = (barrier / f"{application.name}.cgroup").read_text().splitlines()
            unified = [row.removeprefix("0::") for row in rows if row.startswith("0::")]
            assert len(unified) == 1
            assert unified[0].endswith("/" + envelope["unit_name"])
            memberships.append(unified[0])
        assert len(set(memberships)) == 1
    assert (
        len({(barrier / f"{application.name}.started").read_text() for application in applications})
        == 2
    )
    for application, expected in zip(applications, (b"63\n", b"15\n"), strict=True):
        assert (application / "total.txt").read_bytes() == expected
        assert (application / "completed.txt").read_text() == "completed\n"
        recorded = next(
            item
            for item in outputs["repositories"]
            if item["repository"]["repository_id"] == repository_id(application)
        )
        artifact = recorded["steps"][0]["artifacts"][0]
        assert artifact["sha256"] == hashlib.sha256(expected).hexdigest()
        assert artifact["byte_count"] == len(expected)

    for application in applications:
        (barrier / f"{application.name}.started").unlink()
        (application / "completed.txt").unlink()
    (applications[1] / "verify.sh").write_text(verify + "exit 7\n")
    cli("lock", str(selection))
    failed = json.loads(cli("run", str(selection), "--json", expected_returncode=1))
    assert failed["fleet_run_id"] != run_id
    assert failed["outcome"] == "ATTENTION_REQUIRED"
    failed_tape = json.loads(cli("plan", "tape", failed["fleet_run_id"], "--json"))
    partition = {
        item["repository"]["repository_id"]: item["steps"] for item in failed_tape["repositories"]
    }
    assert set(partition) == repository_ids
    assert [step["status"] for step in partition[repository_id(applications[0])]] == [
        "PASSED",
        "PASSED",
    ]
    failed_steps = partition[repository_id(applications[1])]
    assert [step["status"] for step in failed_steps] == ["FAILED", "SKIPPED"]
    assert failed_steps[0]["exit_code"] == 7
    if budgeted:
        assert failed["resource_observation_persistence"] == "RECORDED"
        assert failed["resource_observation"]["cleanup"] == "VERIFIED"
        assert failed_tape["resource_observation"] == failed["resource_observation"]
    assert (applications[0] / "completed.txt").is_file()
    assert not (applications[1] / "completed.txt").exists()
    reopened = json.loads(cli("plan", "tape", run_id, "--json"))
    assert reopened["repositories"] == tape["repositories"]
    assert reopened["status"] == "PASSED"
    assert json.loads(cli("plan", "outputs", run_id, "--json")) == outputs


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

    frozen, handoff, _ = resolver.resolve_frozen_agent(
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
