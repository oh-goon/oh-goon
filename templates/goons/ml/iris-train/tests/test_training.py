import json
import subprocess
import sys
import sysconfig
from functools import wraps
from pathlib import Path

import pytest


def arguments(*overrides: str) -> list[str]:
    return [
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
    ]


def command(directory: Path, *overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments(*overrides),
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_repeated_seed_reproduces_training_parameters(tmp_path):
    first = command(tmp_path)
    assert first.returncode == 0, first.stderr
    original = json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())
    original_metrics = (tmp_path / "ml-output/iris-training-metrics.json").read_bytes()
    second = command(tmp_path, "--training-rounds", "1")
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout == ""
    assert (tmp_path / "ml-output/iris-training-metrics.json").read_bytes() == original_metrics
    assert json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text()) == original


def test_metrics_record_training_choices_and_changed_seed_changes_model(tmp_path):
    first = command(tmp_path, "--test-size-percent", "30", "--max-iterations", "200")
    assert first.returncode == 0, first.stderr
    metrics = json.loads((tmp_path / "ml-output/iris-training-metrics.json").read_text())
    original = json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())
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
    changed = json.loads((tmp_path / "ml-output/iris-training-metrics.json").read_text())
    assert changed["random_seed"] == 7
    assert changed["test_size_percent"] == 30
    assert changed["max_iterations"] == 200
    assert json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text()) != original


def test_failed_accuracy_preserves_measured_metrics_and_model(tmp_path):
    result = command(tmp_path, "--minimum-accuracy-percent", "100")
    assert result.returncode == 1, result.stderr
    metrics = json.loads((tmp_path / "ml-output/iris-training-metrics.json").read_text())
    assert metrics["accuracy"] < metrics["minimum_accuracy"] == 1
    assert json.loads((tmp_path / "ml-output/iris-model-parameters.json").read_text())[
        "coefficients"
    ]


def test_multiple_rounds_preserve_the_deterministic_model_and_report_completed_work(
    tmp_path,
):
    control = command(tmp_path)
    assert control.returncode == 0, control.stderr
    assert control.stdout == ""
    original_metrics = json.loads((tmp_path / "ml-output/iris-training-metrics.json").read_text())
    original_model = (tmp_path / "ml-output/iris-model-parameters.json").read_bytes()

    result = command(tmp_path, "--training-rounds", "3")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "OH-GOON ACTIVITY: IRIS_TRAINING_STARTED rounds=3",
        "OH-GOON ACTIVITY: IRIS_TRAINING_PROGRESS completed=1 total=3",
        "OH-GOON ACTIVITY: IRIS_TRAINING_PROGRESS completed=2 total=3",
        "OH-GOON ACTIVITY: IRIS_TRAINING_PROGRESS completed=3 total=3",
    ]
    metrics = json.loads((tmp_path / "ml-output/iris-training-metrics.json").read_text())
    assert metrics == {
        **original_metrics,
        "training_rounds": 3,
        "completed_training_rounds": 3,
    }
    assert (tmp_path / "ml-output/iris-model-parameters.json").read_bytes() == original_model


def test_multiple_rounds_keep_measured_failure_distinct_from_completed_training(tmp_path):
    result = command(tmp_path, "--training-rounds", "3", "--minimum-accuracy-percent", "100")
    assert result.returncode == 1, result.stderr
    assert result.stdout.splitlines()[-1] == (
        "OH-GOON ACTIVITY: IRIS_TRAINING_PROGRESS completed=3 total=3"
    )
    metrics = json.loads((tmp_path / "ml-output/iris-training-metrics.json").read_text())
    assert metrics["training_rounds"] == metrics["completed_training_rounds"] == 3
    assert metrics["accuracy"] < metrics["minimum_accuracy"] == 1
    assert (tmp_path / "ml-output/iris-model-parameters.json").is_file()


@pytest.mark.parametrize("training_rounds", (1, 3))
def test_every_completed_round_performs_a_native_fit(tmp_path, monkeypatch, training_rounds):
    from iris_train import cli
    from sklearn.linear_model import LogisticRegression

    native_fit = LogisticRegression.fit
    completed = 0

    @wraps(native_fit)
    def observe_fit(*args, **kwargs):
        nonlocal completed
        result = native_fit(*args, **kwargs)
        completed += 1
        return result

    monkeypatch.setattr(LogisticRegression, "fit", observe_fit)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", arguments("--training-rounds", str(training_rounds)))
    assert cli.main() == 0
    assert completed == training_rounds


def test_failed_native_fit_reports_only_completed_work_before_publication(
    tmp_path, monkeypatch, capsys
):
    from iris_train import cli
    from sklearn.linear_model import LogisticRegression

    native_fit = LogisticRegression.fit
    attempted = 0
    completed = 0

    @wraps(native_fit)
    def fail_second_fit(*args, **kwargs):
        nonlocal attempted, completed
        attempted += 1
        if attempted == 2:
            raise RuntimeError("native fitting failed after one completed round")
        result = native_fit(*args, **kwargs)
        completed += 1
        return result

    monkeypatch.setattr(LogisticRegression, "fit", fail_second_fit)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", arguments("--training-rounds", "3"))
    with pytest.raises(RuntimeError, match="native fitting failed"):
        cli.main()
    assert attempted == 2 and completed == 1
    assert capsys.readouterr().out.splitlines() == [
        "OH-GOON ACTIVITY: IRIS_TRAINING_STARTED rounds=3",
        "OH-GOON ACTIVITY: IRIS_TRAINING_PROGRESS completed=1 total=3",
    ]
    assert not (tmp_path / "ml-output").exists()


@pytest.mark.parametrize(
    "argument,value",
    [
        ("--test-size-percent", "50"),
        ("--random-seed", "-1"),
        ("--max-iterations", "1"),
        ("--training-rounds", "0"),
        ("--training-rounds", "10001"),
    ],
)
def test_invalid_application_arguments_refuse_before_output(tmp_path, argument, value):
    result = command(tmp_path, argument, value)
    assert result.returncode == 2
    assert argument in result.stderr
    assert not (tmp_path / "ml-output").exists()
