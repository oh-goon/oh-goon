from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SHOWCASE = Path(__file__).resolve().parents[4] / "examples/airflow-taskflow-showcase"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


def test_repository_showcase_executes_output_handoffs_and_reads_back_failures(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repositories/team/orders"
    shutil.copytree(
        SHOWCASE,
        repository,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )
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

    cli("league", "add-folder", str(tmp_path / "repositories"))
    league = json.loads(cli("league", "scan", "--json"))
    target = league["teams"][0]["repository"]
    assert Path(target["path"]) == repository
    selection = repository / "run.yml"
    original_selection = yaml.safe_load(selection.read_text())

    def select(fixture: str) -> None:
        configuration = {
            **original_selection,
            "target": {"repository_id": target["repository_id"]},
            "inputs": {"fixture": fixture},
        }
        selection.write_text(yaml.safe_dump(configuration, sort_keys=False))

    for fixture, expected_total in (("official", "1236.70"), ("changed", "60.00")):
        select(fixture)
        cli("lock", str(selection))
        receipt = json.loads(cli("run", str(selection), "--json"))
        assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
        run_id = receipt["fleet_run_id"]
        # Each command reopens the original durable Journal in a new process.
        tape = json.loads(cli("plan", "tape", run_id, "--json"))
        outputs = json.loads(cli("plan", "outputs", run_id, "--json"))
        assert tape["status"] == "PASSED"
        steps = tape["repositories"][0]["steps"]
        assert [step["status"] for step in steps] == ["PASSED"] * 3
        assert [step["exit_code"] for step in steps] == [0] * 3
        result_path = repository / ".showcase/result.json"
        result_bytes = result_path.read_bytes()
        assert json.loads(result_bytes) == {"total_order_value": expected_total}
        artifact = outputs["repositories"][0]["steps"][2]["artifacts"][0]
        assert artifact["path"] == ".showcase/result.json"
        assert artifact["status"] == "PRESENT"
        assert artifact["sha256"] == hashlib.sha256(result_bytes).hexdigest()
        assert artifact["byte_count"] == len(result_bytes)
        total = outputs["repositories"][0]["steps"][1]["outputs"][0]
        assert total["name"] == "TOTAL"
        assert total["sha256"] == hashlib.sha256(expected_total.encode()).hexdigest()

    shutil.rmtree(repository / ".showcase")
    select("invalid")
    cli("lock", str(selection))
    failed = json.loads(cli("run", str(selection), "--json", expected_returncode=1))
    tape = json.loads(cli("plan", "tape", failed["fleet_run_id"], "--json"))
    assert tape["status"] == "ATTENTION_REQUIRED"
    steps = tape["repositories"][0]["steps"]
    assert [step["status"] for step in steps] == ["PASSED", "FAILED", "SKIPPED"]
    assert steps[1]["exit_code"] != 0
    assert steps[2]["outputs"] == []
    assert not (repository / ".showcase/result.json").exists()

    # Invalid caller values refuse before extraction can create output state.
    shutil.rmtree(repository / ".showcase")
    select("unknown-fixture")
    rejected = cli("lock", str(selection), expected_returncode=2)
    assert "INVALID_INPUT_VALUE" in rejected and "fixture" in rejected
    assert not (repository / ".showcase").exists()

    select("official")
    cli("lock", str(selection))
    source = repository / "goon.yml"
    source.write_text(source.read_text().replace("extract --fixture", "extract --unknown"))
    cli("run", str(selection), "--json", expected_returncode=2)
    assert not (repository / ".showcase").exists()
