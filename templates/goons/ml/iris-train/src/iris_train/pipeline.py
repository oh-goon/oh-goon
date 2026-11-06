"""Bounded Iris dataflow through independently invoked native ML commands."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import sklearn
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import StandardScaler

MAX_PACKET_BYTES = 16_384
_DATA_SCHEMA = "iris-pipeline-data/v1"
_MODEL_SCHEMA = "iris-pipeline-model/v1"
_VERDICT_SCHEMA = "iris-pipeline-evaluation/v1"
_DATA_FIELDS = {
    "schema",
    "random_seed",
    "test_size_percent",
    "train_data",
    "train_labels",
    "test_data",
    "test_labels",
    "scikit_learn_version",
    "data_sha256",
}
_MODEL_FIELDS = {
    "schema",
    "data_sha256",
    "random_seed",
    "test_size_percent",
    "max_iterations",
    "training_examples",
    "test_examples",
    "scikit_learn_version",
    "parameters",
    "model_packet_sha256",
}
_PARAMETER_FIELDS = {
    "scaler_mean",
    "scaler_scale",
    "scaler_variance",
    "scaler_samples",
    "classes",
    "coefficients",
    "intercepts",
    "iterations",
    "n_features",
    "scikit_learn_version",
}
_VERDICT_FIELDS = {
    "schema",
    "data_sha256",
    "model_packet_sha256",
    "accuracy",
    "minimum_accuracy",
    "accepted",
    "scikit_learn_version",
}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # Parser errors must not echo a caller's JSON packet or unknown argv.
        self.exit(2, "iris-pipeline: invalid arguments; see --help\n")


def _json_bytes(value: object) -> bytes:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(payload) + 1 > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds byte budget")
    return payload


def _digest(value: dict[str, Any], field: str) -> str:
    return hashlib.sha256(
        _json_bytes({k: v for k, v in value.items() if k != field})
    ).hexdigest()


def _hash(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("invalid digest")
    return value


def _integer(value: object, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError("invalid integer")
    return value


def _number(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid finite number")
    return float(value)


def _vector(value: object, length: int) -> np.ndarray:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError("invalid vector")
    return np.asarray([_number(item) for item in value], dtype=float)


def _matrix(value: object, rows: int) -> np.ndarray:
    if not isinstance(value, list) or len(value) != rows:
        raise ValueError("invalid matrix")
    return np.asarray([_vector(row, 4) for row in value], dtype=float)


def _labels(value: object, rows: int) -> np.ndarray:
    if not isinstance(value, list) or len(value) != rows:
        raise ValueError("invalid labels")
    labels = [_integer(item, 0, 2) for item in value]
    if set(labels) != {0, 1, 2}:
        raise ValueError("missing Iris classes")
    return np.asarray(labels, dtype=int)


def _partition_counts(percent: int) -> tuple[int, int]:
    # The codec records the native float test_size argument. Its held-out count
    # uses ceil, including the exact float passed to train_test_split.
    test_rows = math.ceil(150 * (percent / 100))
    return 150 - test_rows, test_rows


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate packet field")
        value[name] = item
    return value


def _packet(text: str, schema: str, fields: set[str]) -> dict[str, Any]:
    if len(text) > MAX_PACKET_BYTES or len(text.encode("utf-8")) > MAX_PACKET_BYTES:
        raise ValueError("packet exceeds byte budget")
    packet = json.loads(text, object_pairs_hook=_unique_object)
    if (
        not isinstance(packet, dict)
        or set(packet) != fields
        or packet["schema"] != schema
    ):
        raise ValueError("invalid packet structure")
    if packet["scikit_learn_version"] != sklearn.__version__:
        raise ValueError("native library version mismatch")
    return packet


def _data(text: str) -> dict[str, Any]:
    packet = _packet(text, _DATA_SCHEMA, _DATA_FIELDS)
    _integer(packet["random_seed"], 0, 2147483647)
    percent = _integer(packet["test_size_percent"], 10, 40)
    train_rows = (
        len(packet["train_data"]) if isinstance(packet["train_data"], list) else 0
    )
    test_rows = len(packet["test_data"]) if isinstance(packet["test_data"], list) else 0
    if (train_rows, test_rows) != _partition_counts(percent):
        raise ValueError("invalid Iris partition")
    _matrix(packet["train_data"], train_rows)
    _matrix(packet["test_data"], test_rows)
    _labels(packet["train_labels"], train_rows)
    _labels(packet["test_labels"], test_rows)
    if _hash(packet["data_sha256"]) != _digest(packet, "data_sha256"):
        raise ValueError("data correspondence mismatch")
    return packet


def _model(text: str) -> dict[str, Any]:
    packet = _packet(text, _MODEL_SCHEMA, _MODEL_FIELDS)
    _hash(packet["data_sha256"])
    _integer(packet["random_seed"], 0, 2147483647)
    percent = _integer(packet["test_size_percent"], 10, 40)
    _integer(packet["max_iterations"], 100, 2000)
    train_rows = _integer(packet["training_examples"], 90, 135)
    test_rows = _integer(packet["test_examples"], 15, 60)
    if (train_rows, test_rows) != _partition_counts(percent):
        raise ValueError("invalid Iris partition")
    parameters = packet["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != _PARAMETER_FIELDS:
        raise ValueError("invalid model parameters")
    if parameters["scikit_learn_version"] != sklearn.__version__:
        raise ValueError("native library version mismatch")
    _integer(parameters["n_features"], 4, 4)
    _integer(parameters["scaler_samples"], train_rows, train_rows)
    _vector(parameters["scaler_mean"], 4)
    if (_vector(parameters["scaler_scale"], 4) <= 0).any():
        raise ValueError("invalid scaler scale")
    if (_vector(parameters["scaler_variance"], 4) < 0).any():
        raise ValueError("invalid scaler variance")
    classes = parameters["classes"]
    if not isinstance(classes, list) or len(classes) != 3:
        raise ValueError("invalid classes")
    if [_integer(item, 0, 2) for item in classes] != [0, 1, 2]:
        raise ValueError("invalid Iris class order")
    _matrix(parameters["coefficients"], 3)
    _vector(parameters["intercepts"], 3)
    iterations = parameters["iterations"]
    if not isinstance(iterations, list) or len(iterations) != 1:
        raise ValueError("invalid iteration count")
    _integer(iterations[0], 1, packet["max_iterations"])
    if _hash(packet["model_packet_sha256"]) != _digest(packet, "model_packet_sha256"):
        raise ValueError("model correspondence mismatch")
    return packet


def _restore(packet: dict[str, Any]) -> Pipeline:
    """Restore this example's documented fitted attributes, then use native APIs."""
    parameters = packet["parameters"]
    scaler = StandardScaler()
    scaler.mean_ = np.asarray(parameters["scaler_mean"], dtype=float)
    scaler.scale_ = np.asarray(parameters["scaler_scale"], dtype=float)
    scaler.var_ = np.asarray(parameters["scaler_variance"], dtype=float)
    scaler.n_features_in_ = parameters["n_features"]
    scaler.n_samples_seen_ = parameters["scaler_samples"]
    classifier = LogisticRegression(
        max_iter=packet["max_iterations"], random_state=packet["random_seed"]
    )
    classifier.classes_ = np.asarray(parameters["classes"], dtype=int)
    classifier.coef_ = np.asarray(parameters["coefficients"], dtype=float)
    classifier.intercept_ = np.asarray(parameters["intercepts"], dtype=float)
    classifier.n_iter_ = np.asarray(parameters["iterations"], dtype=int)
    classifier.n_features_in_ = parameters["n_features"]
    return make_pipeline(scaler, classifier)


