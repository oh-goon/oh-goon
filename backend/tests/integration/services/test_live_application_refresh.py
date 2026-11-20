"""Live application refresh cannot replace an already admitted native Run."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from oh_goon.core.path_layouts import ApplicationStatePathLayout
from tests.integration.services.test_ci_ml_contention import (
    METRICS,
    MODEL,
    ROOT,
    RetainedArtifactFact,
    _commit,
    _exports,
    _git,
    _steps,
)
from tests.integration.services.test_cli_concurrency import (
    CliCommandFact,
    NativeRunFact,
    _CliCase,
    _environment,
)

pytestmark = [
    pytest.mark.process,
    pytest.mark.no_resume,
    pytest.mark.skipif(
        sys.platform != "linux" or any(shutil.which(tool) is None for tool in ("git", "uv")),
        reason="native Linux PIDfds and git/uv required",
    ),
]
BRANCH = "qualification"


@dataclass(frozen=True)
class LiveApplicationRefreshFacts:
    application_commits: tuple[str, str]
    application_lock_sha256: str
    execution_lock_sha256: tuple[str, str]
    producer_implementation_identity: str
    producer_version: str
    native_http_timeout: str | None
    native_http_retries: str | None
    runs: tuple[NativeRunFact, ...]
    step_execution_ids: tuple[str, str]
    project_content_sha256: tuple[str, str]
    project_archive_sha256: tuple[str, str]
    owner_repository_id: str
    artifacts: tuple[RetainedArtifactFact, ...]
    commands: tuple[CliCommandFact, ...]
    scope: str


def _workflow(case: _CliCase, environment: Mapping[str, str]) -> tuple[Path, Path, str]:
    origin = case.root / "application-origin"
    shutil.copytree(
        ROOT / "templates/goons/ml/iris-train",
        origin / "iris",
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "ml-output"
        ),
    )
    _git(origin, environment, "init", "-q", "-b", BRANCH)
    first_commit = _commit(origin, environment)
    (origin / "iris/.venv").mkdir()
    (origin / "iris/.venv/developer-sentinel").write_text("developer environment preserved\n")
    configuration = case.prepare("a", case.root / "repository", environment)
    _commit(case.root / "repository", environment)
    package = configuration.parent / "goons/ml/iris-train"
    package.mkdir(parents=True)
    (package / "README.md").write_text("# ml/iris-train\n")
    document = yaml.safe_load((origin / "iris/goon.yml").read_text())
    document["application"] = {"git": str(origin), "ref": BRANCH, "path": "iris"}
    (package / "goon.yml").write_text(yaml.safe_dump(document, sort_keys=False))
    configuration.with_name("plan.yml").write_text(
        "goons:\n- import: ml/iris-train\n  from: goons\n"
        "- import: general/concurrency\n  from: goons\n"
    )
    selection = json.loads(configuration.read_text())
    selection["inputs"] = {"random_seed": 42, "training_rounds": 3}
    configuration.write_text(json.dumps(selection))
    prepared = case.cli(("workflow", "prepare", str(configuration), "--no-input"), environment)
    assert prepared["status"] == "prepared"
    return configuration, origin, first_commit


def _application_pin(configuration: Path) -> dict[str, Any]:
    lock = yaml.safe_load(configuration.with_name("goon.lock").read_text())
    pins = [row for row in lock["execution_sources"] if row["kind"] == "application"]
    assert len(pins) == 1
    pin: dict[str, Any] = pins[0]
    assert pin["ref"] == BRANCH and pin["path"] == "iris"
    return pin


def _admitted_event(case: _CliCase, run_id: str, environment: Mapping[str, str]) -> dict[str, Any]:
    batch = case.cli(("runs", "events", run_id, "--limit", "1"), environment)
    events = batch["events"]
    assert len(events) == 1 and events[0]["event_type"] == "RUN_ADMITTED"
    event: dict[str, Any] = events[0]
    assert event["fleet_run_id"] == run_id
    return event


def _source(event: dict[str, Any], commit: str) -> dict[str, Any]:
    target = event["target"]
    source: dict[str, Any] = target["plan"]["steps"][0]["project_source"]
    assert source["application"]["commit"] == commit
    assert source["application"]["ref"] == BRANCH
    assert source["application"]["path"] == "iris"
    deliveries = target["project_deliveries"]
    assert len(deliveries) == 1
    assert deliveries[0]["project_sha256"] == source["content_sha256"]
    assert deliveries[0]["owner_repository_id"] == event["targets"][0]["repository_id"]
    return source


def exercise_live_application_refresh(
    command: Path, root: Path, environment: Mapping[str, str]
) -> LiveApplicationRefreshFacts:
    """Refresh one future lock while its original fitted Run remains claimed."""
    selected = dict(environment)
    selected.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    case = _CliCase(command, root)
    try:
        # Query the actual selected Python's supported producer API, including
        # when this controller drives an installed wheel rather than source.
        python = command.with_name("python.exe" if os.name == "nt" else "python")
        producer = case.finish(
            case.start(
                (
                    "-I",
                    "-c",
                    "import json;from oh_goon.integrations.hashmarks import "
                    "require_loaded_hashmarks_runtime,loaded_hashmarks_version;"
                    "print(json.dumps({'identity':require_loaded_hashmarks_runtime(),"
                    "'version':loaded_hashmarks_version()}))",
                ),
                selected,
                program=python,
            )
        )
        assert producer["identity"].startswith("sha256:") and producer["version"]
        configuration, origin, first_commit = _workflow(case, selected)
        native_lock = (origin / "iris/uv.lock").read_bytes()
        first_lock = configuration.with_name("goon.lock").read_bytes()
        assert _application_pin(configuration)["commit"] == first_commit
        active = case.launch(configuration, selected)
        # Cold native uv preparation can exceed the toy transport's 35s
        # readiness window. The original 180s whole-cell budget still applies.
        first = case.held("a", selected, active, readiness_timeout=90)
        _steps(case, first.run_id, selected)
        admitted = _admitted_event(case, first.run_id, selected)
        first_source = _source(admitted, first_commit)
        handoff = case.cli(("runs", "handoff", first.run_id), selected)
        assert handoff["status"] == "RUNNING"

        # Only documentation changes: native training code, inputs and uv.lock
        # remain identical, while the moving application ref advances.
        readme = origin / "iris/README.md"
        with readme.open("a") as stream:
            stream.write("\nNative qualification documentation revision.\n")
        _git(origin, selected, "add", "iris/README.md")
        _git(
            origin,
            selected,
            "-c",
            "user.name=Contention fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "documentation only application revision",
        )
        second_commit = _git(origin, selected, "rev-parse", "HEAD")
        assert second_commit != first_commit
        assert (
            _git(origin, selected, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
            == "iris/README.md"
        )
        refreshed = case.cli(
            ("workflow", "prepare", str(configuration), "--refresh", "applications", "--no-input"),
            selected,
        )
        assert refreshed["status"] == "prepared"
        second_lock = configuration.with_name("goon.lock").read_bytes()
        assert second_lock != first_lock
        assert _application_pin(configuration)["commit"] == second_commit
        assert (origin / "iris/uv.lock").read_bytes() == native_lock
        assert _admitted_event(case, first.run_id, selected) == admitted
        after_refresh = case.cli(("runs", "handoff", first.run_id), selected)
        assert after_refresh["status"] == "RUNNING"
        assert after_refresh["source_admissions"] == handoff["source_admissions"]
        assert after_refresh["recorded_steps"]["steps"][0]["project_source"] == first_source
        case.live(first)
        (root / "a.release").touch()
        assert case.finish(active)["outcome"] == "SUCCESS"
        case.retired(first)
        first_step = _steps(case, first.run_id, selected, terminal=True)[0]
        assert first_step["project_source"] == first_source
        first_artifacts, first_payloads = _exports(case, first.run_id, "first", selected)
        assert (root / "a.effects").read_text() == "a\n"

        # Retire only these exact completed gate controls. The same authored
        # selection then starts a fresh Run with the refreshed application pin.
        for name in ("a.ready", "a.release", "a.effects"):
            (root / name).unlink()
        repeated = case.launch(configuration, selected)
        second = case.held("a", selected, repeated, readiness_timeout=90)
        case.live(second)
        (root / "a.release").touch()
        assert case.finish(repeated)["outcome"] == "SUCCESS"
        case.retired(second)
        second_step = _steps(case, second.run_id, selected, terminal=True)[0]
        second_event = _admitted_event(case, second.run_id, selected)
        second_source = _source(second_event, second_commit)
        assert second_step["project_source"] == second_source
        assert second_source["content_sha256"] != first_source["content_sha256"]
        assert second_source["archive_sha256"] != first_source["archive_sha256"]
        assert first.run_id != second.run_id and first.worker != second.worker
        assert first_step["step_execution_id"] != second_step["step_execution_id"]
        second_artifacts, second_payloads = _exports(case, second.run_id, "second", selected)
        assert set(first_payloads) == set(second_payloads) == {METRICS, MODEL}
        assert first_payloads == second_payloads
        metrics = json.loads(first_payloads[METRICS])
        assert metrics["random_seed"] == 42
        assert metrics["training_rounds"] == metrics["completed_training_rounds"] == 3
        assert metrics["accuracy"] >= metrics["minimum_accuracy"]
        assert json.loads(first_payloads[MODEL])["coefficients"]
        owner = admitted["targets"][0]["repository_id"]
        for step in (first_step, second_step):
            assert step["uv"].get("group") is None
            assert all(
                item["retained_object"]["owner_repository_id"] == owner
                for item in step["artifacts"]
            )
        first_by_path = {item.path: item for item in first_artifacts}
        second_by_path = {item.path: item for item in second_artifacts}
        assert first_by_path.keys() == second_by_path.keys() == {METRICS, MODEL}
        for path, original in first_by_path.items():
            newer = second_by_path[path]
            assert original.sha256 == newer.sha256 and original.object_id == newer.object_id
            assert original.byte_count == newer.byte_count
            assert original.reference_id != newer.reference_id
        assert _steps(case, first.run_id, selected, terminal=True)[0] == first_step
        assert _exports(case, first.run_id, "first-reopened", selected)[1] == first_payloads
        assert _admitted_event(case, first.run_id, selected) == admitted
        assert configuration.with_name("goon.lock").read_bytes() == second_lock
        assert (origin / "iris/uv.lock").read_bytes() == native_lock
        assert (
            origin / "iris/.venv/developer-sentinel"
        ).read_text() == "developer environment preserved\n"
        assert (root / "a.effects").read_text() == "a\n"
        scratch = ApplicationStatePathLayout.from_database(
            Path(selected["OH_GOON_DB"])
        ).run_workspaces
        assert not scratch.exists() or not any(scratch.iterdir())
        return LiveApplicationRefreshFacts(
            (first_commit, second_commit),
            hashlib.sha256(native_lock).hexdigest(),
            (hashlib.sha256(first_lock).hexdigest(), hashlib.sha256(second_lock).hexdigest()),
            producer["identity"],
            producer["version"],
            selected.get("UV_HTTP_TIMEOUT"),
            selected.get("UV_HTTP_RETRIES"),
            tuple(case.runs),
            (first_step["step_execution_id"], second_step["step_execution_id"]),
            (first_source["content_sha256"], second_source["content_sha256"]),
            (first_source["archive_sha256"], second_source["archive_sha256"]),
            owner,
            (*first_artifacts, *second_artifacts),
            tuple(case.commands),
            "Linux live application-branch refresh preserves original admitted source/delivery; real identical Iris fits retain equal objects with distinct Run-authorized references; no cache-hit, overlapping fit, strict or other-host qualification",
        )
    finally:
        case.close()


def test_live_application_refresh_preserves_frozen_run_and_distinct_artifact_references() -> None:
    root = Path(tempfile.mkdtemp(prefix="oh-goon-live-refresh-"))
    environment = _environment(root / "state")
    environment.pop("UV_OFFLINE", None)
    environment.setdefault("UV_HTTP_TIMEOUT", "10")
    environment.setdefault("UV_HTTP_RETRIES", "0")
    try:
        facts = exercise_live_application_refresh(
            Path(sys.executable).with_name("oh-goon"), root, environment
        )
    except BaseException as error:
        error.add_note(
            f"Private live refresh fixture retained at {root}; establish owner quiescence before removal"
        )
        raise
    else:
        print("LIVE_APPLICATION_REFRESH=" + json.dumps(asdict(facts), sort_keys=True))
        shutil.rmtree(root)
