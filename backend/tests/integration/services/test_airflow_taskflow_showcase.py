from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import repository_id

SHOWCASE = Path(__file__).resolve().parents[4] / "templates/goons/general/orders-taskflow"
GIT_EXAMPLE = Path(__file__).resolve().parents[4] / "examples/airflow-taskflow-showcase"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


@pytest.mark.parametrize("git_selected", [False, True], ids=["root-goon", "git-plan"])
def test_repository_showcase_executes_output_handoffs_and_reads_back_failures(
    tmp_path: Path,
    git_selected: bool,
) -> None:
    repository = tmp_path / "repositories/team/orders"
    shutil.copytree(
        SHOWCASE,
        repository,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=Orders developer",
            "-c",
            "user.email=developer@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "actual orders application",
        ],
        check=True,
    )
    source_marker = None
    if git_selected:
        source = tmp_path / "upstream"
        package = source / "templates/goons/general/orders-taskflow"
        shutil.copytree(
            SHOWCASE,
            package,
            ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
        )
        example = source / "examples/airflow-taskflow-showcase"
        example.mkdir(parents=True)
        shutil.copy2(GIT_EXAMPLE / "plan.yml", example / "plan.yml")
        subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "orders",
            ],
            check=True,
        )
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        source_marker = package / ".venv/developer-state"
        source_marker.parent.mkdir()
        source_marker.write_bytes(b"owned by the source developer")
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
        PYTHONDONTWRITEBYTECODE="1",
    )

    def native(*arguments: str) -> None:
        completed = subprocess.run(
            ["uv", *arguments],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, (
            arguments,
            completed.stdout[-8192:],
            completed.stderr[-8192:],
        )

    native_lock = (repository / "uv.lock").read_bytes()
    native("sync", "--frozen", "--no-default-groups", "--group", "dev")
    native(
        "run",
        "--frozen",
        "--no-sync",
        "pytest",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(tmp_path / "native-tests"),
        "tests/test_operations.py",
        "tests/test_cli.py",
    )
    native("run", "--frozen", "--no-sync", "orders-pipeline", "run", "--fixture", "official")
    developer_result = repository / ".showcase/result.json"
    direct_bytes = developer_result.read_bytes()
    assert json.loads(direct_bytes) == {"total_order_value": "1236.70"}
    developer_marker = repository / ".venv/developer-state"
    developer_marker.write_bytes(b"actual developer environment")
    developer_configuration = (repository / ".venv/pyvenv.cfg").read_bytes()
    assert (repository / "uv.lock").read_bytes() == native_lock
    if git_selected:
        environment["UV_OFFLINE"] = "1"
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

    cli("league", "add-folder", str(repository))
    workflow = tmp_path / "workflow"
    cli(
        "init",
        "--template",
        "general/orders-taskflow",
        "--kind",
        "goon",
        "--output",
        str(workflow),
        "--repository",
        str(repository),
    )
    selection = workflow / "run.yml"
    initialized_lock = (workflow / "goon.lock").read_bytes()
    original_selection = yaml.safe_load(selection.read_text())
    target = repository_id(repository)
    if git_selected:
        original_selection["source"] = yaml.safe_load((GIT_EXAMPLE / "run.yml").read_text())[
            "source"
        ]
        original_selection["source"]["git"] = str(source)
    else:
        original_selection["source"] = {"kind": "repository-goon"}

    def select(fixture: str) -> None:
        configuration = {**original_selection, "inputs": {"fixture": fixture}}
        selection.write_text(yaml.safe_dump(configuration, sort_keys=False))

    def export_result(
        run_id: str, step_id: str, destination: Path, *, expected: int = 0
    ) -> dict[str, Any]:
        report: dict[str, Any] = json.loads(
            cli(
                "runs",
                "export-artifact",
                run_id,
                "--step",
                step_id,
                "--path",
                ".showcase/result.json",
                "--output",
                str(destination),
                "--json",
                expected_returncode=expected,
            )
        )
        if expected:
            assert report["status"] == "error"
            assert not destination.exists()
        else:
            assert report["status"] == "exported"
            assert report["fleet_run_id"] == run_id
            assert report["repository_id"] == target
        return report

    history: list[tuple[str, str | None, dict[str, Any], dict[str, Any]]] = []
    run_scratch = ApplicationStatePathLayout.from_database(
        Path(environment["OH_GOON_DB"])
    ).run_workspaces
    for fixture, expected_total in (
        ("official", "1236.70"),
        ("invalid", None),
        ("changed", "60.00"),
    ):
        select(fixture)
        if git_selected and fixture == "official":
            report = json.loads(cli("workflow", "inspect", str(selection), "--acquire", "--json"))
            assert report["status"] == "inputs-valid"
            assert report["source"]["git"]["commit"] == commit
            assert report["occurrences"][0]["source"]["installation"] == "not-established"
            assert (workflow / "goon.lock").read_bytes() == initialized_lock
        cli("workflow", "prepare", str(selection))
        if git_selected:
            pins = yaml.safe_load((workflow / "goon.lock").read_text())["execution_sources"]
            assert [pin["commit"] for pin in pins] == [commit]
        receipt = json.loads(
            cli("run", str(selection), "--json", expected_returncode=0 if expected_total else 1)
        )
        assert receipt["status"] == "COMPLETED"
        run_id = receipt["fleet_run_id"]
        tape = json.loads(cli("runs", "tape", run_id, "--json"))
        outputs = json.loads(cli("runs", "outputs", run_id, "--json"))
        steps = tape["repositories"][0]["steps"]
        assert Path(tape["repositories"][0]["repository"]["path"]) == repository
        assert tape["execution"]["kind"] == "direct"
        assert [step["step_id"].rsplit(".", 1)[-1] for step in steps] == [
            "extract",
            "transform",
            "load",
        ]
        # Fresh CLI processes read retained facts after the execution owner has
        # disposed attempt source/runtime/output state. Developer state stays.
        assert run_scratch.is_dir() and list(run_scratch.iterdir()) == []
        history.append((run_id, expected_total, tape, outputs))
        assert developer_result.read_bytes() == direct_bytes
        assert developer_marker.read_bytes() == b"actual developer environment"
        assert (repository / ".venv/pyvenv.cfg").read_bytes() == developer_configuration
        assert (repository / "uv.lock").read_bytes() == native_lock
        if source_marker is not None:
            assert source_marker.read_bytes() == b"owned by the source developer"
        destination = tmp_path / f"{fixture}-result.json"
        if expected_total is None:
            assert receipt["outcome"] == "ATTENTION_REQUIRED"
            assert tape["status"] == "ATTENTION_REQUIRED"
            assert [step["status"] for step in steps] == ["PASSED", "FAILED", "SKIPPED"]
            assert steps[1]["exit_code"] != 0
            assert steps[2]["outputs"] == [] and steps[2]["artifacts"] == []
            assert outputs["repositories"][0]["steps"][2]["artifacts"] == []
            export_result(run_id, steps[2]["step_id"], destination, expected=2)
            continue
        assert receipt["outcome"] == "SUCCESS" and tape["status"] == "PASSED"
        assert [step["status"] for step in steps] == ["PASSED"] * 3
        assert [step["exit_code"] for step in steps] == [0] * 3
        result_bytes = (json.dumps({"total_order_value": expected_total}) + "\n").encode()
        artifact = outputs["repositories"][0]["steps"][2]["artifacts"][0]
        assert artifact["path"] == ".showcase/result.json" and artifact["status"] == "PRESENT"
        assert artifact["sha256"] == hashlib.sha256(result_bytes).hexdigest()
        assert artifact["byte_count"] == len(result_bytes)
        retained = artifact["retained_object"]
        assert retained["owner_repository_id"] == target
        exported = export_result(run_id, steps[2]["step_id"], destination)
        assert destination.read_bytes() == result_bytes
        assert exported["sha256"] == artifact["sha256"]
        assert exported["reference_id"] == retained["reference_id"]
        total = outputs["repositories"][0]["steps"][1]["outputs"][0]
        assert total["name"] == "TOTAL"
        assert total["sha256"] == hashlib.sha256(expected_total.encode()).hexdigest()

    assert len({run_id for run_id, _, _, _ in history}) == 3
    references = [tape["execution_reference"] for _, _, tape, _ in history]
    assert len({reference["identity"]["digest"] for reference in references}) == 3
    assert len({reference["source"]["sha256"] for reference in references}) == 1
    assert len({tape["configuration"]["caller_file_sha256"] for _, _, tape, _ in history}) == 3
    for run_id, expected_total, original_tape, original_outputs in history:
        # Later success and the current developer result cannot replace the
        # recorded selection, failed load or historical retained file bytes.
        reopened = json.loads(cli("runs", "tape", run_id, "--json"))
        for fact in ("status", "execution_reference", "configuration", "repositories"):
            assert reopened[fact] == original_tape[fact]
        reopened_outputs = json.loads(cli("runs", "outputs", run_id, "--json"))
        assert reopened_outputs["repositories"] == original_outputs["repositories"]
        load_step = reopened["repositories"][0]["steps"][2]
        destination = tmp_path / f"historical-{run_id}.json"
        if expected_total is None:
            assert reopened["status"] == "ATTENTION_REQUIRED"
            assert reopened_outputs["repositories"][0]["steps"][2]["artifacts"] == []
            export_result(run_id, load_step["step_id"], destination, expected=2)
        else:
            export_result(run_id, load_step["step_id"], destination)
            payload = destination.read_bytes()
            assert json.loads(payload) == {"total_order_value": expected_total}
            artifact = load_step["artifacts"][0]
            assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
            assert len(payload) == artifact["byte_count"]

    select("unknown-fixture")
    rejected = cli("workflow", "prepare", str(selection), expected_returncode=2)
    assert "INVALID_INPUT_VALUE" in rejected and "fixture" in rejected
    assert developer_result.read_bytes() == direct_bytes
    select("official")
    cli("workflow", "prepare", str(selection))
    if git_selected:
        changed = yaml.safe_load(selection.read_text())
        changed["source"]["plan"] = "examples/missing/plan.yml"
        selection.write_text(yaml.safe_dump(changed))
    else:
        goon = repository / "goon.yml"
        goon.write_text(goon.read_text().replace("extract --fixture", "extract --unknown"))
    cli("run", str(selection), "--json", expected_returncode=2)
    assert developer_result.read_bytes() == direct_bytes
