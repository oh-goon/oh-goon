"""Real captured Python Runs conserve retained history while owned scratch is reclaimed."""

from __future__ import annotations

import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import ExecutionBusy, RepositoryEffectQuarantined
from oh_goon.core.file_digest import sha256_file_with_size
from oh_goon.core.models.fleet_plan_composition import PlanInputValue
from oh_goon.core.models.fleet_plan_evidence import MissingPlanArtifact, PresentPlanArtifact
from oh_goon.core.models.run import FleetRunDetail
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import repository_id
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.runtime.storage_lifecycle import (
    measure_storage,
    prune_generic_storage,
    storage_census,
)
from tests.factories.python_goon_projects import write_isolated_build_project

ROOT = Path(__file__).resolve().parents[4]
SCENARIOS = ("same", "same", "source-refresh", "different", "fail", "missing", "cancel")
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("uv") is None,
    reason="real storage campaign requires Linux process identity and native uv",
)

_PRODUCER = """import json
import os
from pathlib import Path
import sys

def main():
    mode, ready, hold = sys.argv[1:]
    if mode == "missing":
        print("no optional artifact produced")
        return 0
    value = 2 if mode == "different" else 1
    Path("model.json").write_text(json.dumps({"weights": [value] * 1024}, sort_keys=True) + "\\n")
    if mode == "cancel":
        observed = {"pid": os.getpid(), "cwd": str(Path.cwd()),
                    "home": str(Path.home()), "environment": os.environ["UV_PROJECT_ENVIRONMENT"]}
        with Path(ready).open("w") as channel:
            channel.write(json.dumps(observed))
        with Path(hold).open("rb") as channel:
            channel.read(1)
    print("native captured output producer")
    return 23 if mode == "fail" else 0
"""


@dataclass
class _ObservedRun:
    run_id: str
    scenario: str
    recorded_run: dict[str, Any]
    recorded_operations: list[dict[str, Any]]
    source_sha256: str
    source_references: tuple[str, ...]
    artifacts: tuple[PresentPlanArtifact, ...]
    missing_artifacts: tuple[str, ...]
    step_id: str
    tape_status: str


