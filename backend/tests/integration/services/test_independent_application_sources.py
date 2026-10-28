"""Independent applications use canonical source, native commands and Run history."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.integrations.git_source_preparation import git_source_cache

ROOT = Path(__file__).resolve().parents[4]
ORDERS = ROOT / "templates/goons/general/orders-taskflow"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None,
    reason="native POSIX Git and uv required",
)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()


def _commit(repository: Path) -> str:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Application fixture",
        "-c",
        "user.email=application@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "application source",
    )
    return _git(repository, "rev-parse", "HEAD")


def _orders_application(
    repository: Path, subdirectory: str = ".", *, initialize_git: bool = True
) -> Path:
    application = repository / subdirectory
    shutil.copytree(
        ORDERS,
        application,
        ignore=shutil.ignore_patterns(
            "goon.yml", "run.yml", "goon.lock", ".venv", "__pycache__", ".pytest_cache", ".showcase"
        ),
    )
    assert not (application / "goon.yml").exists()
    (repository / ".gitignore").write_text(
        "**/.venv/\n**/__pycache__/\n**/.pytest_cache/\n**/.showcase/\n", encoding="utf-8"
    )
    if initialize_git:
        _git(repository, "init", "-q", "-b", "main")
        _commit(repository)
    return application


def _cli_environment(tmp_path: Path) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_OFFLINE="1",
    )
    return environment


def _cli(tmp_path: Path, environment: dict[str, str]) -> Callable[..., str]:
    executable = Path(sys.executable).with_name("oh-goon")

    def invoke(*arguments: str, expected: int = 0) -> str:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == expected, (
            arguments,
            completed.stdout[-8192:],
            completed.stderr[-8192:],
        )
        return completed.stdout

    return invoke


def _registered_target(cli: Callable[..., str], repository: Path) -> str:
    cli("league", "add-folder", str(repository))
    league = json.loads(cli("league", "scan", "--json"))
    target = next(
        team["repository"]
        for team in league["teams"]
        if Path(team["repository"]["path"]) == repository
    )
    return str(target["repository_id"])


def _workflow(
    root: Path, target: str, application: dict[str, str], *, lock_mode: str = "frozen"
) -> Path:
    package = root / "goons/general/independent-orders"
    package.mkdir(parents=True)
    declaration = yaml.safe_load((ORDERS / "goon.yml").read_text(encoding="utf-8"))
    declaration["application"] = application
    declaration["uv"] = {"lock_mode": lock_mode}
    (package / "goon.yml").write_text(yaml.safe_dump(declaration, sort_keys=False))
    (package / "README.md").write_text(
        "# general/independent-orders\n\nInvoke the independently selected orders application.\n"
    )
    (root / "plan.yml").write_text(
        "goons:\n- import: general/independent-orders\n  from: ./goons\n"
    )
    selection = root / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": target},
            },
            sort_keys=False,
        )
    )
    return selection


def _select(selection: Path, fixture: str) -> None:
    payload = yaml.safe_load(selection.read_text())
    payload["inputs"] = {"fixture": fixture}
    selection.write_text(yaml.safe_dump(payload, sort_keys=False))


def _run_and_read(
    cli: Callable[..., str], selection: Path, total: str | None
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    receipt = json.loads(cli("run", str(selection), "--json", expected=0 if total else 1))
    assert receipt["status"] == "COMPLETED"
    run_id = str(receipt["fleet_run_id"])
    tape = json.loads(cli("plan", "tape", run_id, "--json"))
    outputs = json.loads(cli("plan", "outputs", run_id, "--json"))
    steps = tape["repositories"][0]["steps"]
    assert [step["step_id"].rsplit(".", 1)[-1] for step in steps] == [
        "extract",
        "transform",
        "load",
    ]
    if total is None:
        assert receipt["outcome"] != "SUCCESS"
        assert tape["status"] == "ATTENTION_REQUIRED"
        assert [step["status"] for step in steps] == ["PASSED", "FAILED", "SKIPPED"]
        assert steps[1]["exit_code"] != 0
        assert outputs["repositories"][0]["steps"][2]["artifacts"] == []
    else:
        assert receipt["outcome"] == "SUCCESS" and tape["status"] == "PASSED"
        assert [step["status"] for step in steps] == ["PASSED"] * 3
        result = (json.dumps({"total_order_value": total}) + "\n").encode()
        artifact = outputs["repositories"][0]["steps"][2]["artifacts"][0]
        assert artifact["sha256"] == hashlib.sha256(result).hexdigest()
        assert artifact["byte_count"] == len(result) and artifact.get("retained_object") is None
        produced_total = outputs["repositories"][0]["steps"][1]["outputs"][0]
        assert produced_total["sha256"] == hashlib.sha256(total.encode()).hexdigest()
    return run_id, tape, outputs


def test_local_application_uses_unchanged_native_code_and_keeps_three_run_history(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    selected = _workflow(tmp_path / "workflow", target, {"path": str(application)})

    # This is the actual standalone orders application, without its workflow.
    # Native development installs the same entrypoint the separate Goon invokes.
    sync = subprocess.run(
        ["uv", "sync", "--project", str(application), "--frozen", "--no-default-groups"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert sync.returncode == 0, sync.stderr
    developer_marker = application / ".venv/developer-owned"
    developer_marker.write_bytes(b"native developer environment")
    developer_configuration = (application / ".venv/pyvenv.cfg").read_bytes()
    direct_output = tmp_path / "native-development"
    direct_output.mkdir()
    direct = subprocess.run(
        [str(application / ".venv/bin/orders-pipeline"), "run", "--fixture", "official"],
        cwd=direct_output,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert direct.returncode == 0, direct.stderr
    assert json.loads((direct_output / ".showcase/result.json").read_text()) == {
        "total_order_value": "1236.70"
    }
    inspection = json.loads(cli("config", "inspect", str(selected), "--json"))
    assert inspection["status"] == "inputs-valid"

    history = []
    for fixture, total in (("official", "1236.70"), ("invalid", None), ("changed", "60.00")):
        _select(selected, fixture)
        cli("lock", str(selected))
        run_id, tape, outputs = _run_and_read(cli, selected, total)
        steps = tape["repositories"][0]["steps"]
        source = steps[0]["project_source"]
        assert source["application"] == {"path": str(application)}
        assert all(step["project_source"] == source for step in steps)
        assert all(step["uv"]["lock_mode"] == "frozen" for step in steps)
        for step in steps:
            for attempt in step["attempts"]:
                assert attempt["uv_preparation"]["lock_mode"] == "frozen"
                assert attempt["uv_preparation"]["freshness"] == "not_checked"
                assert attempt["supervisor"]["final_process_group_quiescent"] is True
        assert not (application / ".showcase").exists()
        assert developer_marker.read_bytes() == b"native developer environment"
        assert (application / ".venv/pyvenv.cfg").read_bytes() == developer_configuration
        scratch = ApplicationStatePathLayout.from_database(
            Path(environment["OH_GOON_DB"])
        ).run_workspaces
        assert scratch.is_dir() and list(scratch.iterdir()) == []
        history.append((run_id, tape, outputs))

    assert len({run_id for run_id, _, _ in history}) == 3
    assert (
        len(
            {
                tape["repositories"][0]["steps"][0]["project_source"]["content_sha256"]
                for _, tape, _ in history
            }
        )
        == 1
    )
    assert _git(repository, "status", "--porcelain") == ""
    source_file = application / "src/orders_pipeline/data/official.json"
    original = source_file.read_bytes()
    source_file.write_text('{"1001":5.00}\n')
    cli("run", str(selected), "--json", expected=2)
    source_file.write_bytes(original)

    # Fresh CLI processes consume historical facts after the authoring tree and
    # all attempt workspaces are gone; later success cannot rewrite old failure.
    shutil.rmtree(selected.parent)
    for run_id, original_tape, original_outputs in history:
        reopened = json.loads(cli("plan", "tape", run_id, "--json"))
        for field in ("status", "execution_reference", "configuration", "repositories"):
            assert reopened[field] == original_tape[field]
        assert (
            json.loads(cli("plan", "outputs", run_id, "--json"))["repositories"]
            == (original_outputs["repositories"])
        )


@pytest.mark.parametrize("subdirectory", (".", "nested/project"), ids=("root", "subdirectory"))
def test_git_application_keeps_its_own_pin_across_ref_changes_and_cache_loss(
    tmp_path: Path, subdirectory: str
) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository, subdirectory)
    original_commit = _git(repository, "rev-parse", "HEAD")
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    coordinates = {"git": str(repository), "ref": "main", "path": subdirectory}
    selected = _workflow(tmp_path / "workflow", target, coordinates)
    cli("lock", str(selected))
    lock_path = selected.with_name("goon.lock")
    original_lock = yaml.safe_load(lock_path.read_text())

    def pin() -> dict[str, Any]:
        return next(
            source
            for source in yaml.safe_load(lock_path.read_text())["execution_sources"]
            if source["kind"] == "application"
        )

    assert pin()["commit"] == original_commit
    _, original_tape, _ = _run_and_read(cli, selected, "1236.70")
    captured = original_tape["repositories"][0]["steps"][0]["project_source"]
    assert {
        field: captured["application"][field] for field in (*coordinates, "kind", "commit")
    } == {
        **coordinates,
        "kind": "application",
        "commit": original_commit,
    }
    (application / "src/orders_pipeline/data/official.json").write_text('{"1001":5.00}\n')
    changed_commit = _commit(repository)
    cli("lock", str(selected))
    assert yaml.safe_load(lock_path.read_text()) == original_lock
    cli("lock", str(selected), "--update-goons")
    assert pin()["commit"] == original_commit
    shutil.rmtree(git_source_cache(environment))
    _, retained_tape, _ = _run_and_read(cli, selected, "1236.70")
    assert retained_tape["repositories"][0]["steps"][0]["project_source"] == captured
    cli("lock", str(selected), "--update-applications")
    assert pin()["commit"] == changed_commit
    _, refreshed_tape, _ = _run_and_read(cli, selected, "5.00")
    refreshed = refreshed_tape["repositories"][0]["steps"][0]["project_source"]
    assert refreshed["application"]["commit"] == changed_commit
    assert refreshed["content_sha256"] != captured["content_sha256"]
    assert not (application / ".venv").exists()
    assert not (application / ".showcase").exists()


def test_registered_filesystem_application_runs_and_exports_without_git(tmp_path: Path) -> None:
    repository = tmp_path / "plain application"
    application = _orders_application(repository, initialize_git=False)
    original_lock = (application / "uv.lock").read_bytes()
    original_source = (application / "src/orders_pipeline/operations.py").read_bytes()
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    cli("league", "add-folder", str(repository))
    assert json.loads(cli("league", "scan", "--json"))["teams"] == []
    workflow = tmp_path / "workflow"
    cli(
        "templates",
        "init",
        "general/orders-taskflow",
        "--kind",
        "goon",
        "--repository",
        str(repository),
        "--output",
        str(workflow),
    )
    selection = workflow / "run.yml"
    target = yaml.safe_load(selection.read_text())["target"]["repository_id"]
    package = workflow / "goons/general/independent-orders"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/independent-orders\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "application": {"path": str(application)},
                "uv": {"lock_mode": "frozen"},
                "steps": [
                    {
                        "run": "orders-pipeline run --fixture official",
                        "artifacts": [
                            {"path": ".showcase/result.json", "retention": "content_store"}
                        ],
                    }
                ],
            }
        )
    )
    (workflow / "plan.yml").write_text(
        "goons:\n- import: general/independent-orders\n  from: ./goons\n"
    )
    cli("lock", str(selection))
    receipt = json.loads(cli("run", str(selection), "--json"))
    assert receipt["outcome"] == "SUCCESS"
    run_id = receipt["fleet_run_id"]
    tape = json.loads(cli("plan", "tape", run_id, "--json"))
    recorded_repository = tape["repositories"][0]
    assert recorded_repository["repository"]["repository_id"] == target
    assert Path(recorded_repository["repository"]["path"]) == repository
    (step,) = recorded_repository["steps"]
    assert step["status"] == "PASSED" and step["settlement"]["cleanup"] == "VERIFIED"
    assert step["project_source"]["application"] == {"path": str(application)}
    (artifact,) = step["artifacts"]
    assert artifact["retained_object"]["owner_repository_id"] == target
    assert (application / "uv.lock").read_bytes() == original_lock
    assert (application / "src/orders_pipeline/operations.py").read_bytes() == original_source
    assert not (repository / ".git").exists()
    assert not (application / ".venv").exists() and not (application / ".showcase").exists()
    scratch = ApplicationStatePathLayout.from_database(
        Path(environment["OH_GOON_DB"])
    ).run_workspaces
    assert list(scratch.iterdir()) == []
    shutil.rmtree(repository)
    shutil.rmtree(workflow)
    reopened = json.loads(cli("plan", "tape", run_id, "--json"))
    assert reopened["repositories"] == tape["repositories"]
    destination = tmp_path / "retained-orders.json"
    exported = json.loads(
        cli(
            "plan",
            "export-artifact",
            run_id,
            "--step",
            step["step_id"],
            "--path",
            artifact["path"],
            "--output",
            str(destination),
            "--json",
        )
    )
    expected = (json.dumps({"total_order_value": "1236.70"}) + "\n").encode()
    assert destination.read_bytes() == expected
    assert exported["sha256"] == artifact["sha256"] == hashlib.sha256(expected).hexdigest()
    assert exported["repository_id"] == target


def test_plain_application_source_does_not_require_python_or_goon_metadata(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "applications/shell"
    shutil.copytree(ROOT / "examples/standard-atomic/scripts", repository / "scripts")
    _git(repository, "init", "-q")
    _commit(repository)
    assert set(path.name for path in repository.iterdir()) == {"scripts", ".git"}
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/shell-application"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# general/shell-application\n")
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {"application": {"path": str(repository)}, "steps": [{"run": "sh scripts/hello.sh"}]}
        )
    )
    (workflow / "plan.yml").write_text(
        "goons:\n- import: general/shell-application\n  from: ./goons\n"
    )
    selected = workflow / "run.yml"
    selected.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": target},
            }
        )
    )
    cli("lock", str(selected))
    receipt = json.loads(cli("run", str(selected), "--json"))
    assert receipt["outcome"] == "SUCCESS"
    tape = json.loads(cli("plan", "tape", receipt["fleet_run_id"], "--json"))
    step = tape["repositories"][0]["steps"][0]
    assert step["status"] == "PASSED" and step["settlement"]["cleanup"] == "VERIFIED"
    assert step["uv"] is None
    assert step["project_source"]["application"] == {"path": str(repository)}
    assert step["attempts"][0].get("uv_preparation") is None
    assert step["attempts"][0]["supervisor"]["final_process_group_quiescent"] is True
    assert _git(repository, "status", "--porcelain") == ""


def test_application_retained_result_exports_after_attempt_disposal(tmp_path: Path) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    original_lock = (application / "uv.lock").read_bytes()
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    selected = _workflow(tmp_path / "workflow", target, {"path": str(application)})
    goon = selected.parent / "goons/general/independent-orders/goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["steps"] = [
        {
            "run": "orders-pipeline run --fixture ${{ inputs.fixture }}",
            "artifacts": [{"path": ".showcase/result.json", "retention": "content_store"}],
        }
    ]
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    cli("lock", str(selected))
    receipt = json.loads(cli("run", str(selected), "--json"))
    assert receipt["outcome"] == "SUCCESS"
    tape = json.loads(cli("plan", "tape", receipt["fleet_run_id"], "--json"))
    (step,) = tape["repositories"][0]["steps"]
    assert step["status"] == "PASSED" and step["settlement"]["cleanup"] == "VERIFIED"
    assert step["project_source"]["application"] == {"path": str(application)}
    assert step["attempts"][0]["supervisor"]["final_process_group_quiescent"] is True
    (artifact,) = step["artifacts"]
    expected = (json.dumps({"total_order_value": "1236.70"}) + "\n").encode()
    assert artifact["path"] == ".showcase/result.json" and artifact["status"] == "PRESENT"
    assert artifact["sha256"] == hashlib.sha256(expected).hexdigest()
    assert artifact["byte_count"] == len(expected)
    assert artifact["retained_object"]["owner_repository_id"] == target
    assert (application / "uv.lock").read_bytes() == original_lock
    assert not (application / ".showcase").exists()
    assert not (application / ".venv").exists()
    assert _git(repository, "status", "--porcelain") == ""
    scratch = ApplicationStatePathLayout.from_database(
        Path(environment["OH_GOON_DB"])
    ).run_workspaces
    assert scratch.is_dir() and list(scratch.iterdir()) == []

    # Export reopens retained Run evidence after the original attempt and
    # authoring workspace have gone, without rerunning the application.
    shutil.rmtree(selected.parent)
    destination = tmp_path / "retained result.json"
    exported = json.loads(
        cli(
            "plan",
            "export-artifact",
            receipt["fleet_run_id"],
            "--step",
            step["step_id"],
            "--path",
            artifact["path"],
            "--output",
            str(destination),
            "--json",
        )
    )
    assert exported["status"] == "exported"
    assert destination.read_bytes() == expected
    assert exported["sha256"] == artifact["sha256"]
    assert exported["byte_count"] == len(expected)
    assert exported["reference_id"] == artifact["retained_object"]["reference_id"]
    assert _git(repository, "status", "--porcelain") == ""


def test_repeated_application_occurrences_preserve_independent_output_lineage(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    selected = _workflow(tmp_path / "workflow", target, {"path": str(application)})
    selected.with_name("plan.yml").write_text(
        yaml.safe_dump(
            {
                "goons": [
                    {
                        "import": "general/independent-orders",
                        "from": "./goons",
                        "as": occurrence,
                        "with": {"fixture": fixture},
                    }
                    for occurrence, fixture in (("first", "official"), ("second", "changed"))
                ]
            }
        )
    )
    cli("lock", str(selected))
    receipt = json.loads(cli("run", str(selected), "--json"))
    assert receipt["outcome"] == "SUCCESS"
    tape = json.loads(cli("plan", "tape", receipt["fleet_run_id"], "--json"))
    steps = tape["repositories"][0]["steps"]
    assert len(steps) == len({step["step_id"] for step in steps}) == 6
    assert len({step["project_source"]["content_sha256"] for step in steps}) == 1
    for completed, total in zip((steps[:3], steps[3:]), ("1236.70", "60.00"), strict=True):
        assert [step["status"] for step in completed] == ["PASSED"] * 3
        result = (json.dumps({"total_order_value": total}) + "\n").encode()
        assert completed[2]["artifacts"][0]["sha256"] == hashlib.sha256(result).hexdigest()
        assert completed[1]["outputs"][0]["sha256"] == hashlib.sha256(total.encode()).hexdigest()
    assert not (application / ".showcase").exists()
    assert _git(repository, "status", "--porcelain") == ""


@pytest.mark.parametrize("mode", ("frozen", "locked"))
def test_application_uv_lock_mode_matches_native_uv_without_rewriting_source(
    tmp_path: Path, mode: str
) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    metadata = application / "pyproject.toml"
    metadata.write_text(
        metadata.read_text().replace(
            "dependencies = []", 'dependencies = ["application-lock-mismatch-probe==0.0.0"]'
        )
    )
    _commit(repository)
    original_lock = (application / "uv.lock").read_bytes()
    environment = _cli_environment(tmp_path)
    native = subprocess.run(
        ["uv", "sync", "--project", str(application), "--" + mode, "--no-default-groups"],
        env={**environment, "UV_PROJECT_ENVIRONMENT": str(tmp_path / "native-environment")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert (native.returncode == 0) is (mode == "frozen"), native.stderr
    assert (application / "uv.lock").read_bytes() == original_lock

    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    selected = _workflow(tmp_path / "workflow", target, {"path": str(application)}, lock_mode=mode)
    cli("lock", str(selected))
    if mode == "frozen":
        _, tape, _ = _run_and_read(cli, selected, "1236.70")
        preparation = tape["repositories"][0]["steps"][0]["attempts"][0]["uv_preparation"]
        assert preparation["freshness"] == "not_checked"
    else:
        receipt = json.loads(cli("run", str(selected), "--json", expected=1))
        assert receipt["outcome"] != "SUCCESS"
        tape = json.loads(cli("plan", "tape", receipt["fleet_run_id"], "--json"))
        steps = tape["repositories"][0]["steps"]
        assert [step["status"] for step in steps] == ["FAILED", "SKIPPED", "SKIPPED"]
        assert not steps[-1]["artifacts"]
    assert all(step["uv"]["lock_mode"] == mode for step in tape["repositories"][0]["steps"])
    assert (application / "uv.lock").read_bytes() == original_lock
    assert _git(repository, "status", "--porcelain") == ""


def test_independent_application_example_runs_after_replacing_its_placeholders(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    workflow = tmp_path / "workflow"
    shutil.copytree(ROOT / "examples/independent-application", workflow)
    declarations = tuple(workflow.rglob("goon.yml"))
    assert len(declarations) == 1
    goon = declarations[0]
    declaration = yaml.safe_load(goon.read_text())
    declaration["application"]["path"] = str(application)
    declaration["steps"][0]["run"] = "orders-pipeline run --fixture official"
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    selected = workflow / "run.yml"
    configuration = yaml.safe_load(selected.read_text())
    configuration["target"]["repository_id"] = target
    selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
    inspection = json.loads(cli("config", "inspect", str(selected), "--json"))
    assert inspection["status"] == "inputs-valid"
    cli("config", "validate", str(selected), "--json")
    cli("lock", str(selected))
    receipt = json.loads(cli("run", str(selected), "--json"))
    assert receipt["outcome"] == "SUCCESS"
    tape = json.loads(cli("plan", "tape", receipt["fleet_run_id"], "--json"))
    assert tape["status"] == "PASSED"
    step = tape["repositories"][0]["steps"][0]
    assert step["project_source"]["application"]["path"] == str(application)
    assert step["uv"]["lock_mode"] == "frozen"
    assert step["settlement"]["cleanup"] == "VERIFIED"
    handoff = json.loads(cli("plan", "handoff", receipt["fleet_run_id"], "--json"))
    assert handoff["recorded_steps"]["steps"][0]["project_source"] == step["project_source"]
    assert not (application / ".showcase").exists()
    assert _git(repository, "status", "--porcelain") == ""


def test_python_application_cannot_fall_back_to_an_uninstalled_host_command(
    tmp_path: Path,
) -> None:
    assert shutil.which("true") is not None
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    selected = _workflow(tmp_path / "workflow", target, {"path": str(application)})
    goon = selected.parent / "goons/general/independent-orders/goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["steps"] = [
        {"run": "true"},
        {"run": "orders-pipeline run --fixture official"},
    ]
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    cli("lock", str(selected))
    receipt = json.loads(cli("run", str(selected), "--json", expected=1))
    assert receipt["outcome"] != "SUCCESS"
    tape = json.loads(cli("plan", "tape", receipt["fleet_run_id"], "--json"))
    steps = tape["repositories"][0]["steps"]
    assert [step["status"] for step in steps] == ["FAILED", "SKIPPED"]
    assert not (application / ".showcase").exists()


def test_application_escape_refuses_before_native_install_and_preserves_prior_lock(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "applications/orders"
    application = _orders_application(repository)
    environment = _cli_environment(tmp_path)
    cli = _cli(tmp_path, environment)
    target = _registered_target(cli, repository)
    selected = _workflow(tmp_path / "workflow", target, {"path": str(application)})
    cli("lock", str(selected))
    lock = selected.with_name("goon.lock")
    valid_lock = lock.read_bytes()
    assert not (application / ".venv").exists()
    outside = tmp_path / "unselected-private-file"
    outside.write_text("UNSELECTED_PRIVATE_CONTENT")
    (application / "escaping-resource").symlink_to(outside)
    rejected = cli("lock", str(selected), expected=2)
    assert "UNSELECTED_PRIVATE_CONTENT" not in rejected
    assert lock.read_bytes() == valid_lock
    assert not (application / ".venv").exists()
