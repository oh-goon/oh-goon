import json
import os
from pathlib import Path
import subprocess
import sysconfig

import pytest


def run_command(workdir, *args):
    suffix = ".exe" if os.name == "nt" else ""
    command = Path(sysconfig.get_path("scripts")) / ("orders-pipeline" + suffix)
    return subprocess.run(
        [str(command), *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("fixture", "expected"), [("official", "1236.70"), ("changed", "60.00")]
)
def test_installed_commands_exchange_outputs(tmp_path, fixture, expected):
    extracted = run_command(tmp_path, "extract", "--fixture", fixture)
    assert extracted.returncode == 0, extracted.stderr
    output = tmp_path / ".showcase"
    payload = (output / "extracted.json").read_text(encoding="utf8").strip()
    transformed = run_command(tmp_path, "transform", "--data", payload)
    assert transformed.returncode == 0, transformed.stderr
    total = (output / "total.txt").read_text(encoding="utf8").strip()
    loaded = run_command(tmp_path, "load", "--total", total)
    assert loaded.returncode == 0, loaded.stderr
    assert json.loads((output / "result.json").read_text(encoding="utf8")) == {
        "total_order_value": expected
    }
    assert expected in loaded.stdout


def test_transform_failure_does_not_publish_success(tmp_path):
    extracted = run_command(tmp_path, "extract", "--fixture", "invalid")
    assert extracted.returncode == 0, extracted.stderr
    output = tmp_path / ".showcase"
    payload = (output / "extracted.json").read_text(encoding="utf8").strip()
    transformed = run_command(tmp_path, "transform", "--data", payload)
    assert transformed.returncode != 0
    assert "Invalid amount" in transformed.stderr
    assert not (output / "total.txt").exists()
    assert not (output / "result.json").exists()


def test_extract_requires_explicit_fixture(tmp_path):
    result = run_command(tmp_path, "extract")
    assert result.returncode != 0
    assert "--fixture" in result.stderr
    assert not (tmp_path / ".showcase").exists()


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [("official", "1236.70"), ("invalid", None), ("changed", "60.00")],
)
def test_direct_workflow_uses_shared_operations_and_stops_on_failure(
    tmp_path, fixture, expected
):
    result = run_command(tmp_path, "run", "--fixture", fixture)
    output = tmp_path / ".showcase"
    assert (output / "extracted.json").is_file()
    if expected is None:
        assert result.returncode != 0
        assert "Invalid amount" in result.stderr
        assert not (output / "total.txt").exists()
        assert not (output / "result.json").exists()
    else:
        assert result.returncode == 0, result.stderr
        assert (output / "total.txt").read_text(encoding="utf8").strip() == expected
        assert json.loads((output / "result.json").read_text(encoding="utf8")) == {
            "total_order_value": expected
        }