def _private_environment(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    selected = {
        "OH_GOON_DB": str(root / "data/oh-goon/runtime.sqlite"),
        "OH_GOON_LEAGUE_FILE": str(root / "config/league.yml"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_STATE_HOME": str(root / "state"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "UV_CACHE_DIR": str(root / "native-uv-cache"),
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
    }
    for name, value in selected.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("OH_GOON_EXECUTORS_FILE", raising=False)
    for name in ("config", "data", "state", "cache", "native-uv-cache"):
        (root / name).mkdir(mode=0o700, parents=True)
    return dict(os.environ)


def _census(environment: dict[str, str], *, phase: str) -> dict[str, Any]:
    observed = storage_census(environment)
    classes = []
    for item in observed.inventory:
        measured = measure_storage(item.path)
        classes.append(
            {
                "storage_id": item.storage_id,
                "owner": item.owner,
                "durability": item.durability,
                "cleanup_authority": item.cleanup_authority,
                "generic_prune": item.generic_prune,
                "path": str(item.path) if item.path is not None else None,
                "measurement": asdict(measured) if measured is not None else None,
            }
        )
    native_cache = measure_storage(Path(environment["UV_CACHE_DIR"]))
    return {
        "phase": phase,
        "measurement_kind": "logical lstat bytes; classes may overlap; no summed physical total",
        "classes": classes,
        "unclassified": [
            {"path": str(item.path), "reason": item.reason, "root_kind": item.root_kind}
            for item in observed.unclassified
        ],
        "native_uv_cache": {
            "owner": "native uv; reported only, never generically pruned",
            "path": environment["UV_CACHE_DIR"],
            "measurement": asdict(native_cache) if native_cache is not None else None,
        },
        "native_git_cache": "reported by the skills.git-sources class; native Git owns maintenance",
    }


def _installed_cli(
    root: Path, environment: dict[str, str], *arguments: str, expected: int = 0
) -> str:
    completed = subprocess.run(
        [str(Path(sys.executable).with_name("oh-goon")), *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == expected, (completed.stdout[-8192:], completed.stderr[-8192:])
    return completed.stdout


def _package(root: Path) -> tuple[Path, Path, Path, Path]:
    repository = root / "repositories/target"
    repository.mkdir(parents=True)
    (repository / "subject.txt").write_text("registered storage campaign subject\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    package = root / "workflow/goons/general/isolated-build"
    write_isolated_build_project(package)
    implementation = package / "isolated_app.py"
    implementation.write_text(_PRODUCER)
    ready, hold = root / "ready", root / "hold"
    os.mkfifo(ready)
    os.mkfifo(hold)
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "uv": {"lock_mode": "frozen"},
                "inputs": {"mode": {"type": "string", "default": "same"}},
                "steps": [
                    {
                        "id": "produce",
                        "run": (
                            "isolated-build ${{ inputs.mode }} "
                            f"{shlex.quote(str(ready))} {shlex.quote(str(hold))}"
                        ),
                        "artifacts": [{"path": "model.json", "retention": "content_store"}],
                    }
                ],
            },
            sort_keys=False,
        )
    )
    developer = package / ".venv/developer-owned"
    developer.parent.mkdir()
    developer.write_text("product execution must not replace this environment\n")
    plan = root / "workflow/plan.yml"
    plan.write_text("goons:\n- import: general/isolated-build\n  from: goons\n")
    return repository, plan, ready, hold


def _prepared(
    runtime: ApplicationRuntime, plan: Path, inputs: dict[str, PlanInputValue]
) -> FleetPlanTarget:
    runtime.plans.lock(plan, inputs=inputs)
    return runtime.plans.prepare_execution_target(plan, inputs=inputs)


def _record(detail: FleetRunDetail, scenario: str) -> _ObservedRun:
    assert isinstance(detail.run.target, FleetPlanTarget)
    assert detail.run.target.project_deliveries
    assert len(detail.game_tape.repositories) == 1
    (step,) = detail.game_tape.repositories[0].steps
    assert step.project_source is not None
    return _ObservedRun(
        run_id=detail.run.fleet_run_id,
        scenario=scenario,
        recorded_run=detail.run.model_dump(mode="json", exclude_computed_fields=True),
        recorded_operations=[
            item.model_dump(mode="json", exclude_computed_fields=True) for item in detail.operations
        ],
        source_sha256=step.project_source.content_sha256,
        source_references=tuple(item.reference_id for item in detail.run.target.project_deliveries),
        artifacts=tuple(item for item in step.artifacts if isinstance(item, PresentPlanArtifact)),
        missing_artifacts=tuple(
            item.path for item in step.artifacts if isinstance(item, MissingPlanArtifact)
        ),
        step_id=step.step_id,
        tape_status=detail.game_tape.status.value,
    )


def _cancel_running(
    runtime: ApplicationRuntime,
    target: FleetPlanTarget,
    owner: str,
    run_id: str,
    ready: Path,
    environment: dict[str, str],
    censuses: list[dict[str, Any]],
) -> FleetRunDetail:
    with os.fdopen(os.open(ready, os.O_RDWR | os.O_NONBLOCK), "rb", buffering=0) as channel:
        started = runtime.runs.start_prepared_plan(
            target, repository_ids={owner}, fleet_run_id=run_id
        )
        try:
            assert select.select([channel], [], [], 30)[0], runtime.runs.get(run_id)
            active = json.loads(channel.read(4096))
            paths = [Path(active[key]) for key in ("cwd", "home", "environment")]
            assert all(path.is_dir() for path in paths)
            censuses.append(_census(environment, phase=f"{run_id}:active"))
            with pytest.raises(ExecutionBusy):
                prune_generic_storage(environment)
            assert all(path.is_dir() for path in paths)
        finally:
            if not runtime.runs.get(started.fleet_run_id).run.status.terminal:
                runtime.runs.cancel(started.fleet_run_id)
        terminal = runtime.runs.wait_for_terminal(run_id, timeout=30)
    assert terminal.run.status.value == "CANCELLED"
    assert terminal.game_tape.status.value == "CANCELLED"
    assert all(not path.exists() for path in paths)
    with pytest.raises(ProcessLookupError):
        os.kill(active["pid"], 0)
    return terminal


def _assert_history(runtime: ApplicationRuntime, observations: list[_ObservedRun]) -> None:
    for observed in observations:
        readback = runtime.runs.get(observed.run_id)
        assert readback.run.model_dump(mode="json", exclude_computed_fields=True) == (
            observed.recorded_run
        )
        assert [
            item.model_dump(mode="json", exclude_computed_fields=True)
            for item in readback.operations
        ] == observed.recorded_operations
    history = runtime.runs.history(limit=100)
    assert {item.run.fleet_run_id for item in history.items} == {
        item.run_id for item in observations
    }


def _assert_retained(
    store: FleetContentStore, observations: list[_ObservedRun], owner: str
) -> set[str]:
    objects: set[str] = set()
    for observed in observations:
        for reference in observed.source_references:
            objects.add(
                store.get(reference, owner_repository_id=owner, expected_kind="file").object_id
            )
        for artifact in observed.artifacts:
            retained = artifact.retained_object
            assert retained is not None
            stored = store.get(
                retained.reference_id, owner_repository_id=owner, expected_kind="file"
            )
            assert stored.identity.sha256 == retained.sha256
            assert stored.identity.byte_count == artifact.byte_count == retained.byte_count
            assert sha256_file_with_size(stored.path) == (artifact.sha256, artifact.byte_count)
            objects.add(stored.object_id)
    return objects


def _iris_runs(
    runtime: ApplicationRuntime, root: Path, owner: str, start: int
) -> list[_ObservedRun]:
    package = root / "iris-workflow/goons/ml/iris-train"
    shutil.copytree(
        ROOT / "templates/goons/ml/iris-train",
        package,
        ignore=shutil.ignore_patterns(
            ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "ml-output"
        ),
    )
    plan = root / "iris-workflow/plan.yml"
    plan.write_text("goons:\n- import: ml/iris-train\n  from: goons\n")
    observations = []
    prepared: dict[int, FleetPlanTarget] = {}
    for ordinal, seed in enumerate((42, 42, 7), start=start):
        if seed not in prepared:
            prepared[seed] = _prepared(
                runtime,
                plan,
                {
                    "random_seed": seed,
                    "test_size_percent": 20,
                    "minimum_accuracy_percent": 90,
                    "max_iterations": 300,
                },
            )
        target = prepared[seed]
        started = runtime.runs.start_prepared_plan(
            target, repository_ids={owner}, fleet_run_id=f"storage-{ordinal:03d}-iris-{seed}"
        )
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=180)
        assert detail.game_tape.status.value == "PASSED", detail
        observed = _record(detail, f"iris-{seed}")
        assert len(observed.artifacts) == 2 and not observed.missing_artifacts
        observations.append(observed)
    models = [
        next(item for item in run.artifacts if item.path.endswith("iris-model-parameters.json"))
        for run in observations
    ]
    assert models[0].sha256 == models[1].sha256
    assert models[0].sha256 != models[2].sha256
    assert (
        observations[0].source_sha256
        == observations[1].source_sha256
        == observations[2].source_sha256
    )
    assert not (package / "ml-output").exists()
    return observations


def _write_report(label: str, root: Path, report: dict[str, Any]) -> Path:
    destination = Path(
        os.environ.get("OH_GOON_STORAGE_CAMPAIGN_REPORT_DIR", str(root / "evidence"))
    )
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = destination / f"{label}.json"
    path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    path.chmod(0o600)
    return path


def _exercise_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, count: int, iris: bool
) -> None:
    started_at = monotonic()
    root = tmp_path / "storage-campaign"
    root.mkdir(mode=0o700)
    environment = _private_environment(root, monkeypatch)
    repository, plan, ready, _hold = _package(root)
    owner = repository_id(repository)
    layout = ApplicationStatePathLayout.from_database(Path(environment["OH_GOON_DB"]))
    observations: list[_ObservedRun] = []
    censuses = [_census(environment, phase="before")]
    report: dict[str, Any] = {
        "status": "INCOMPLETE",
        "scope": "trusted-local actual Fleet Runs; captured Python; logical storage census",
        "requested_runs": count,
        "strict_wsl_container_qualification": "NOT_RUN",
        "censuses": censuses,
    }
    runtime = ApplicationRuntime.build(standard_local=True)
    label = "daily-100" if iris else "bounded-7"
    try:
        runtime.locations.add(repository.parent)
        writer_count = count - 4 if iris else count
        writer_scenarios = SCENARIOS[:-1] if iris else SCENARIOS
        prepared: dict[str, FleetPlanTarget] = {}
        for ordinal in range(writer_count):
            scenario = writer_scenarios[ordinal % len(writer_scenarios)]
            if ordinal == 2:
                implementation = plan.parent / "goons/general/isolated-build/isolated_app.py"
                with implementation.open("a") as stream:
                    stream.write("\n# captured source refresh\n")
                prepared.clear()
            mode = "same" if scenario == "source-refresh" else scenario
            if scenario == "source-refresh" and ordinal != 2:
                scenario = "same-after-refresh"
            if mode not in prepared:
                prepared[mode] = _prepared(runtime, plan, {"mode": mode})
            target = prepared[mode]
            run_id = f"storage-{ordinal:03d}-{scenario}"
            if scenario == "cancel":
                detail = _cancel_running(
                    runtime, target, owner, run_id, ready, environment, censuses
                )
            else:
                started = runtime.runs.start_prepared_plan(
                    target, repository_ids={owner}, fleet_run_id=run_id
                )
                detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=60)
            observed = _record(detail, scenario)
            expected = (
                "CANCELLED"
                if scenario == "cancel"
                else "ATTENTION_REQUIRED"
                if scenario == "fail"
                else "PASSED"
            )
            assert observed.tape_status == expected, detail
            if scenario == "missing":
                assert observed.missing_artifacts == ("model.json",) and observed.artifacts == ()
            elif scenario == "cancel":
                assert observed.artifacts == ()
            else:
                assert len(observed.artifacts) == 1 and not observed.missing_artifacts
            observations.append(observed)
            assert not list(layout.run_workspaces.glob("oh-goon-plan-*"))
            assert not (repository / "model.json").exists()
            assert not (plan.parent / "goons/general/isolated-build/model.json").exists()
            censuses.append(_census(environment, phase=f"{run_id}:terminal"))
            print(f"storage campaign {ordinal + 1}/{count}: {scenario} -> {expected}", flush=True)

        assert observations[0].source_sha256 == observations[1].source_sha256
        assert observations[0].source_sha256 != observations[2].source_sha256
        assert len({observations[index].artifacts[0].sha256 for index in (0, 1, 2)}) == 1
        assert observations[3].artifacts[0].sha256 != observations[0].artifacts[0].sha256
        if iris:
            observations.extend(_iris_runs(runtime, root, owner, writer_count))
            censuses.append(_census(environment, phase="iris:terminal"))
            # Native cancellation intentionally quarantines unknown effects. Do
            # it last instead of clearing that safety cut to continue a loop.
            prepared["cancel"] = _prepared(runtime, plan, {"mode": "cancel"})
            run_id = f"storage-{count - 1:03d}-cancel"
            detail = _cancel_running(
                runtime, prepared["cancel"], owner, run_id, ready, environment, censuses
            )
            cancelled = _record(detail, "cancel")
            assert cancelled.artifacts == () and cancelled.tape_status == "CANCELLED"
            observations.append(cancelled)
            censuses.append(_census(environment, phase=f"{run_id}:terminal"))
        assert len(observations) == len({item.run_id for item in observations}) == count
        with pytest.raises(RepositoryEffectQuarantined):
            runtime.runs.start_prepared_plan(
                prepared["cancel"],
                repository_ids={owner},
                fleet_run_id="storage-refused-after-cancellation",
            )
        _assert_history(runtime, observations)
        store = FleetContentStore(layout.content_store)
        _assert_retained(store, observations, owner)
        duplicate_objects: set[str] = set()
        for index in (0, 1, 2):
            retained = observations[index].artifacts[0].retained_object
            assert retained is not None
            duplicate_objects.add(
                store.get(
                    retained.reference_id, owner_repository_id=owner, expected_kind="file"
                ).object_id
            )
        assert len(duplicate_objects) == 1
        # These two exact test promotions have explicit disposal decisions. Run
        # model/source references are never released to manufacture a plateau.
        unreachable = store.put_frozen_bytes(
            b"explicit unreachable test promotion", owner_repository_id=owner
        )
        leased = store.put_frozen_bytes(
            b"explicit live-leased test promotion", owner_repository_id=owner
        )
        with store.acquire_lease(
            leased.reference_id, owner_repository_id=owner, expected_kind="file"
        ):
            store.release_reference(unreachable.reference_id, owner_repository_id=owner)
            store.release_reference(leased.reference_id, owner_repository_id=owner)
            censuses.append(_census(environment, phase="before-prune"))
            pruned = prune_generic_storage(environment)
            assert unreachable.object_id in pruned.removed_content_objects
            assert leased.object_id not in pruned.removed_content_objects and leased.path.is_file()
            assert not unreachable.path.exists()
            _assert_retained(store, observations, owner)
            censuses.append(_census(environment, phase="after-prune-with-live-lease"))
        after_lease = prune_generic_storage(environment)
        assert leased.object_id in after_lease.removed_content_objects
        assert not leased.path.exists()
        protected_objects = _assert_retained(store, observations, owner)
        _assert_history(runtime, observations)
        with pytest.raises(RepositoryEffectQuarantined):
            runtime.runs.start_prepared_plan(
                prepared["cancel"],
                repository_ids={owner},
                fleet_run_id="storage-refused-after-prune",
            )
        _assert_history(runtime, observations)
        developer = plan.parent / "goons/general/isolated-build/.venv/developer-owned"
        assert developer.read_text() == "product execution must not replace this environment\n"
        assert (repository / "subject.txt").read_text() == "registered storage campaign subject\n"
        runtime.shutdown(wait_for_runs=True)
        runtime = ApplicationRuntime.build(standard_local=True)
        _assert_history(runtime, observations)
        exports = root / "promoted-export-checks"
        exports.mkdir()
        representative = (
            next(item for item in observations if item.scenario == "iris-7")
            if iris
            else observations[2]
        )
        for observed in (observations[0], observations[3], representative):
            assert observed.tape_status == "PASSED" and observed.artifacts
            artifact = (
                next(
                    item
                    for item in observed.artifacts
                    if item.path == "ml-output/iris-model-parameters.json"
                )
                if observed.scenario == "iris-7"
                else observed.artifacts[0]
            )
            exported = runtime.runs.export_artifact(
                observed.run_id,
                step_id=observed.step_id,
                artifact_path=artifact.path,
                destination=exports / f"{observed.run_id}.json",
            )
            assert exported.cleanup_status == "verified"
            assert sha256_file_with_size(exported.destination) == (
                artifact.sha256,
                artifact.byte_count,
            )
        censuses.append(_census(environment, phase="reopened-readback"))
        before_status = sha256_file_with_size(Path(environment["OH_GOON_DB"]))
        status = json.loads(
            _installed_cli(root, environment, "storage", "status", "--measure", "--json")
        )
        assert sha256_file_with_size(Path(environment["OH_GOON_DB"])) == before_status
        records = {item["storage_id"]: item for item in status["storage"]}
        assert records["application.database"]["measurement"]["bytes"] > 0
        assert records["application.retained-content"]["durability"] == "durable-retained-content"
        assert records["application.content-staging"]["generic_prune"] is True
        runtime.shutdown(wait_for_runs=True)
        report.update(
            status="PASS",
            source_refreshes=1,
            prepared_target_reuse="immutable captured source/inputs only; every Run admits and executes anew",
            objects_protected_by_run_references=len(protected_objects),
            duplicate_model_objects=len(duplicate_objects),
            fresh_cli_storage_status=status,
            prune=pruned.to_payload(),
            prune_after_lease_close=after_lease.to_payload(),
        )
    finally:
        runtime.shutdown(wait_for_runs=True)
        report.update(
            wall_seconds=monotonic() - started_at,
            completed_runs=len(observations),
            outcomes={
                status: sum(item.tape_status == status for item in observations)
                for status in ("PASSED", "ATTENTION_REQUIRED", "CANCELLED")
            },
            runs=[
                {
                    "run_id": item.run_id,
                    "scenario": item.scenario,
                    "tape_status": item.tape_status,
                    "project_source_sha256": item.source_sha256,
                    "source_references": item.source_references,
                    "artifacts": [artifact.model_dump(mode="json") for artifact in item.artifacts],
                    "missing_artifacts": item.missing_artifacts,
                }
                for item in observations
            ],
        )
        print(f"Storage campaign evidence: {_write_report(label, root, report)}", flush=True)


