"""Captured private data through the installed CLI and original training Run."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryFile
from typing import Any
from uuid import uuid4

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = Path("src/iris_train/data/training.json")
ARTIFACTS = (
    "ml-output/iris-training-metrics.json",
    "ml-output/iris-model-parameters.json",
)
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None,
    reason="native POSIX Git and uv required",
)


def _native(
    arguments: list[str],
    root: Path,
    environment: dict[str, str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    with TemporaryFile() as stdout, TemporaryFile() as stderr:
        result = subprocess.run(
            arguments,
            cwd=root,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            timeout=timeout,
            check=False,
        )
        assert stdout.tell() <= 512 * 1024 and stderr.tell() <= 128 * 1024
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read().decode()
        error = stderr.read().decode()
    return subprocess.CompletedProcess(arguments, result.returncode, output, error)


@dataclass
class Training:
    root: Path
    application: Path
    selection: Path
    developer: Path
    environment: dict[str, str]
    canary: str
    data: dict[str, Any]
    deadline: float

    def public(self, payload: bytes | str) -> None:
        raw = payload.encode() if isinstance(payload, str) else payload
        if self.canary.encode() in raw:
            pytest.fail("private dataset marker escaped into public evidence")

    def native(
        self,
        arguments: list[str],
        *,
        expected: int = 0,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        remaining = self.deadline - time.monotonic()
        assert remaining > 0, "captured-data witness exhausted its native budget"
        result = _native(
            arguments,
            self.root,
            self.environment if environment is None else environment,
            timeout=min(45, remaining),
        )
        self.public(result.stdout)
        self.public(result.stderr)
        assert result.returncode == expected, (
            arguments[:2],
            result.stdout[-4096:],
            result.stderr[-4096:],
        )
        return result

    def cli(self, *arguments: str, expected: int = 0) -> dict[str, Any]:
        result = self.native(
            [str(Path(sys.executable).with_name("oh-goon")), *arguments, "--json"],
            expected=expected,
        )
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    def dataset(self, copies: int = 1) -> bytes:
        count = len(self.data["target"]) * copies
        value = {
            "data": self.data["data"] * copies,
            "target": self.data["target"] * copies,
            "record_ids": [f"{self.canary}-{index}" for index in range(count)],
        }
        payload = json.dumps(value, separators=(",", ":")).encode()
        assert len(payload) <= 64 * 1024
        destination = self.application / RESOURCE
        destination.parent.mkdir(exist_ok=True)
        destination.write_bytes(payload)
        return payload


@pytest.fixture
def training(tmp_path: Path) -> Training:
    application = tmp_path / "application"
    shutil.copytree(
        ROOT / "templates/goons/ml/iris-train",
        application,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OH_GOON_") and key != "UV_PROJECT_ENVIRONMENT"
    }
    environment.update(
        OH_GOON_DB=str(tmp_path / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(tmp_path / "league.yml"),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        HASHMARKS_NO_UPDATE_CHECK="1",
    )
    selected = tmp_path / "workflow/run.yml"
    fixture = Training(
        tmp_path,
        application,
        selected,
        tmp_path / "developer-environment",
        environment,
        "private-dataset-" + uuid4().hex,
        {},
        time.monotonic() + 170,
    )
    fixture.native(["git", "init", "-q", str(application)])
    (application / ".gitignore").write_text(RESOURCE.as_posix() + "\n")
    fixture.native(
        [
            "uv",
            "sync",
            "--project",
            str(application),
            "--frozen",
            "--no-default-groups",
            "--group",
            "dev",
        ],
        environment={**environment, "UV_PROJECT_ENVIRONMENT": str(fixture.developer)},
    )
    original = fixture.native(
        [
            str(fixture.developer / "bin/python"),
            "-c",
            "import json; from sklearn.datasets import load_iris; "
            "x,y=load_iris(return_X_y=True); "
            "print(json.dumps({'data':x.tolist(),'target':y.tolist()}))",
        ]
    )
    fixture.data = json.loads(original.stdout)
    fixture.dataset()
    fixture.native(
        ["git", "-C", str(application), "check-ignore", "--quiet", "--", str(RESOURCE)]
    )
    fixture.cli(
        "init",
        "--template",
        "ml/iris-training",
        "--kind",
        "plan",
        "--output",
        str(selected.parent),
        "--repository",
        str(application),
        "--no-input",
    )
    package = selected.parent / "goons/ml/data-training"
    package.mkdir(parents=True)
    declaration = yaml.safe_load(
        (ROOT / "templates/goons/ml/iris-train/goon.yml").read_text()
    )
    declaration["application"] = {"path": str(application)}
    declaration["steps"][0]["id"] = "train"
    (package / "goon.yml").write_text(yaml.safe_dump(declaration, sort_keys=False))
    (package / "README.md").write_text("# ml/data-training\n\nCaptured Iris data.\n")
    (selected.parent / "plan.yml").write_text(
        yaml.safe_dump(
            {
                "execution": {"purpose": "verification"},
                "goons": [{"import": "ml/data-training", "from": "./goons"}],
            }
        )
    )
    configuration = yaml.safe_load(selected.read_text())
    configuration["source"] = {"kind": "file-plan", "path": "./plan.yml"}
    configuration["inputs"] = {"dataset": "captured"}
    selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
    fixture.cli("workflow", "prepare", str(selected), "--no-input")
    fixture.public((selected.parent / "goon.lock").read_bytes())
    return fixture


def _step(tape: dict[str, Any]) -> dict[str, Any]:
    steps = tape["repositories"][0]["steps"]
    assert len(steps) == 1
    return steps[0]


def _exports(
    training: Training, run_id: str, step_id: str, directory: Path
) -> dict[str, bytes]:
    directory.mkdir()
    output = training.cli("runs", "outputs", run_id)
    recorded = {
        item["path"]: item
        for item in output["repositories"][0]["steps"][0]["artifacts"]
    }
    assert set(recorded) == set(ARTIFACTS)
    payloads = {}
    for path in ARTIFACTS:
        artifact = recorded[path]
        assert artifact["status"] == "PRESENT" and artifact["retained_object"]
        destination = directory / Path(path).name
        receipt = training.cli(
            "runs",
            "export-artifact",
            run_id,
            "--step",
            step_id,
            "--path",
            path,
            "--output",
            str(destination),
        )
        assert receipt["cleanup_status"] == "verified"
        payload = destination.read_bytes()
        training.public(payload)
        assert len(payload) == artifact["byte_count"]
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
        payloads[path] = payload
    return payloads


def test_captured_training_data_refresh_preserves_private_run_lineage(
    training: Training,
) -> None:
    application, selected = training.application, training.selection
    native_lock = (application / "uv.lock").read_bytes()
    developer_config = (training.developer / "pyvenv.cfg").read_bytes()
    history: list[tuple[str, dict[str, Any], dict[str, bytes]]] = []
    locks = []
    for copies in (1, 2):
        payload = training.dataset(copies)
        if copies == 2:
            previous_lock = (selected.parent / "goon.lock").read_bytes()
            before = training.cli("runs", "list")["items"]
            rejected = training.cli("run", str(selected), "--no-input", expected=2)
            assert rejected.get("fleet_run_id") is None
            assert training.cli("runs", "list")["items"] == before
            assert (selected.parent / "goon.lock").read_bytes() == previous_lock
            training.cli("workflow", "prepare", str(selected), "--no-input")
        lock = (selected.parent / "goon.lock").read_bytes()
        training.public(lock)
        locks.append(lock)
        training.cli("workflow", "inspect", str(selected))
        receipt = training.cli("run", str(selected), "--no-input")
        assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
        run_id = receipt["fleet_run_id"]
        tape = training.cli("runs", "tape", run_id)
        training.cli("runs", "show", run_id)
        training.cli("runs", "explain", run_id)
        step = _step(tape)
        assert tape["status"] == step["status"] == "PASSED"
        assert step["exit_code"] == 0 and step["settlement"]["cleanup"] == "VERIFIED"
        assert step["uv"]["source"] == "captured"
        assert step["uv"]["lock_mode"] == "frozen" and step["uv"]["group"] is None
        files = _exports(
            training, run_id, step["step_id"], training.root / f"export-{copies}"
        )
        metrics = json.loads(files[ARTIFACTS[0]])
        parameters = json.loads(files[ARTIFACTS[1]])
        assert metrics["dataset_sha256"] == hashlib.sha256(payload).hexdigest()
        assert metrics["training_examples"] == 120 * copies
        assert metrics["test_examples"] == 30 * copies
        assert metrics["accuracy"] >= metrics["minimum_accuracy"] == 0.9
        assert parameters["coefficients"] and parameters["classes"] == [0, 1, 2]
        history.append((run_id, tape, files))
    assert locks[0] != locks[1]
    assert history[0][0] != history[1][0]
    assert (
        _step(history[0][1])["step_execution_id"]
        != _step(history[1][1])["step_execution_id"]
    )
    assert (
        _step(history[0][1])["project_source"]["content_sha256"]
        != _step(history[1][1])["project_source"]["content_sha256"]
    )
    assert history[0][2][ARTIFACTS[1]] != history[1][2][ARTIFACTS[1]]
    assert (application / "uv.lock").read_bytes() == native_lock
    assert (training.developer / "pyvenv.cfg").read_bytes() == developer_config
    assert not (application / "ml-output").exists()
    shutil.rmtree(selected.parent)
    shutil.rmtree(application)
    evidence = []
    for index, (run_id, original, files) in enumerate(history):
        reopened = training.cli("runs", "tape", run_id)
        for key in ("status", "configuration", "execution_reference", "repositories"):
            assert reopened[key] == original[key]
        assert (
            _exports(
                training,
                run_id,
                _step(original)["step_id"],
                training.root / f"reopened-{index}",
            )
            == files
        )
        metrics = json.loads(files[ARTIFACTS[0]])
        evidence.append(
            {
                "fleet_run_id": run_id,
                "step_execution_id": _step(original)["step_execution_id"],
                "project_content_sha256": _step(original)["project_source"][
                    "content_sha256"
                ],
                "dataset_sha256": metrics["dataset_sha256"],
                "training_examples": metrics["training_examples"],
                "test_examples": metrics["test_examples"],
            }
        )
    print("TRAINING_DATA_EVIDENCE " + json.dumps(evidence, sort_keys=True))


def test_training_data_source_choices_refuse_before_admission(
    training: Training,
) -> None:
    selected = training.selection
    configuration = yaml.safe_load(selected.read_text())
    lock = (selected.parent / "goon.lock").read_bytes()
    for unsupported in (
        "/arbitrary/host/training.json",
        "https://example.invalid/training.json",
        "ref-from-another-repository",
    ):
        configuration["inputs"]["dataset"] = unsupported
        selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
        refused = training.cli(
            "workflow", "prepare", str(selected), "--no-input", expected=2
        )
        assert refused.get("fleet_run_id") is None
        assert training.cli("runs", "list")["items"] == []
        assert (selected.parent / "goon.lock").read_bytes() == lock
        assert not (training.application / "ml-output").exists()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "malformed",
        pytest.param("encoding", marks=pytest.mark.daily),
        pytest.param("oversized", marks=pytest.mark.daily),
    ],
)
def test_invalid_captured_training_data_cannot_publish_a_model(
    training: Training, fault: str
) -> None:
    dataset = training.application / RESOURCE
    if fault == "missing":
        dataset.unlink()
    elif fault == "malformed":
        dataset.write_text('{"data": "' + training.canary + '", "target": []}')
    elif fault == "encoding":
        dataset.write_bytes(training.canary.encode() + b"\xff")
    else:
        payload = dataset.read_bytes()
        dataset.write_bytes(payload + b" " * (64 * 1024 + 1 - len(payload)))
    training.cli("workflow", "prepare", str(training.selection), "--no-input")
    receipt = training.cli("run", str(training.selection), "--no-input", expected=1)
    assert (
        receipt["status"] == "COMPLETED" and receipt["outcome"] == "ATTENTION_REQUIRED"
    )
    run_id = receipt["fleet_run_id"]
    tape = training.cli("runs", "tape", run_id)
    training.cli("runs", "show", run_id)
    training.cli("runs", "explain", run_id)
    step = _step(tape)
    assert step["status"] == "FAILED" and step["exit_code"] == 2
    assert step["settlement"]["cleanup"] == "VERIFIED"
    output = training.cli("runs", "outputs", run_id)
    assert all(
        artifact["status"] != "PRESENT" and artifact.get("retained_object") is None
        for item in output["repositories"][0]["steps"]
        for artifact in item["artifacts"]
    )
    destination = training.root / "forbidden-model.json"
    training.cli(
        "runs",
        "export-artifact",
        run_id,
        "--step",
        step["step_id"],
        "--path",
        ARTIFACTS[1],
        "--output",
        str(destination),
        expected=2,
    )
    assert not destination.exists()
    assert not (training.application / "ml-output").exists()
