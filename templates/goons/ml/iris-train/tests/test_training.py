import json
import subprocess
import sysconfig
from pathlib import Path

import pytest


def command(directory: Path, *overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(Path(sysconfig.get_path("scripts")) / "iris-train"),
            "--random-seed",
            "42",
            "--test-size-percent",
            "20",
            "--minimum-accuracy-percent",
            "90",
            "--max-iterations",
            "300",
            *overrides,
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_repeated_seed_reproduces_training_parameters(tmp_path):
    first = command(tmp_path)
    assert first.returncode == 0, first.stderr
    original = json.loads(
        (tmp_path / "ml-output/iris-model-parameters.json").read_text()
    )
    second = command(tmp_path)
    assert second.returncode == 0, second.stderr
    assert (
        json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())
        == original
    )


def test_metrics_record_training_choices_and_changed_seed_changes_model(tmp_path):
    first = command(tmp_path, "--test-size-percent", "30", "--max-iterations", "200")
    assert first.returncode == 0, first.stderr
    metrics = json.loads(
        (tmp_path / "ml-output/iris-training-metrics.json").read_text()
    )
    original = json.loads(
        (tmp_path / "ml-output/iris-model-parameters.json").read_text()
    )
    assert metrics["random_seed"] == 42
    assert metrics["test_size_percent"] == 30
    assert metrics["minimum_accuracy"] == 0.9
    assert metrics["max_iterations"] == 200
    assert metrics["training_examples"] == 105
    assert metrics["test_examples"] == 45
    assert 0.9 <= metrics["accuracy"] <= 1
    second = command(
        tmp_path,
        "--random-seed",
        "7",
        "--test-size-percent",
        "30",
        "--max-iterations",
        "200",
    )
    assert second.returncode == 0, second.stderr
    changed = json.loads(
        (tmp_path / "ml-output/iris-training-metrics.json").read_text()
    )
    assert changed["random_seed"] == 7
    assert changed["test_size_percent"] == 30
    assert changed["max_iterations"] == 200
    assert (
        json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())
        != original
    )


def test_failed_accuracy_preserves_measured_metrics_and_model(tmp_path):
    result = command(tmp_path, "--minimum-accuracy-percent", "100")
    assert result.returncode == 1, result.stderr
    metrics = json.loads(
        (tmp_path / "ml-output/iris-training-metrics.json").read_text()
    )
    assert metrics["accuracy"] < metrics["minimum_accuracy"] == 1
    assert json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())[
        "coefficients"
    ]


@pytest.mark.parametrize(
    "argument,value",
    [("--test-size-percent", "50"), ("--random-seed", "-1"), ("--max-iterations", "1")],
)
def test_invalid_application_arguments_refuse_before_output(tmp_path, argument, value):
    result = command(tmp_path, argument, value)
    assert result.returncode == 2
    assert argument in result.stderr
    assert not (tmp_path / "ml-output").exists()