def test_storage_lifecycle_documented_commands_use_private_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documentation = (ROOT / "docs/development/LOCAL_STORAGE_LIFECYCLE.md").read_text()
    commands = [
        shlex.split(line, comments=True)[1:]
        for block in re.findall(r"```bash\n(.*?)```", documentation, re.DOTALL)
        for line in block.splitlines()
        if shlex.split(line, comments=True)[:2] == ["oh-goon", "storage"]
    ]
    statuses = [command for command in commands if command[:2] == ["storage", "status"]]
    dry = [
        command
        for command in commands
        if command[:2] == ["storage", "prune"] and "--yes" not in command
    ]
    confirmed = [
        command
        for command in commands
        if command[:2] == ["storage", "prune"] and "--yes" in command
    ]
    assert statuses and dry and confirmed
    root = tmp_path / "documented-storage"
    root.mkdir(mode=0o700)
    environment = _private_environment(root, monkeypatch)
    database_path = Path(environment["OH_GOON_DB"])
    repository, plan, _ready, _hold = _package(root)
    owner = repository_id(repository)
    runtime = ApplicationRuntime.build(standard_local=True)
    try:
        runtime.locations.add(repository.parent)
        target = _prepared(runtime, plan, {"mode": "same"})
        started = runtime.runs.start_prepared_plan(
            target, repository_ids={owner}, fleet_run_id="documented-storage-producer"
        )
        detail = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=60)
        assert detail.game_tape.status.value == "PASSED", detail
        recorded = _record(detail, "documented-storage-producer")
    finally:
        runtime.shutdown(wait_for_runs=True)
    layout = ApplicationStatePathLayout.from_database(database_path)
    scratch = layout.run_workspaces / "oh-goon-plan-document-proof"
    scratch.mkdir(parents=True)
    scratch_file = scratch / "owned-scratch"
    scratch_file.write_bytes(b"eligible execution-idle scratch")
    foreign = layout.run_workspaces / "foreign-private"
    foreign.mkdir()
    foreign_file = foreign / "sentinel"
    foreign_file.write_bytes(b"unrecognized workspace remains private")
    unknown = Path(environment["XDG_CACHE_HOME"]) / "oh-goon/document-proof-unclassified"
    unknown.mkdir(parents=True)
    unknown_file = unknown / "sentinel"
    unknown_file.write_bytes(b"unclassified native/private bytes")
    protected = (database_path, foreign_file, unknown_file)
    expected = {path: sha256_file_with_size(path) for path in (*protected, scratch_file)}
    before_paths = {path.relative_to(root) for path in root.rglob("*")}
    for command in statuses:
        output = _installed_cli(root, environment, *command)
        if "--json" in command:
            payload = json.loads(output)
            assert "application.database" in {item["storage_id"] for item in payload["storage"]}
            assert str(unknown) in {item["path"] for item in payload["unclassified"]}
        assert {path: sha256_file_with_size(path) for path in expected} == expected
        assert {path.relative_to(root) for path in root.rglob("*")} == before_paths
    for command in dry:
        _installed_cli(root, environment, *command, expected=2)
        assert {path: sha256_file_with_size(path) for path in expected} == expected
        assert {path.relative_to(root) for path in root.rglob("*")} == before_paths
    for command in confirmed:
        scratch.mkdir(parents=True, exist_ok=True)
        scratch_file.write_bytes(b"eligible execution-idle scratch")
        _installed_cli(root, environment, *command)
        assert not scratch.exists()
        assert database_path.is_file()
        assert {path: sha256_file_with_size(path) for path in (foreign_file, unknown_file)} == {
            path: expected[path] for path in (foreign_file, unknown_file)
        }
        reopened = ApplicationRuntime.build(standard_local=True)
        try:
            _assert_history(reopened, [recorded])
            _assert_retained(FleetContentStore(layout.content_store), [recorded], owner)
        finally:
            reopened.shutdown(wait_for_runs=True)


def test_bounded_real_run_storage_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _exercise_campaign(tmp_path, monkeypatch, count=7, iris=False)


@pytest.mark.daily
def test_one_hundred_real_run_storage_retention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _exercise_campaign(tmp_path, monkeypatch, count=100, iris=True)