def _destinations(directory: str, *names: str) -> list[Path]:
    root = Path(directory)
    if root.is_symlink() or root.exists() and not root.is_dir():
        raise ValueError("output directory unavailable")
    targets = [root / name for name in names]
    if any(path.exists() or path.is_symlink() for path in targets):
        raise ValueError("output already exists")
    return targets


def _write_json(path: Path, value: object) -> None:
    payload = _json_bytes(value)
    path.parent.mkdir(exist_ok=True)
    with path.open("xb") as output:
        output.write(payload + b"\n")


def _ingest(args: argparse.Namespace) -> int:
    (destination,) = _destinations("ml-pipeline", "data.json")
    data, labels = load_iris(return_X_y=True)
    train_data, test_data, train_labels, test_labels = train_test_split(
        data,
        labels,
        test_size=args.test_size_percent / 100,
        stratify=labels,
        random_state=args.random_seed,
    )
    packet = {
        "schema": _DATA_SCHEMA,
        "random_seed": args.random_seed,
        "test_size_percent": args.test_size_percent,
        "train_data": train_data.tolist(),
        "train_labels": train_labels.tolist(),
        "test_data": test_data.tolist(),
        "test_labels": test_labels.tolist(),
        "scikit_learn_version": sklearn.__version__,
    }
    packet["data_sha256"] = _digest(packet, "data_sha256")
    _data(_json_bytes(packet).decode())
    _write_json(destination, packet)
    return 0


