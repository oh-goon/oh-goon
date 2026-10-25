"""Frozen Python source survives durable replay and scheduled native execution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TypedDict

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import FleetPlanInvalid, JournalConflict, StateConflict
from oh_goon.core.models.caller_input_file import CallerInputFileIdentity
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.target import FleetPlanTarget
from oh_goon.core.product_identity import repository_id, schedule_repository_fleet_run_id
from oh_goon.integrations.fleet_content_store import FleetContentObject
from oh_goon.runtime.league_folders import LeagueFolderStore
from oh_goon.services import fleet_run_control_plane
from oh_goon.services.run_configuration import load_run_configuration

SHOWCASE = Path(__file__).resolve().parents[4] / "examples/airflow-taskflow-showcase"
pytestmark = pytest.mark.skipif(
    os.name != "posix" or shutil.which("uv") is None, reason="native POSIX uv required"
)


class _RunOptions(TypedDict):
    repository_ids: set[str]
    fleet_run_id: str


class _ScheduleOptions(TypedDict):
    schedule_id: str
    repository_ids: set[str]
    interval_minutes: int
    caller_input_file: CallerInputFileIdentity
    external_artifacts: tuple[tuple[str, bytes], ...]


@pytest.fixture
def execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ApplicationRuntime, Path, Path]]:
    repository = tmp_path / "repositories/target"
    repository.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    monkeypatch.setenv("OH_GOON_DB", str(tmp_path / "runtime.sqlite"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    LeagueFolderStore().replace((repository,))
    workflow = tmp_path / "workflow"
    package = workflow / "goons/general/orders"
    shutil.copytree(
        SHOWCASE,
        package,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache", ".showcase"),
    )
    (package / "README.md").write_text("# general/orders\n\nRun a complete Python package.\n")
    plan = workflow / "plan.yml"
    plan.write_text("goons:\n- import: general/orders\n  from: goons\n")
    runtime = ApplicationRuntime.build(standard_local=True)
    runtime.locations.add(repository.parent)
    try:
        yield runtime, repository, plan
    finally:
        runtime.shutdown(wait_for_runs=True)


def test_package_run_replay_reuses_original_delivery_and_rejects_changed_authority(
    execution: tuple[ApplicationRuntime, Path, Path],
) -> None:
    runtime, repository, plan = execution
    runtime.plans.lock(plan)
    target = runtime.plans.prepare_execution_target(plan)
    options: _RunOptions = {
        "repository_ids": {repository_id(repository)},
        "fleet_run_id": "package-replay",
    }
    original = runtime.runs.start_prepared_plan(target, **options)
    completed = runtime.runs.wait_for_terminal(original.fleet_run_id, timeout=30)
    assert completed.game_tape.status.value == "PASSED"
    assert isinstance(completed.run.target, FleetPlanTarget)
    retained = completed.run.target
    assert retained.project_deliveries
    # The initial request carries private captured bytes, while durable readback
    # carries retained references. Both retries bind the same original Run.
    assert runtime.runs.start_prepared_plan(target, **options) == completed.run
    decoded = FleetPlanTarget.model_validate_json(retained.model_dump_json())
    assert runtime.runs.start_prepared_plan(decoded, **options) == completed.run
    forged_reference = retained.project_deliveries[0].model_copy(
        update={"reference_id": "ref-" + "0" * 32}
    )
    with pytest.raises(JournalConflict, match="another operation intent"):
        runtime.runs.start_prepared_plan(
            target.model_copy(update={"project_deliveries": (forged_reference,)}), **options
        )
    with pytest.raises(JournalConflict, match="another operation intent"):
        runtime.runs.start_prepared_plan(
            target.model_copy(
                update={"source": target.source.model_copy(update={"sha256": "0" * 64})}
            ),
            **options,
        )


def test_schedule_keeps_captured_package_after_source_removal_and_serialization(
    execution: tuple[ApplicationRuntime, Path, Path],
) -> None:
    runtime, repository, plan = execution
    selection = plan.with_name("run.yml")
    selection.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "source": {"kind": "file-plan", "path": "plan.yml"},
                "target": {"repository_id": repository_id(repository)},
            }
        )
    )
    captured = load_run_configuration(selection)
    runtime.plans.lock(
        plan,
        caller_input_file=captured.authority(),
        external_artifacts=captured.external_artifacts(),
    )
    options: _ScheduleOptions = {
        "schedule_id": "package-schedule",
        "repository_ids": {repository_id(repository)},
        "interval_minutes": 60,
        "caller_input_file": captured.authority(),
        "external_artifacts": captured.external_artifacts(),
    }
    saved = runtime.plans.schedule(plan, **options)
    assert saved.target.project_deliveries
    assert saved.target.caller_input_file is None
    assert saved.target.scheduled_caller_file is not None
    assert saved.target.plan._project_archives == {}
    assert runtime.plans.schedule(plan, **options) == saved
    assert runtime.plans.reschedule(plan, **options) == saved
    # Remove all authoring inputs, including the caller file and native lock.
    # A separately decoded Schedule still executes its exact retained package.
    shutil.rmtree(plan.parent)
    decoded = runtime.schedules.definition(saved.schedule_id)
    assert decoded == saved
    occurrence = runtime.schedules.start(
        saved.schedule_id,
        "frozen-package-occurrence",
        expected_generation_id=decoded.generation_id,
        expected_record_version=decoded.record_version,
    )
    child_id = schedule_repository_fleet_run_id(
        occurrence.schedule_run_id, repository_id(repository)
    )
    completed = runtime.runs.wait_for_terminal(child_id, timeout=30)
    assert completed.game_tape.status.value == "PASSED"
    evidence = completed.operations[0].evidence
    assert isinstance(evidence, FleetPlanOperationEvidence)
    report = evidence.plan
    assert report is not None
    artifact = report.steps[-1].artifacts[0]
    expected = (json.dumps({"total_order_value": "1236.70"}) + "\n").encode()
    assert artifact.status == "PRESENT"
    assert artifact.sha256 == hashlib.sha256(expected).hexdigest()
    runtime.schedules.tick()
    observed = runtime.schedules.start(
        saved.schedule_id,
        occurrence.schedule_run_id,
        expected_generation_id=decoded.generation_id,
        expected_record_version=decoded.record_version,
    )
    assert observed.successful == 1 and observed.not_started == 0
    assert not (repository / ".showcase").exists()


@pytest.mark.parametrize(
    "lost_ack", (False, True), ids=("competing-admission", "committed-ack-loss")
)
def test_admission_conflict_preserves_committed_source_and_releases_only_loser_references(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    lost_ack: bool,
) -> None:
    runtime, repository, plan = execution
    runtime.plans.lock(plan)
    target = runtime.plans.prepare_execution_target(plan)
    options: _RunOptions = {
        "repository_ids": {repository_id(repository)},
        "fleet_run_id": "source-convergence",
    }
    if not lost_ack:
        original = runtime.runs.start_prepared_plan(target, **options)
        runtime.runs.wait_for_terminal(original.fleet_run_id, timeout=30)
        original_matching = fleet_run_control_plane._matching_existing_run
        observations = 0

        def observe_competing_run(*args: Any, **kwargs: Any) -> FleetRunState | None:
            nonlocal observations
            observations += 1
            return None if observations == 1 else original_matching(*args, **kwargs)

        monkeypatch.setattr(
            fleet_run_control_plane, "_matching_existing_run", observe_competing_run
        )
    else:
        admit = runtime.runs._lifecycle.admit

        def lose_committed_ack(*args: Any, **kwargs: Any) -> Any:
            ticket, state = admit(*args, **kwargs)
            runtime.runs._submit(state.fleet_run_id, ticket)
            raise JournalConflict("injected lost admission ACK after original dispatch")

        monkeypatch.setattr(runtime.runs._lifecycle, "admit", lose_committed_ack)

    store = runtime.plans._content_store
    assert store is not None
    references: list[FleetContentObject] = []
    put = store.put_frozen_bytes

    def observe_put(data: bytes, *, owner_repository_id: str) -> FleetContentObject:
        stored = put(data, owner_repository_id=owner_repository_id)
        references.append(stored)
        return stored

    monkeypatch.setattr(store, "put_frozen_bytes", observe_put)
    started = runtime.runs.start_prepared_plan(target, **options)
    completed = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
    assert completed.game_tape.status.value == "PASSED"
    assert isinstance(completed.run.target, FleetPlanTarget)
    committed = completed.run.target.project_deliveries
    assert references
    for reference in committed:
        assert store.get(
            reference.reference_id, owner_repository_id=repository_id(repository)
        ).path.is_file()
    for created in references:
        if lost_ack:
            assert any(item.reference_id == created.reference_id for item in committed)
        else:
            with pytest.raises(FleetPlanInvalid, match="unavailable"):
                store.get(created.reference_id, owner_repository_id=repository_id(repository))


def test_failed_schedule_publication_releases_its_unadmitted_package_source(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, repository, plan = execution
    runtime.plans.lock(plan)
    store = runtime.plans._content_store
    assert store is not None
    references: list[FleetContentObject] = []
    original_put = store.put_frozen_bytes

    def observe_put(data: bytes, *, owner_repository_id: str) -> FleetContentObject:
        stored = original_put(data, owner_repository_id=owner_repository_id)
        references.append(stored)
        return stored

    def fail_publication(*args: object, **kwargs: object) -> None:
        raise StateConflict("injected Schedule persistence refusal")

    monkeypatch.setattr(store, "put_frozen_bytes", observe_put)
    monkeypatch.setattr(runtime.schedules, "create_prepared", fail_publication)
    with pytest.raises(StateConflict, match="persistence refusal"):
        runtime.plans.schedule(
            plan,
            schedule_id="refused",
            repository_ids={repository_id(repository)},
            interval_minutes=60,
        )
    assert references
    for reference in references:
        with pytest.raises(FleetPlanInvalid, match="unavailable"):
            store.get(reference.reference_id, owner_repository_id=repository_id(repository))
