"""Real training and native pytest preserve one shared-state admission owner."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
import yaml

from oh_goon.core.path_layouts import ApplicationStatePathLayout
from tests.integration.services.test_cli_concurrency import (
    CliCommandFact,
    NativeRunFact,
    _CliCase,
    _environment,
)

ROOT = Path(__file__).resolve().parents[4]
METRICS = "ml-output/iris-training-metrics.json"
MODEL = "ml-output/iris-model-parameters.json"
pytestmark = [
    pytest.mark.process,
    pytest.mark.no_resume,
    pytest.mark.skipif(
        sys.platform != "linux" or any(shutil.which(tool) is None for tool in ("git", "uv")),
        reason="native Linux PIDfds and git/uv required",
    ),
]


@dataclass(frozen=True)
class RetainedArtifactFact:
    run_id: str
    path: str
    sha256: str
    byte_count: int
    object_id: str
    reference_id: str


@dataclass(frozen=True)
class CiMlContentionFacts:
    application_commit: str
    application_lock_sha256: tuple[str, str]
    runs: tuple[NativeRunFact, ...]
    busy_codes: tuple[str, str]
    artifacts: tuple[RetainedArtifactFact, ...]
    training_seeds: tuple[int, int]
    training_rounds: int
    pytest_tests: int
    active_retained_artifacts: int
    commands: tuple[CliCommandFact, ...]
    scope: str


def _git(root: Path, environment: Mapping[str, str], *arguments: str) -> str:
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        completed = subprocess.run(
            ("git", "-C", str(root), *arguments),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=10,
        )
        output.seek(0)
        errors.seek(0)
        body, diagnostic = output.read(4097), errors.read(4097)
    assert len(body) <= 4096 and len(diagnostic) <= 4096
    assert completed.returncode == 0, (arguments, diagnostic)
    return body.decode().strip()


def _commit(root: Path, environment: Mapping[str, str]) -> str:
    _git(root, environment, "add", ".")
    _git(
        root,
        environment,
        "-c",
        "user.name=Contention fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "unchanged native workload source",
    )
    return _git(root, environment, "rev-parse", "HEAD")


def _author_workflows(
    case: _CliCase, environment: Mapping[str, str]
) -> tuple[dict[str, Path], Path, str, tuple[str, str]]:
    origin = case.root / "application-origin"
    for name, source in (
        ("iris", ROOT / "templates/goons/ml/iris-train"),
        ("orders", ROOT / "templates/goons/general/orders-taskflow"),
    ):
        shutil.copytree(
            source,
            origin / name,
            ignore=shutil.ignore_patterns(
                ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "ml-output", ".showcase"
            ),
        )
    _git(origin, environment, "init", "-q")
    commit = _commit(origin, environment)
    lock_hashes = tuple(
        hashlib.sha256((origin / name / "uv.lock").read_bytes()).hexdigest()
        for name in ("iris", "orders")
    )
    for name in ("iris", "orders"):
        (origin / name / ".venv").mkdir()
        (origin / name / ".venv/developer-sentinel").write_text("developer environment preserved\n")
    target = case.root / "repository"
    configurations = {label: case.prepare(label, target, environment) for label in ("a", "b", "ci")}
    _commit(target, environment)
    for label, configuration in configurations.items():
        authoring = configuration.parent
        if label == "ci":
            document = yaml.safe_load(
                (ROOT / "examples/python-ci/goons/python/checks/goon.yml").read_text()
            )
            # Preserve the native example's pytest operation and selected CI
            # group. Lint and packaging are separate qualification boundaries.
            document["steps"] = [document["steps"][1]]
            document["uv"]["lock_mode"] = "frozen"
            selected_path, identity = "orders", "python/checks"
        else:
            document = yaml.safe_load((origin / "iris/goon.yml").read_text())
            selected_path, identity = "iris", "ml/iris-train"
        document["application"] = {"git": str(origin), "ref": commit, "path": selected_path}
        package = authoring / "goons" / identity
        package.mkdir(parents=True)
        (package / "README.md").write_text(f"# {identity}\n")
        (package / "goon.yml").write_text(yaml.safe_dump(document, sort_keys=False))
        (authoring / "plan.yml").write_text(
            yaml.safe_dump(
                {
                    "goons": [
                        {"import": identity, "from": "goons"},
                        {"import": "general/concurrency", "from": "goons"},
                    ]
                },
                sort_keys=False,
            )
        )
        selection = json.loads(configuration.read_text())
        if label != "ci":
            selection["inputs"] = {"training_rounds": 3, "random_seed": 42 if label == "a" else 7}
        configuration.write_text(json.dumps(selection))
        prepared = case.cli(
            ("workflow", "prepare", str(configuration), "--register-target", "--no-input"),
            environment,
        )
        assert prepared["status"] == "prepared"
    return configurations, origin, commit, (lock_hashes[0], lock_hashes[1])


def _steps(
    case: _CliCase, run_id: str, environment: Mapping[str, str], *, terminal: bool = False
) -> list[dict[str, Any]]:
    tape = case.cli(("runs", "tape", run_id), environment)
    assert tape["fleet_run_id"] == run_id
    steps: list[dict[str, Any]] = tape["repositories"][0]["steps"]
    assert len(steps) == 2
    first = steps[0]
    assert first["status"] == "PASSED"
    if not terminal:
        # The live event projection reports completion before the repository's
        # final OperationReport publishes attempts, settlement and artifacts.
        return steps
    assert first["attempt_count"] == 1
    assert not first["attempt_transitions"]
    assert first["settlement"]["outcome"] == "SUCCESS"
    assert first["settlement"]["cleanup"] == "VERIFIED"
    assert first["uv"]["lock_mode"] == "frozen"
    assert first["attempts"][0]["uv_preparation"] == {
        "lock_mode": "frozen",
        "freshness": "not_checked",
    }
    return steps


def _exports(
    case: _CliCase, run_id: str, label: str, environment: Mapping[str, str]
) -> tuple[tuple[RetainedArtifactFact, ...], dict[str, bytes]]:
    recorded = case.cli(("runs", "outputs", run_id), environment)
    step = recorded["repositories"][0]["steps"][0]
    facts, payloads = [], {}
    for artifact in step["artifacts"]:
        assert artifact["status"] == "PRESENT" and artifact["retained_object"] is not None
        destination = case.root / f"{label}-{Path(artifact['path']).name}"
        case.cli(
            (
                "runs",
                "export-artifact",
                run_id,
                "--step",
                step["step_id"],
                "--path",
                artifact["path"],
                "--output",
                str(destination),
            ),
            environment,
        )
        with destination.open("rb") as stream:
            payload = stream.read(1024 * 1024 + 1)
        assert len(payload) == artifact["byte_count"] <= 1024 * 1024
        assert hashlib.sha256(payload).hexdigest() == artifact["sha256"]
        retained = artifact["retained_object"]
        facts.append(
            RetainedArtifactFact(
                run_id,
                artifact["path"],
                artifact["sha256"],
                len(payload),
                retained["object_id"],
                retained["reference_id"],
            )
        )
        payloads[artifact["path"]] = payload
    return tuple(facts), payloads


def exercise_ci_ml_contention(
    command: Path, root: Path, environment: Mapping[str, str]
) -> CiMlContentionFacts:
    """Execute genuine native workloads through the same selected CLI/state root."""
    selected = dict(environment)
    selected.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    case = _CliCase(command, root)
    try:
        configurations, origin, commit, lock_hashes = _author_workflows(case, selected)
        locks = {
            label: path.with_name("goon.lock").read_bytes()
            for label, path in configurations.items()
        }
        active = case.launch(configurations["a"], selected)
        first = case.held("a", selected, active)
        # Real Iris training has passed before the ordinary gate starts. Both
        # competing workflows were prepared before the original lease existed.
        _steps(case, first.run_id, selected)
        assert case.cli(("runs", "show", first.run_id), selected)["status"] == "RUNNING"
        original_handoff = case.cli(("runs", "handoff", first.run_id), selected)
        before = case.cli(("runs", "list"), selected)
        busy_codes = []
        for label in ("b", "ci"):
            refused = case.cli(
                ("run", str(configurations[label]), "--no-input", "--yes"), selected, expected=2
            )
            assert refused["code"] == "EXECUTION_BUSY"
            assert refused["stage"] == "execution_admission" and refused.get("fleet_run_id") is None
            assert (
                not (root / f"{label}.ready").exists() and not (root / f"{label}.effects").exists()
            )
            busy_codes.append(refused["code"])
        assert case.cli(("runs", "list"), selected)["items"] == before["items"]
        active_outputs = case.cli(("runs", "outputs", first.run_id), selected)
        assert active_outputs["fleet_run_id"] == first.run_id
        active_artifacts = len(active_outputs["repositories"][0]["steps"][0]["artifacts"])
        case.live(first)
        (root / "a.release").touch()
        assert case.finish(active)["outcome"] == "SUCCESS"
        case.retired(first)
        first_steps = _steps(case, first.run_id, selected, terminal=True)
        training_steps = [first_steps[0]]
        artifacts, first_payloads = _exports(case, first.run_id, "a", selected)
        all_artifacts = list(artifacts)
        training_payloads = [first_payloads]
        for label in ("b", "ci"):
            accepted = case.launch(configurations[label], selected)
            observed = case.held(label, selected, accepted)
            _steps(case, observed.run_id, selected)
            (root / f"{label}.release").touch()
            assert case.finish(accepted)["outcome"] == "SUCCESS"
            case.retired(observed)
            steps = _steps(case, observed.run_id, selected, terminal=True)
            recorded, payloads = _exports(case, observed.run_id, label, selected)
            all_artifacts.extend(recorded)
            if label == "b":
                training_steps.append(steps[0])
                training_payloads.append(payloads)
            else:
                assert steps[0]["uv"]["group"] == "ci"
                assert steps[0]["project_source"]["application"]["path"] == "orders"
                assert steps[0]["project_source"]["application"]["commit"] == commit
                assert set(payloads) == {"junit.xml"}
                junit = ElementTree.fromstring(payloads["junit.xml"])
                suites = list(junit.iter("testsuite"))
                tests = sum(int(row.attrib["tests"]) for row in suites)
                assert tests > 0
                assert all(
                    int(row.attrib["failures"])
                    == int(row.attrib["errors"])
                    == int(row.attrib["skipped"])
                    == 0
                    for row in suites
                )
        for step in training_steps:
            assert step["uv"].get("group") is None
            assert step["project_source"]["application"]["commit"] == commit
            assert step["project_source"]["application"]["path"] == "iris"
        assert training_steps[0]["project_source"] == training_steps[1]["project_source"]
        for seed, payloads in zip((42, 7), training_payloads, strict=True):
            assert set(payloads) == {METRICS, MODEL}
            metrics = json.loads(payloads[METRICS])
            assert metrics["random_seed"] == seed
            assert metrics["training_rounds"] == metrics["completed_training_rounds"] == 3
            assert metrics["accuracy"] >= metrics["minimum_accuracy"]
            model = json.loads(payloads[MODEL])
            assert model["coefficients"] and model["scaler_mean"]
        assert training_payloads[0][MODEL] != training_payloads[1][MODEL]
        assert len({row.reference_id for row in all_artifacts}) == len(all_artifacts)
        # Fresh processes still read/export the original retained result after
        # later Runs and cleanup; no scratch output is used as retention.
        assert _exports(case, first.run_id, "a-reopened", selected)[1] == first_payloads
        assert (
            case.cli(("runs", "handoff", first.run_id), selected)["source_admissions"]
            == original_handoff["source_admissions"]
        )
        assert case.cli(("runs", "show", first.run_id), selected)["outcome"] == "SUCCESS"
        final = _steps(case, first.run_id, selected, terminal=True)
        assert final[0] == first_steps[0]
        assert all(step["settlement"]["cleanup"] == "VERIFIED" for step in final)
        assert len({fact.run_id for fact in case.runs}) == 3
        assert all(
            (root / f"{label}.effects").read_text() == label + "\n" for label in configurations
        )
        assert all(
            path.with_name("goon.lock").read_bytes() == locks[label]
            for label, path in configurations.items()
        )
        for name, expected_hash in zip(("iris", "orders"), lock_hashes, strict=True):
            assert (
                hashlib.sha256((origin / name / "uv.lock").read_bytes()).hexdigest()
                == expected_hash
            )
            assert (
                origin / name / ".venv/developer-sentinel"
            ).read_text() == "developer environment preserved\n"
        scratch = ApplicationStatePathLayout.from_database(
            Path(selected["OH_GOON_DB"])
        ).run_workspaces
        assert not scratch.exists() or not any(scratch.iterdir())
        return CiMlContentionFacts(
            commit,
            lock_hashes,
            tuple(case.runs),
            (busy_codes[0], busy_codes[1]),
            tuple(all_artifacts),
            (42, 7),
            3,
            tests,
            active_artifacts,
            tuple(case.commands),
            "Linux shared SQLite admission; real Iris fits and native pytest; developer environments preserved and owned scratch retired; no parallel capacity, global CPU cap, strict execution or other-host qualification",
        )
    finally:
        case.close()


def test_native_ci_and_training_conserve_shared_state_results() -> None:
    root = Path(tempfile.mkdtemp(prefix="oh-goon-ci-ml-contention-"))
    environment = _environment(root / "state")
    # HUG-130's dependency-free toy forces offline UV. These complete native
    # projects may acquire their committed locked dependencies normally.
    environment.pop("UV_OFFLINE", None)
    try:
        facts = exercise_ci_ml_contention(
            Path(sys.executable).with_name("oh-goon"), root, environment
        )
    except BaseException as error:
        error.add_note(
            f"Private contention fixture retained at {root}; establish owner quiescence before removal"
        )
        raise
    else:
        print("CI_ML_CONTENTION=" + json.dumps(asdict(facts), sort_keys=True))
        shutil.rmtree(root)
