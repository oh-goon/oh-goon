"""The documented local Python journey executes real locked tools through Fleet."""

import json
import os
import select
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

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
    # Start with the real existing Plan, explicitly select ordinary local verification.
    from oh_goon.runtime.plan_templates import PlanTemplateStore

    source = PlanTemplateStore().source("python/quality").content
    path.write_text(
        source.replace("version: 1\n", "version: 1\nexecution: {purpose: verification}\n", 1)
    )
    values = tmp_path / "values.yml"
    values.write_text(f"inputs:\n  project: '{project}'\n  typechecker: pyright\n")
    local = tmp_path / "goons/general/project-check"
    local.mkdir(parents=True)
    (local / "README.md").write_text("# general/project-check\n\nCompile the selected project.\n")
    (local / "goon.yml").write_text(f"""version: 1
uv:
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
    flags = ["--inputs-file", "values.yml", "--input", f"typechecker={checker}"]
    assert main(["lock", str(path), *flags]) == 0
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(tmp_path / "repositories")
        if project == ".":
            capsys.readouterr()
            assert (
                main(
                    [
                        "plan",
                        "run",
                        str(path),
                        *flags,
                        "--repository-id",
                        repository_id(repository),
                        "--standard-local",
                        "--wait",
                        "--json",
                    ]
                )
                == 0
            )
            receipt = json.loads(capsys.readouterr().out)
            result = runtime.runs.get(receipt["fleet_run_id"])
        else:
            started = runtime.plans.run(
                path,
                repository_ids={repository_id(repository)},
                inputs={"project": project, "typechecker": checker},
            )
            result = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=60)
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
        assert main(["lock", str(path), *flags]) == 0
        started = runtime.plans.run(
            path,
            repository_ids={repository_id(repository)},
            inputs={"project": project, "typechecker": checker},
        )
        failed = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=60)
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
    (directory / "goon.yml").write_text(f"""version: 1
uv: {{group: dev}}
steps:
- run: uv run python wait.py {shlex.quote(str(ready))} {shlex.quote(str(hold))}
- run: uv run python complete.py
""")
    path = tmp_path / "plan.yml"
    path.write_text(
        "version: 1\nexecution: {purpose: verification}\ngoons:\n- import: general/wait\n  from: goons\n"
    )
    assert main(["lock", str(path)]) == 0
    runtime = ApplicationRuntime.build(standard_local=True)
    started = None
    try:
        runtime.locations.add(tmp_path / "repositories")
        with os.fdopen(os.open(ready, os.O_RDWR | os.O_NONBLOCK), "rb", buffering=0) as channel:
            started = runtime.plans.run(path, repository_ids={repository_id(repository)})
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
