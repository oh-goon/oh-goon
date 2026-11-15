import hashlib
import json
import math
import os
import sys

import pytest
from iris_train import cli, dataset
from sklearn.datasets import load_iris

CANARY = "PRIVATE_CAPTURED_DATASET_RECORD"
ARGUMENTS = [
    "iris-train",
    "--random-seed",
    "42",
    "--test-size-percent",
    "20",
    "--minimum-accuracy-percent",
    "90",
    "--max-iterations",
    "300",
]


@pytest.fixture
def resource(tmp_path, monkeypatch):
    package = tmp_path / "package"
    (package / "data").mkdir(parents=True)
    monkeypatch.setattr(dataset, "__file__", str(package / "dataset.py"))
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.chdir(output)
    monkeypatch.setattr(sys, "argv", [*ARGUMENTS, "--dataset", "captured"])
    return package / "data/training.json"


def payload():
    data, labels = load_iris(return_X_y=True)
    return {
        "data": data.tolist(),
        "target": labels.tolist(),
        "record_ids": [f"{CANARY}-{index}" for index in range(len(labels))],
    }


@pytest.mark.parametrize("at_byte_bound", (False, True))
@pytest.mark.parametrize("training_rounds", (1, 3))
def test_captured_resource_trains_native_model_and_records_only_dataset_identity(
    resource, capsys, at_byte_bound, training_rounds
):
    sys.argv.extend(["--training-rounds", str(training_rounds)])
    raw = json.dumps(payload(), separators=(",", ":")).encode()
    if at_byte_bound:
        raw += b" " * (dataset.MAX_DATASET_BYTES - len(raw))
        assert len(raw) == dataset.MAX_DATASET_BYTES
    resource.write_bytes(raw)
    assert cli.main() == 0
    metrics = json.loads(
        (resource.parents[2] / "output/ml-output/iris-training-metrics.json").read_text()
    )
    parameters = json.loads(
        (resource.parents[2] / "output/ml-output/iris-model-parameters.json").read_text()
    )
    assert metrics["dataset_sha256"] == hashlib.sha256(raw).hexdigest()
    assert metrics["training_examples"] == 120 and metrics["test_examples"] == 30
    assert metrics["accuracy"] >= metrics["minimum_accuracy"] == 0.9
    assert parameters["coefficients"]
    if training_rounds > 1:
        assert metrics["training_rounds"] == metrics["completed_training_rounds"] == 3
    else:
        assert "training_rounds" not in metrics
        assert "completed_training_rounds" not in metrics
    captured = capsys.readouterr()
    assert CANARY not in json.dumps(metrics) + json.dumps(parameters) + captured.out + captured.err


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "malformed",
        "encoding",
        "nonfinite",
        "duplicate",
        "shape",
        "target-count",
        "boolean-label",
        "boolean-feature",
        "too-few",
        "record-count",
        "record-bound",
        "unknown-field",
        "oversized",
    ],
)
def test_invalid_resource_refuses_before_fitting_without_private_rows(
    resource, monkeypatch, capsys, fault
):
    value = payload()
    raw = None
    if fault == "malformed":
        raw = (CANARY + "{").encode()
    elif fault == "encoding":
        raw = b"\xff" + CANARY.encode()
    elif fault == "nonfinite":
        value["data"][0][0] = math.inf
    elif fault == "duplicate":
        raw = b'{"data":[],"data":[],"target":[],"record_ids":["' + CANARY.encode() + b'"]}'
    elif fault == "shape":
        value["data"][0].pop()
    elif fault == "target-count":
        value["target"].pop()
    elif fault == "boolean-label":
        value["target"][0] = True
    elif fault == "boolean-feature":
        value["data"][0][0] = True
    elif fault == "too-few":
        value = {name: rows[:29] for name, rows in value.items()}
    elif fault == "record-count":
        value["record_ids"].pop()
    elif fault == "record-bound":
        value["record_ids"][0] = CANARY * 20
    elif fault == "unknown-field":
        value[CANARY] = "not a dataset field"
    elif fault == "oversized":
        raw = json.dumps(value).encode()
        raw += b" " * (dataset.MAX_DATASET_BYTES + 1 - len(raw))
        assert len(raw) == dataset.MAX_DATASET_BYTES + 1
    if fault != "missing":
        resource.write_bytes(raw if raw is not None else json.dumps(value).encode())

    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid data reached native model fitting")

    monkeypatch.setattr(cli.LogisticRegression, "fit", forbidden)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert dataset.DatasetError.code in captured.err and CANARY not in captured.err + captured.out
    assert not (resource.parents[2] / "output/ml-output").exists()


@pytest.mark.parametrize("kind", ["file-symlink", "directory-symlink", "fifo"])
def test_unsafe_resource_is_refused_without_blocking_or_following(
    resource, monkeypatch, capsys, kind
):
    outside = resource.parents[2] / "outside"
    outside.mkdir()
    target = outside / "training.json"
    target.write_text(json.dumps(payload()))
    if kind == "file-symlink":
        resource.symlink_to(target)
    elif kind == "directory-symlink":
        resource.parent.rmdir()
        resource.parent.symlink_to(outside, target_is_directory=True)
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("native FIFO required")
        os.mkfifo(resource)

    def forbidden(*_args, **_kwargs):
        pytest.fail("unsafe resource reached a data read")

    monkeypatch.setattr(dataset.os, "fdopen", forbidden)
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert dataset.DatasetError.code in captured.err and CANARY not in captured.out + captured.err
    assert target.read_text() == json.dumps(payload())
    assert not (resource.parents[2] / "output/ml-output").exists()


@pytest.mark.parametrize(
    "selection",
    ["/private/" + CANARY, "https://user:" + CANARY + "@example.invalid/data", CANARY],
)
def test_unsupported_dataset_selection_has_no_raw_echo_or_fallback(
    tmp_path, monkeypatch, capsys, selection
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [*ARGUMENTS, "--dataset", selection])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 2
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    assert not (tmp_path / "ml-output").exists()
