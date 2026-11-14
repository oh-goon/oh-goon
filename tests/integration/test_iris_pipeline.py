"""Real Iris application commands through admitted Steps and retained export."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = (
    "ml-output/iris-training-metrics.json",
    "ml-output/iris-model-parameters.json",
    "ml-output/iris-model.joblib",
)
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None,
    reason="native POSIX Git and uv required",
)
Cli = Callable[..., dict[str, Any]]


def _native(
    argv: list[str], cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _initialize(
    root: Path, application: Path, authored: Path
) -> tuple[Cli, Path, Path, dict[str, str]]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OH_GOON_") and key != "UV_PROJECT_ENVIRONMENT"
    }
    environment.update(
        OH_GOON_DB=str(root / "runtime.sqlite"),
        OH_GOON_LEAGUE_FILE=str(root / "league.yml"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_DATA_HOME=str(root / "data"),
        XDG_STATE_HOME=str(root / "state"),
    )
    for argv in (
        ["git", "init", "-q", str(application)],
        ["git", "-C", str(application), "add", "."],
        [
            "git",
            "-C",
            str(application),
            "-c",
            "user.name=Iris example",
            "-c",
            "user.email=iris@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "complete native Iris application",
        ],
    ):
        result = _native(argv, root, environment)
        assert result.returncode == 0, result.stderr[-8192:]

    developer = root / "developer-environment"
    prepared = _native(
        [
            "uv",
            "sync",
            "--project",
            str(application),
            "--frozen",
            "--no-default-groups",
        ],
        root,
        {**environment, "UV_PROJECT_ENVIRONMENT": str(developer)},
    )
    assert prepared.returncode == 0, prepared.stderr[-8192:]

    def cli(*args: str, expected: int = 0) -> dict[str, Any]:
        result = _native(
            [str(Path(sys.executable).with_name("oh-goon")), *args, "--json"],
            root,
            environment,
        )
        assert result.returncode == expected, (
            args,
            result.stdout[-8192:],
            result.stderr[-8192:],
        )
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value

    workflow = root / "workflow"
    cli(
        "init",
        "--template",
        "python/quality",
        "--output",
        str(workflow),
        "--repository",
        str(application),
        "--no-input",
    )
    selected = workflow / "run.yml"
    configuration = yaml.safe_load(selected.read_text())
    target = configuration["target"]
    assert (workflow / target["repository"]).resolve() == application
    shutil.copy2(authored / "plan.yml", workflow / "plan.yml")
    shutil.copytree(authored / "goons", workflow / "goons")
    goon = workflow / "goons/ml/iris-pipeline/goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    declaration["application"]["path"] = str(application)
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    configuration["source"] = {"kind": "file-plan", "path": "./plan.yml"}
    configuration.pop("inputs", None)
    selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
    cli("workflow", "prepare", str(selected), "--no-input")
    assert yaml.safe_load(selected.read_text())["target"] == target
    return cli, selected, developer, environment


def _steps(tape: dict[str, Any]) -> list[dict[str, Any]]:
    return list(tape["repositories"][0]["steps"])


def _export(cli: Cli, run_id: str, step_id: str, root: Path) -> dict[str, Path]:
    root.mkdir()
    output = cli("runs", "outputs", run_id)
    recorded = {
        artifact["path"]: artifact
        for artifact in output["repositories"][0]["steps"][-1]["artifacts"]
    }
    assert set(recorded) == set(ARTIFACTS)
    exported: dict[str, Path] = {}
    for path in ARTIFACTS:
        artifact = recorded[path]
        assert artifact["status"] == "PRESENT"
        assert artifact["retained_object"] is not None
        destination = root / Path(path).name
        receipt = cli(
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
        assert len(payload) == artifact["byte_count"]
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
        exported[path] = destination
    return exported


@pytest.mark.parametrize(
    "extended",
    [
        pytest.param(False, id="local-single"),
        pytest.param(True, id="git-seed-history", marks=pytest.mark.daily),
    ],
)
def test_iris_pipeline_retains_real_models_across_seed_changes(
    tmp_path: Path, extended: bool
) -> None:
    application, authored = tmp_path / "application", tmp_path / "authored"
    shutil.copytree(ROOT / "templates/goons/ml/iris-train", application)
    shutil.copytree(ROOT / "examples/iris-pipeline", authored)
    cli, selected, developer, environment = _initialize(tmp_path, application, authored)
    inspected = cli("workflow", "inspect", str(selected))
    assert inspected["status"] == "inputs-valid"
    assert (
        cli("workflow", "validate", str(selected))["proof"]["provider"]
        == "not-applicable"
    )
    developer_configuration = (developer / "pyvenv.cfg").read_bytes()
    history: list[tuple[str, dict[str, Any], dict[str, Path]]] = []
    metrics: list[dict[str, Any]] = []
    parameters: list[dict[str, Any]] = []
    revision = _native(
        ["git", "-C", str(application), "rev-parse", "HEAD"], tmp_path, environment
    ).stdout.strip()
    for seed in (42, 7) if extended else (42,):
        if seed == 7:
            goon = selected.parent / "goons/ml/iris-pipeline/goon.yml"
            declaration = yaml.safe_load(goon.read_text())
            declaration["application"] = {
                "git": application.as_uri(),
                "ref": revision,
                "path": ".",
            }
            goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
        configuration = yaml.safe_load(selected.read_text())
        configuration["inputs"] = {"random_seed": seed}
        selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
        cli("workflow", "prepare", str(selected), "--no-input")
        receipt = cli("run", str(selected), "--no-input")
        assert receipt["status"] == "COMPLETED" and receipt["outcome"] == "SUCCESS"
        run_id = receipt["fleet_run_id"]
        tape = cli("runs", "tape", run_id)
        steps = _steps(tape)
        assert [s["step_id"].rsplit(".", 1)[-1] for s in steps] == [
            "ingest",
            "train",
            "evaluate",
            "publish",
        ]
        assert [s["status"] for s in steps] == ["PASSED"] * 4
        assert len({s["step_id"] for s in steps}) == 4
        assert len({s["step_execution_id"] for s in steps}) == 4
        assert [s["output_names"] for s in steps] == [
            ["DATA"],
            ["MODEL"],
            ["VERDICT"],
            [],
        ]
        assert len({s["outputs"][0]["sha256"] for s in steps[:3]}) == 3
        assert all(0 < s["outputs"][0]["byte_count"] <= 16_384 for s in steps[:3])
        assert all(
            s["outputs"][0].get("producer_execution_identity") is None
            for s in steps[:3]
        )
        assert len({s["project_source"]["content_sha256"] for s in steps}) == 1
        assert all(s["settlement"]["cleanup"] == "VERIFIED" for s in steps)
        assert all(s["uv"]["lock_mode"] == "frozen" for s in steps)
        assert all(
            s["uv"]["source"] == "captured" and s["uv"]["group"] is None for s in steps
        )
        assert not any(s["artifacts"] for s in steps[:3])
        files = _export(cli, run_id, steps[-1]["step_id"], tmp_path / f"export-{seed}")
        measured = json.loads(files[ARTIFACTS[0]].read_text())
        fitted = json.loads(files[ARTIFACTS[1]].read_text())
        assert measured["random_seed"] == seed
        assert measured["training_examples"] == 120 and measured["test_examples"] == 30
        assert measured["accuracy"] >= measured["minimum_accuracy"] == 0.9
        checked = _native(
            [
                str(developer / "bin/python"),
                "-c",
                "import json,joblib,sys; from sklearn.datasets import load_iris; "
                "from sklearn.model_selection import train_test_split; "
                "from sklearn.metrics import accuracy_score; "
                "m=json.load(open(sys.argv[2])); x,y=load_iris(return_X_y=True); "
                "_,xt,_,yt=train_test_split(x,y,test_size=m['test_size_percent']/100,"
                "stratify=y,random_state=m['random_seed']); p=joblib.load(sys.argv[1]); "
                "assert float(accuracy_score(yt,p.predict(xt)))==m['accuracy']; "
                "assert p.steps[-1][1].coef_.tolist()==json.load(open(sys.argv[3]))['coefficients']",
                str(files[ARTIFACTS[2]]),
                str(files[ARTIFACTS[0]]),
                str(files[ARTIFACTS[1]]),
            ],
            tmp_path,
            environment,
        )
        assert checked.returncode == 0, checked.stderr
        metrics.append(measured)
        parameters.append(fitted)
        history.append((run_id, tape, files))
    if extended:
        assert history[0][0] != history[1][0]
        assert metrics[0]["data_sha256"] != metrics[1]["data_sha256"]
        assert metrics[0]["model_packet_sha256"] != metrics[1]["model_packet_sha256"]
        assert parameters[0]["coefficients"] != parameters[1]["coefficients"]
        assert (
            _steps(history[0][1])[0]["outputs"][0]["sha256"]
            != _steps(history[1][1])[0]["outputs"][0]["sha256"]
        )

    # A later failure in this same workflow must not reuse any earlier model.
    configuration = yaml.safe_load(selected.read_text())
    configuration["inputs"] = {"random_seed": 42, "minimum_accuracy_percent": 100}
    selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
    cli("workflow", "prepare", str(selected), "--no-input")
    failed = cli("run", str(selected), "--no-input", expected=1)
    assert failed["status"] == "COMPLETED" and failed["outcome"] == "ATTENTION_REQUIRED"
    failed_id = failed["fleet_run_id"]
    assert failed_id not in {run_id for run_id, _, _ in history}
    failed_tape = cli("runs", "tape", failed_id)
    failed_steps = _steps(failed_tape)
    assert [s["status"] for s in failed_steps] == [
        "PASSED",
        "PASSED",
        "FAILED",
        "SKIPPED",
    ]
    assert failed_steps[2]["exit_code"] == 1
    assert {s["step_execution_id"] for s in failed_steps}.isdisjoint(
        s["step_execution_id"] for _, original, _ in history for s in _steps(original)
    )
    failed_outputs = cli("runs", "outputs", failed_id)
    assert all(
        artifact["status"] != "PRESENT" and artifact.get("retained_object") is None
        for step in failed_outputs["repositories"][0]["steps"]
        for artifact in step["artifacts"]
    )
    assert (developer / "pyvenv.cfg").read_bytes() == developer_configuration
    assert not (application / "ml-output").exists()
    assert not (application / "ml-pipeline").exists()
    assert (
        _native(
            ["git", "-C", str(application), "status", "--porcelain"],
            tmp_path,
            environment,
        ).stdout
        == ""
    )
    shutil.rmtree(selected.parent)
    shutil.rmtree(application)
    for index, (run_id, original, original_files) in enumerate(history):
        reopened = cli("runs", "tape", run_id)
        for key in ("status", "execution_reference", "configuration", "repositories"):
            assert reopened[key] == original[key]
        replay = _export(
            cli, run_id, _steps(original)[-1]["step_id"], tmp_path / f"reopened-{index}"
        )
        assert json.loads(replay[ARTIFACTS[0]].read_text()) == metrics[index]
        assert json.loads(replay[ARTIFACTS[1]].read_text()) == parameters[index]
        assert all(
            replay[path].read_bytes() == original_files[path].read_bytes()
            for path in ARTIFACTS
        )
    assert cli("runs", "tape", failed_id)["repositories"] == failed_tape["repositories"]
    forbidden = tmp_path / "failed-publish.joblib"
    cli(
        "runs",
        "export-artifact",
        failed_id,
        "--step",
        failed_steps[-1]["step_id"],
        "--path",
        ARTIFACTS[2],
        "--output",
        str(forbidden),
        expected=2,
    )
    assert not forbidden.exists()


# Evaluation is the bounded ordinary Run-level stopping proof. Direct native
# refusals below cover the other application guards; their full Run variants
# retain separate daily lifecycle evidence.
@pytest.mark.parametrize(
    "stage",
    [
        pytest.param("ingest", marks=pytest.mark.daily),
        pytest.param("train", marks=pytest.mark.daily),
        "evaluate",
        pytest.param("publish", marks=pytest.mark.daily),
    ],
)
def test_iris_pipeline_stage_failure_cannot_publish_a_model(
    tmp_path: Path, stage: str
) -> None:
    application, authored = tmp_path / "application", tmp_path / "authored"
    shutil.copytree(ROOT / "templates/goons/ml/iris-train", application)
    shutil.copytree(ROOT / "examples/iris-pipeline", authored)
    cli, selected, developer, environment = _initialize(tmp_path, application, authored)
    goon = selected.parent / "goons/ml/iris-pipeline/goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    if stage == "ingest":
        declaration["steps"][0]["run"] = (
            "iris-pipeline ingest --random-seed -1 --test-size-percent 20"
        )
    elif stage == "train":
        declaration["steps"][1]["run"] = (
            "iris-pipeline train --data ${{ steps.ingest.outputs.DATA }} --max-iterations 1"
        )
    elif stage == "evaluate":
        configuration = yaml.safe_load(selected.read_text())
        configuration["inputs"] = {"minimum_accuracy_percent": 100}
        selected.write_text(yaml.safe_dump(configuration, sort_keys=False))
    else:
        # This is a real rejected verdict from the same native model, not a
        # public simulation flag. The workflow still evaluates normally first.
        direct = tmp_path / "direct-rejection"
        direct.mkdir()
        executable = str(developer / "bin/iris-pipeline")
        ingested = _native(
            [executable, "ingest", "--random-seed", "42", "--test-size-percent", "20"],
            direct,
            environment,
        )
        assert ingested.returncode == 0, ingested.stderr
        data = (direct / "ml-pipeline/data.json").read_text()
        trained = _native(
            [executable, "train", "--data", data, "--max-iterations", "300"],
            direct,
            environment,
        )
        assert trained.returncode == 0, trained.stderr
        model = (direct / "ml-pipeline/model.json").read_text()
        evaluated = _native(
            [
                executable,
                "evaluate",
                "--data",
                data,
                "--model",
                model,
                "--minimum-accuracy-percent",
                "100",
            ],
            direct,
            environment,
        )
        assert evaluated.returncode == 1, evaluated.stderr
        rejected = json.loads((direct / "ml-pipeline/evaluation.json").read_text())
        assert rejected["accepted"] is False
        declaration["steps"][3]["run"] = (
            "iris-pipeline publish --model ${{ steps.train.outputs.MODEL }} --verdict "
            + shlex.quote(json.dumps(rejected))
        )
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    cli("workflow", "prepare", str(selected), "--no-input")
    receipt = cli("run", str(selected), "--no-input", expected=1)
    assert receipt["status"] == "COMPLETED" and receipt["outcome"] != "SUCCESS"
    run_id = receipt["fleet_run_id"]
    tape = cli("runs", "tape", run_id)
    failed = ["ingest", "train", "evaluate", "publish"].index(stage)
    steps = _steps(tape)
    assert [s["status"] for s in steps] == ["PASSED"] * failed + ["FAILED"] + [
        "SKIPPED"
    ] * (3 - failed)
    assert steps[failed]["exit_code"] != 0
    assert all(a["status"] != "PRESENT" for s in steps for a in s["artifacts"])
    outputs = cli("runs", "outputs", run_id)
    assert all(
        a["status"] != "PRESENT"
        for s in outputs["repositories"][0]["steps"]
        for a in s["artifacts"]
    )
    destination = tmp_path / "forbidden.joblib"
    cli(
        "runs",
        "export-artifact",
        run_id,
        "--step",
        steps[-1]["step_id"],
        "--path",
        ARTIFACTS[2],
        "--output",
        str(destination),
        expected=2,
    )
    assert not destination.exists()


def test_iris_pipeline_rejects_resealed_contradictory_split_metadata(
    tmp_path: Path,
) -> None:
    application = tmp_path / "application"
    shutil.copytree(ROOT / "templates/goons/ml/iris-train", application)
    developer = tmp_path / "native"
    environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(developer)}
    result = _native(
        [
            "uv",
            "sync",
            "--project",
            str(application),
            "--frozen",
            "--no-default-groups",
        ],
        tmp_path,
        environment,
    )
    assert result.returncode == 0, result.stderr[-8192:]
    executable = str(developer / "bin/iris-pipeline")
    direct = tmp_path / "direct"
    direct.mkdir()
    invalid = _native(
        [executable, "ingest", "--random-seed", "-1", "--test-size-percent", "20"],
        direct,
        environment,
    )
    assert invalid.returncode == 2
    assert not (direct / "ml-pipeline").exists()
    assert (
        _native(
            [executable, "ingest", "--random-seed", "42", "--test-size-percent", "20"],
            direct,
            environment,
        ).returncode
        == 0
    )
    data = json.loads((direct / "ml-pipeline/data.json").read_text())
    original = dict(data)
    invalid = _native(
        [executable, "train", "--data", json.dumps(original), "--max-iterations", "1"],
        direct,
        environment,
    )
    assert invalid.returncode == 2
    assert not (direct / "ml-pipeline/model.json").exists()
    data["test_size_percent"] = 40
    data["data_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in data.items() if k != "data_sha256"},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    trained = _native(
        [executable, "train", "--data", json.dumps(data), "--max-iterations", "300"],
        direct,
        environment,
    )
    assert trained.returncode == 2, trained.stderr
    assert not (direct / "ml-pipeline/model.json").exists()
    assert not (direct / "ml-output").exists()
    valid = tmp_path / "valid-model"
    valid.mkdir()
    trained = _native(
        [
            executable,
            "train",
            "--data",
            json.dumps(original),
            "--max-iterations",
            "300",
        ],
        valid,
        environment,
    )
    assert trained.returncode == 0, trained.stderr
    model = json.loads((valid / "ml-pipeline/model.json").read_text())
    evaluated = _native(
        [
            executable,
            "evaluate",
            "--data",
            json.dumps(original),
            "--model",
            json.dumps(model),
            "--minimum-accuracy-percent",
            "90",
        ],
        valid,
        environment,
    )
    assert evaluated.returncode == 0, evaluated.stderr
    verdict = json.loads((valid / "ml-pipeline/evaluation.json").read_text())
    rejected = {**verdict, "minimum_accuracy": 1, "accepted": False}
    refused = _native(
        [
            executable,
            "publish",
            "--model",
            json.dumps(model),
            "--verdict",
            json.dumps(rejected),
        ],
        valid,
        environment,
    )
    assert refused.returncode == 2, refused.stderr
    assert not (valid / "ml-output").exists()
    model["test_size_percent"] = 40
    model["model_packet_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in model.items() if k != "model_packet_sha256"},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    verdict["model_packet_sha256"] = model["model_packet_sha256"]
    refused = _native(
        [
            executable,
            "publish",
            "--model",
            json.dumps(model),
            "--verdict",
            json.dumps(verdict),
        ],
        valid,
        environment,
    )
    assert refused.returncode == 2, refused.stderr
    assert not (valid / "ml-output").exists()


# The ordinary binding refusal and actual bounded file-I/O cases in
# backend/tests/unit/integrations/test_fleet_plan_output_contract_edges.py keep
# fast protection; these complete ML Run permutations remain in daily proof.
@pytest.mark.parametrize(
    "fault",
    [
        "binding",
        "duplicate-step",
        pytest.param("wrong-recipient", marks=pytest.mark.daily),
        pytest.param("missing-output", marks=pytest.mark.daily),
        pytest.param("output-budget", marks=pytest.mark.daily),
    ],
)
def test_iris_pipeline_invalid_dataflow_has_no_publish_authority(
    tmp_path: Path, fault: str
) -> None:
    application, authored = tmp_path / "application", tmp_path / "authored"
    shutil.copytree(ROOT / "templates/goons/ml/iris-train", application)
    shutil.copytree(ROOT / "examples/iris-pipeline", authored)
    cli, selected, _, environment = _initialize(tmp_path, application, authored)
    goon = selected.parent / "goons/ml/iris-pipeline/goon.yml"
    declaration = yaml.safe_load(goon.read_text())
    if fault == "binding":
        declaration["steps"][1]["run"] = (
            "iris-pipeline train --data ${{ steps.missing.outputs.DATA }} --max-iterations 300"
        )
    elif fault == "duplicate-step":
        declaration["steps"][1]["id"] = declaration["steps"][0]["id"]
    elif fault == "wrong-recipient":
        declaration["steps"][2]["run"] = declaration["steps"][2]["run"].replace(
            "${{ steps.ingest.outputs.DATA }}", "${{ steps.train.outputs.MODEL }}"
        )
    elif fault == "missing-output":
        declaration["steps"][0]["outputs"]["DATA"]["path"] = "ml-pipeline/missing.json"
    else:
        declaration["steps"][0]["outputs"]["DATA"]["max_bytes"] = 1
    goon.write_text(yaml.safe_dump(declaration, sort_keys=False))
    if fault in {"binding", "duplicate-step"}:
        refused = _native(
            [
                str(Path(sys.executable).with_name("oh-goon")),
                "workflow",
                "prepare",
                str(selected),
                "--no-input",
                "--json",
            ],
            tmp_path,
            environment,
        )
        assert refused.returncode == 2, (refused.stdout, refused.stderr)
        assert cli("runs", "list")["items"] == []
    else:
        cli("workflow", "prepare", str(selected), "--no-input")
        result = cli("run", str(selected), "--no-input", expected=1)
        tape = cli("runs", "tape", result["fleet_run_id"])
        steps = _steps(tape)
        assert result["outcome"] != "SUCCESS"
        if fault == "wrong-recipient":
            assert [step["status"] for step in steps] == [
                "PASSED",
                "PASSED",
                "FAILED",
                "SKIPPED",
            ]
            assert steps[2]["exit_code"] != 0
        assert steps[-1]["status"] == "SKIPPED"
        assert all(a["status"] != "PRESENT" for s in steps for a in s["artifacts"])
    assert not (application / "ml-output").exists()
