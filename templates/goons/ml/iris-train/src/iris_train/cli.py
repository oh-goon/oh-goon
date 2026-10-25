from __future__ import annotations

import argparse
import json
from pathlib import Path

import sklearn
from sklearn.datasets import load_iris
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def main() -> int:
    """Train native scikit-learn's Iris pipeline and retain its actual parameters."""

    parser = argparse.ArgumentParser(prog="iris-train")
    parser.add_argument("--random-seed", type=int, required=True)
    parser.add_argument("--test-size-percent", type=int, required=True)
    parser.add_argument("--minimum-accuracy-percent", type=int, required=True)
    parser.add_argument("--max-iterations", type=int, required=True)
    args = parser.parse_args()
    for name, lower, upper in (
        ("random_seed", 0, 2147483647),
        ("test_size_percent", 10, 40),
        ("minimum_accuracy_percent", 80, 100),
        ("max_iterations", 100, 2000),
    ):
        if not lower <= getattr(args, name) <= upper:
            parser.error(
                f"--{name.replace('_', '-')} must be between {lower} and {upper}"
            )
    data, labels = load_iris(return_X_y=True)
    train_data, test_data, train_labels, test_labels = train_test_split(
        data,
        labels,
        test_size=args.test_size_percent / 100,
        stratify=labels,
        random_state=args.random_seed,
    )
    scaler = StandardScaler()
    classifier = LogisticRegression(
        max_iter=args.max_iterations, random_state=args.random_seed
    )
    pipeline = make_pipeline(scaler, classifier)
    pipeline.fit(train_data, train_labels)
    accuracy = pipeline.score(test_data, test_labels)
    output = Path("ml-output")
    output.mkdir(exist_ok=True)
    metrics = {
        "accuracy": accuracy,
        "minimum_accuracy": args.minimum_accuracy_percent / 100,
        "random_seed": args.random_seed,
        "training_examples": len(train_data),
        "test_examples": len(test_data),
        "scikit_learn_version": sklearn.__version__,
    }
    parameters = {
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "classes": classifier.classes_.tolist(),
        "coefficients": classifier.coef_.tolist(),
        "intercepts": classifier.intercept_.tolist(),
        "iterations": classifier.n_iter_.tolist(),
        "scikit_learn_version": sklearn.__version__,
    }
    (output / "iris-training-metrics.json").write_text(
        json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
    )
    (output / "iris-model-parameters.json").write_text(
        json.dumps(parameters, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if accuracy >= metrics["minimum_accuracy"] else 1