def _train(args: argparse.Namespace) -> int:
    (destination,) = _destinations("ml-pipeline", "model.json")
    data = _data(args.data)
    scaler = StandardScaler()
    classifier = LogisticRegression(
        max_iter=args.max_iterations, random_state=data["random_seed"]
    )
    pipeline = make_pipeline(scaler, classifier)
    pipeline.fit(data["train_data"], data["train_labels"])
    packet = {
        "schema": _MODEL_SCHEMA,
        "data_sha256": data["data_sha256"],
        "random_seed": data["random_seed"],
        "test_size_percent": data["test_size_percent"],
        "max_iterations": args.max_iterations,
        "training_examples": len(data["train_data"]),
        "test_examples": len(data["test_data"]),
        "scikit_learn_version": sklearn.__version__,
        "parameters": {
            "scaler_mean": scaler.mean_.tolist(),
            "scaler_scale": scaler.scale_.tolist(),
            "scaler_variance": scaler.var_.tolist(),
            "scaler_samples": int(scaler.n_samples_seen_),
            "classes": classifier.classes_.tolist(),
            "coefficients": classifier.coef_.tolist(),
            "intercepts": classifier.intercept_.tolist(),
            "iterations": classifier.n_iter_.tolist(),
            "n_features": int(classifier.n_features_in_),
            "scikit_learn_version": sklearn.__version__,
        },
    }
    packet["model_packet_sha256"] = _digest(packet, "model_packet_sha256")
    _model(_json_bytes(packet).decode())
    _write_json(destination, packet)
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    (destination,) = _destinations("ml-pipeline", "evaluation.json")
    data, model = _data(args.data), _model(args.model)
    if (
        model["data_sha256"] != data["data_sha256"]
        or model["random_seed"] != data["random_seed"]
        or model["test_size_percent"] != data["test_size_percent"]
        or model["training_examples"] != len(data["train_data"])
        or model["test_examples"] != len(data["test_data"])
    ):
        raise ValueError("model and data correspondence mismatch")
    pipeline = _restore(model)
    accuracy = float(
        accuracy_score(data["test_labels"], pipeline.predict(data["test_data"]))
    )
    minimum = args.minimum_accuracy_percent / 100
    verdict = {
        "schema": _VERDICT_SCHEMA,
        "data_sha256": data["data_sha256"],
        "model_packet_sha256": model["model_packet_sha256"],
        "accuracy": accuracy,
        "minimum_accuracy": minimum,
        "accepted": accuracy >= minimum,
        "scikit_learn_version": sklearn.__version__,
    }
    _write_json(destination, verdict)
    return 0 if verdict["accepted"] else 1


def _publish(args: argparse.Namespace) -> int:
    targets = _destinations(
        "ml-output",
        "iris-training-metrics.json",
        "iris-model-parameters.json",
        "iris-model.joblib",
    )
    model = _model(args.model)
    verdict = _packet(args.verdict, _VERDICT_SCHEMA, _VERDICT_FIELDS)
    accuracy = _number(verdict["accuracy"])
    minimum = _number(verdict["minimum_accuracy"])
    if (
        type(verdict["accepted"]) is not bool
        or verdict["accepted"] is not True
        or not 0 <= accuracy <= 1
        or not 0.8 <= minimum <= 1
        or round(minimum * 100) / 100 != minimum
        or accuracy < minimum
        or _hash(verdict["model_packet_sha256"]) != model["model_packet_sha256"]
        or _hash(verdict["data_sha256"]) != model["data_sha256"]
    ):
        raise ValueError("evaluation not accepted or does not match model")
    pipeline = _restore(model)
    metrics = {
        "accuracy": accuracy,
        "minimum_accuracy": minimum,
        "random_seed": model["random_seed"],
        "test_size_percent": model["test_size_percent"],
        "max_iterations": model["max_iterations"],
        "training_examples": model["training_examples"],
        "test_examples": model["test_examples"],
        "scikit_learn_version": sklearn.__version__,
        "data_sha256": model["data_sha256"],
        "model_packet_sha256": model["model_packet_sha256"],
    }
    # Validate every small output and target before the first filesystem effect.
    _json_bytes(metrics)
    _json_bytes(model["parameters"])
    _write_json(targets[0], metrics)
    _write_json(targets[1], model["parameters"])
    with targets[2].open("xb") as output:
        joblib.dump(pipeline, output)
    return 0


def _bounded_integer(lower: int, upper: int):
    def parse(text: str) -> int:
        return _integer(int(text), lower, upper)

    return parse


def main() -> int:
    parser = _Parser(prog="iris-pipeline")
    commands = parser.add_subparsers(dest="stage", required=True, parser_class=_Parser)
    ingest = commands.add_parser(
        "ingest", help="validate and split bundled Iris data once"
    )
    ingest.add_argument(
        "--random-seed", required=True, type=_bounded_integer(0, 2147483647)
    )
    ingest.add_argument(
        "--test-size-percent", required=True, type=_bounded_integer(10, 40)
    )
    train = commands.add_parser(
        "train", help="fit the native pipeline from the DATA packet"
    )
    train.add_argument("--data", required=True)
    train.add_argument(
        "--max-iterations", required=True, type=_bounded_integer(100, 2000)
    )
    evaluate = commands.add_parser(
        "evaluate", help="score the exact model against held-out rows"
    )
    evaluate.add_argument("--data", required=True)
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument(
        "--minimum-accuracy-percent", required=True, type=_bounded_integer(80, 100)
    )
    publish = commands.add_parser(
        "publish", help="publish only a matching accepted model"
    )
    publish.add_argument("--model", required=True)
    publish.add_argument("--verdict", required=True)
    args = parser.parse_args()
    try:
        return {
            "ingest": _ingest,
            "train": _train,
            "evaluate": _evaluate,
            "publish": _publish,
        }[args.stage](args)
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, OSError):
        print("iris-pipeline: invalid pipeline input or output state", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
