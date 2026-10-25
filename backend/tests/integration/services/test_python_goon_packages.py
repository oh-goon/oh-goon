from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from oh_goon.cli import main
from oh_goon.core.product_identity import repository_id
from oh_goon.runtime.league_folders import LeagueFolderStore

PROBE = """import argparse
import importlib.util
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    Path("label.txt").write_text(args.label)
    Path("tools.txt").write_text("present" if importlib.util.find_spec("pytest") else "absent")
    Path("cwd.txt").write_text(str(Path.cwd()))
    return 0
"""


@pytest.mark.parametrize("working_directory", (".", "scratch/results"))
def test_package_groups_repeated_imports_and_no_ambient_command_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    working_directory: str,
) -> None:
    repository = tmp_path / "repositories/target"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    LeagueFolderStore().replace((repository,))
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/probe"
    implementation = package / "src/probe"
    implementation.mkdir(parents=True)
    (implementation / "__init__.py").write_text(PROBE)
    (package / "README.md").write_text("# general/probe\n\nObserve the installed environment.\n")
    (package / "pyproject.toml").write_text("""[project]
name = "probe"
version = "0.1.0"
requires-python = ">=3.12"
[project.scripts]
environment-probe = "probe:main"
[dependency-groups]
dev = ["pytest>=8"]
[build-system]
requires = ["uv_build>=0.10.0,<0.11.0"]
build-backend = "uv_build"
""")
    lock = subprocess.run(
        ["uv", "lock", "--project", str(package), "--offline"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert lock.returncode == 0, lock.stderr
    developer = package / ".venv/keep"
    developer.parent.mkdir()
    developer.write_text("developer environment")
    goon = package / "goon.yml"
    specification: dict[str, Any] = {
        "runtime": "python",
        "inputs": {"label": "value"},
        "steps": [
            {
                "run": "environment-probe --label ${{ inputs.label }}",
                "working_directory": working_directory,
                "outputs": {"LABEL": "label.txt", "TOOLS": "tools.txt", "CWD": "cwd.txt"},
                "artifacts": [{"path": "label.txt", "retention": "evidence"}],
            }
        ],
    }
    goon.write_text(yaml.safe_dump(specification))
    plan = workflow / "plan.yml"
    plan.write_text(
        yaml.safe_dump(
            {
                "execution": {"purpose": "verification"},
                "goons": [
                    {"import": "general/probe", "from": "./goons", "with": {"label": label}}
                    for label in ("first", "second")
                ],
            }
        )
    )
    selection = workflow / "run.yml"
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": repository_id(repository)},
            }
        )
    )

    def run(expected: int = 0) -> list[dict[str, Any]]:
        assert main(["lock", str(selection)]) == 0
        capsys.readouterr()
        assert main(["run", str(selection), "--json"]) == expected
        receipt = json.loads(capsys.readouterr().out)
        assert main(["plan", "outputs", receipt["fleet_run_id"], "--json"]) == 0
        return cast(
            list[dict[str, Any]], json.loads(capsys.readouterr().out)["repositories"][0]["steps"]
        )

    steps = run()
    outputs = [{item["name"]: item for item in step["outputs"]} for step in steps]
    for label, output in zip(("first", "second"), outputs, strict=True):
        assert output["LABEL"]["sha256"] == hashlib.sha256(label.encode()).hexdigest()
        assert output["TOOLS"]["sha256"] == hashlib.sha256(b"absent").hexdigest()
    for label, step in zip(("first", "second"), steps, strict=True):
        artifact = step["artifacts"][0]
        assert artifact["status"] == "PRESENT"
        assert artifact["path"] == (Path(working_directory) / "label.txt").as_posix()
        assert artifact["sha256"] == hashlib.sha256(label.encode()).hexdigest()
    assert outputs[0]["CWD"]["sha256"] != outputs[1]["CWD"]["sha256"]
    assert developer.read_text() == "developer environment"
    assert not (repository / ".venv").exists()
    assert not (repository / "label.txt").exists()
    assert not (package / "label.txt").exists()
    assert not (repository / "scratch").exists()
    assert not (package / "scratch").exists()
    locked_source = (workflow / "goon.lock").read_bytes()
    for name in ("label.txt", "tools.txt", "cwd.txt"):
        generated_output = package / working_directory / name
        generated_output.parent.mkdir(parents=True, exist_ok=True)
        generated_output.write_text("local generated output")
    assert main(["lock", str(selection)]) == 0
    capsys.readouterr()
    assert (workflow / "goon.lock").read_bytes() == locked_source

    specification["uv"] = {"group": "dev"}
    goon.write_text(yaml.safe_dump(specification))
    for step in run():
        output = next(item for item in step["outputs"] if item["name"] == "TOOLS")
        assert output["sha256"] == hashlib.sha256(b"present").hexdigest()
    assert developer.read_text() == "developer environment"

    # `true` exists on the host, but is not an installed project command.
    specification["steps"] = [{"run": "true"}]
    goon.write_text(yaml.safe_dump(specification))
    failed = run(expected=1)
    assert all(step["outputs"] == [] for step in failed)
