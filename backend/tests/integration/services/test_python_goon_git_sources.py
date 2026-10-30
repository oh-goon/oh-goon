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

SHOWCASE = Path(__file__).resolve().parents[4] / "templates/goons/general/orders-taskflow"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native Git and POSIX uv required"
)


def test_git_package_and_complete_example_keep_pins_across_cache_loss(tmp_path):
    source = tmp_path / "upstream"
    package = source / "goons/general/orders"
    shutil.copytree(
        SHOWCASE,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    example = source / "example"
    example.mkdir()
    (example / "plan.yml").write_text(
        "execution: {purpose: verification}\ngoons:\n- import: general/orders\n  from: ../goons\n"
    )

    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(source), *arguments], text=True).strip()

    git("init", "-q", "-b", "main")

    def commit():
        git("add", ".")
        git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "source",
        )
        return git("rev-parse", "HEAD")

    original = commit()
    repository = tmp_path / "repositories/target"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    env = {name: value for name, value in os.environ.items() if not name.startswith("OH_GOON_")}
    env.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
    )
    executable = Path(sys.executable).with_name("oh-goon")

    def cli(*arguments, expected=0):
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=tmp_path,
            env=env,
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

    cli("league", "add-folder", str(repository))
    target = json.loads(cli("league", "scan", "--json"))["teams"][0]["repository"]
    assert Path(target["path"]) == repository
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    selection = workflow / "run.yml"
    coordinates = {"git": str(source), "ref": "main", "path": "goons/general/orders"}
    (workflow / "plan.yml").write_text(
        yaml.safe_dump(
            {
                "execution": {"purpose": "verification"},
                "goons": [{"import": "general/orders", "from": coordinates}],
            }
        )
    )
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository": str(repository)},
            }
        )
    )
    cli("workflow", "inspect", str(selection), "--json", expected=2)
    assert not (tmp_path / "cache/oh-goon/skill-sources").exists()
    cli("workflow", "prepare", str(selection))
    lock_path = workflow / "goon.lock"
    locked = yaml.safe_load(lock_path.read_text())
    assert locked["execution_sources"][0]["commit"] == original
    assert not (package / ".venv").exists()

    def run_and_read(expected_total):
        receipt = json.loads(cli("run", str(selection), "--json"))
        assert receipt["outcome"] == "SUCCESS"
        output = json.loads(cli("runs", "outputs", receipt["fleet_run_id"], "--json"))
        steps = output["repositories"][0]["steps"]
        result = steps[2]["artifacts"][0]
        expected_bytes = (json.dumps({"total_order_value": expected_total}) + "\n").encode()
        assert result["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
        assert not (repository / ".showcase").exists()
        assert not (repository / ".venv").exists()

    run_and_read("1236.70")
    (package / "src/orders_pipeline/data/official.json").write_text('{"1001":5.00}\n')
    changed = commit()
    cli("workflow", "prepare", str(selection))
    assert yaml.safe_load(lock_path.read_text()) == locked
    shutil.rmtree(tmp_path / "cache/oh-goon/skill-sources")
    run_and_read("1236.70")
    offline = tmp_path / "offline-upstream"
    source.rename(offline)
    try:
        cli("workflow", "inspect", str(selection), "--json")
    finally:
        offline.rename(source)
    cli("workflow", "prepare", str(selection), "--refresh", "goons")
    assert yaml.safe_load(lock_path.read_text())["execution_sources"][0]["commit"] == changed
    run_and_read("5.00")

    # A complete example names both its authoring root and exact Plan. Its local
    # imports remain in the same revision, without guessing a nearby Plan.
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "source": {
                    "kind": "git-plan",
                    "git": str(source),
                    "ref": "main",
                    "path": ".",
                    "plan": "example/plan.yml",
                },
                "target": {"repository": str(repository)},
            }
        )
    )
    cli("workflow", "prepare", str(selection))
    complete = yaml.safe_load(lock_path.read_text())
    assert complete["execution_sources"][0]["kind"] == "plan"
    assert complete["execution_sources"][0]["commit"] == changed
    run_and_read("5.00")
