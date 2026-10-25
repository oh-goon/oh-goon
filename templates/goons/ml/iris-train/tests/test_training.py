import json
import subprocess
import sysconfig
from pathlib import Path

import pytest


def command(directory: Path, *overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(Path(sysconfig.get_path("scripts")) / "iris-train"),
         "--random-seed", "42", "--test-size-percent", "20",
         "--minimum-accuracy-percent", "90", "--max-iterations", "300", *overrides],
        cwd=directory, capture_output=True, text=True, timeout=60,
    )


def test_repeated_seed_reproduces_training_parameters(tmp_path):
    first = command(tmp_path)
    assert first.returncode == 0, first.stderr
    original = json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())
    second = command(tmp_path)
    assert second.returncode == 0, second.stderr
    assert json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text()) == original


@pytest.mark.parametrize("argument,value", [("--test-size-percent", "50"), ("--random-seed", "-1"), ("--max-iterations", "1")])
def test_invalid_application_arguments_refuse_before_output(tmp_path, argument, value):
    result = command(tmp_path, argument, value)
    assert result.returncode == 2
    assert argument in result.stderr
    assert not (tmp_path / "ml-output").exists()
