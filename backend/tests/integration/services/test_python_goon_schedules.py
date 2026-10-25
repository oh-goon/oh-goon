"""Frozen Python source survives durable replay and scheduled native execution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest
import yaml

from oh_goon.application import ApplicationRuntime
from oh_goon.core.errors import (
    FleetPlanInvalid,
    FleetPlanSourceStale,
    JournalConflict,
    RunNotFound,
    ScheduleNotFound,
    StateConflict,
)
from oh_goon.core.models.caller_input_file import CallerInputFileIdentity
from oh_goon.core.models.diagnostic import DiagnosticEvent
from oh_goon.core.models.operation import FleetPlanOperationEvidence
from oh_goon.core.models.run import FleetRunState
from oh_goon.core.models.target import FleetPlanTarget, GoonProjectDeliveryReference
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.product_identity import (
    repository_id,
    schedule_repository_fleet_run_id,
)
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.integrations import fleet_plan_step_execution
from oh_goon.integrations.fleet_content_store import FleetContentObject
from oh_goon.runtime.diagnostics import JsonlDiagnosticSink
from oh_goon.services import fleet_run_control_plane
from oh_goon.services.run_configuration import load_run_configuration
from tests.factories import python_goon_projects
from tests.factories.python_goon_projects import SharedPackageScheduleOptions

execution = python_goon_projects.execution
shared_execution = python_goon_projects.shared_execution

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


def test_wrong_retained_package_archive_refuses_before_admission(
    execution: tuple[ApplicationRuntime, Path, Path],
) -> None:
    runtime, repository, plan = execution
    runtime.plans.lock(plan)
    owner = repository_id(repository)
    original = runtime.plans.schedule(
        plan, schedule_id="retained-package-baseline", repository_ids={owner}, interval_minutes=60
    ).target
    baseline = runtime.runs.start_prepared_plan(
        original, repository_ids={owner}, fleet_run_id="valid-retained-package"
    )
    completed = runtime.runs.wait_for_terminal(baseline.fleet_run_id, timeout=30)
    assert completed.game_tape.status.value == "PASSED"
    source = original.plan.steps[0]
    assert source.kind == "command" and source.project_source is not None
    store = runtime.plans._content_store
    assert store is not None
    wrong = store.put_frozen_bytes(b"different captured package", owner_repository_id=owner)
    mismatched = original.model_copy(
        update={
            "project_deliveries": (
                GoonProjectDeliveryReference(
                    project_sha256=source.project_source.content_sha256,
                    owner_repository_id=owner,
                    reference_id=wrong.reference_id,
                ),
            )
        }
    )
    try:
        with pytest.raises(FleetPlanInvalid, match="expected captured source identity"):
            runtime.runs.start_prepared_plan(
                mismatched, repository_ids={owner}, fleet_run_id="wrong-retained-package"
            )
        with pytest.raises(RunNotFound):
            runtime.runs.get("wrong-retained-package")
    finally:
        store.release_reference(wrong.reference_id, owner_repository_id=owner)


@pytest.mark.parametrize("phase", ("preparation", "command"))
@pytest.mark.parametrize(
    "quiescent", (True, False, None), ids=("settled", "unsettled", "unavailable")
)
def test_native_package_lifecycle_retains_scratch_only_when_terminal_supervision_is_unsettled(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    quiescent: bool | None,
) -> None:
    """Inject terminal receipt loss after real uv/application work, without claiming live survivors."""
    runtime, repository, plan = execution
    runtime.plans.lock(plan)
    native_runner = fleet_plan_step_execution._direct_step_command_runner
    inputs: list[tuple[Path, Path, Path]] = []
    injected = False

    def observe_native(*argv: str, **kwargs: Any) -> CommandResult:
        nonlocal injected
        result = native_runner(*argv, **kwargs)
        environment = Path(kwargs["env"]["UV_PROJECT_ENVIRONMENT"])
        package_source = Path(argv[argv.index("--project") + 1])
        inputs.append((kwargs["cwd"], environment, package_source))
        selected = argv[:2] == ("uv", "sync" if phase == "preparation" else "run")
        if selected and not injected:
            injected = True
            if quiescent is None:
                return replace(result, supervisor=cast(CommandSupervisorReceipt, None))
            return replace(
                result,
                supervisor=replace(result.supervisor, final_process_group_quiescent=quiescent),
            )
        return result

    monkeypatch.setattr(fleet_plan_step_execution, "_direct_step_command_runner", observe_native)
    started = runtime.plans.run(plan, repository_ids={repository_id(repository)})
    completed = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
    assert injected and inputs
    operation = completed.operations[0]
    assert isinstance(operation.evidence, FleetPlanOperationEvidence)
    first = operation.evidence.plan.steps[0]
    if quiescent is True:
        assert completed.game_tape.status.value == "PASSED"
        assert operation.resource_settlement == "SETTLED"
        artifact = operation.evidence.plan.steps[-1].artifacts[0]
        assert artifact.status == "PRESENT"
        expected = (json.dumps({"total_order_value": "1236.70"}) + "\n").encode()
        assert artifact.sha256 == hashlib.sha256(expected).hexdigest()
        for workspace, environment, package_source in inputs:
            assert not workspace.exists()
            assert not environment.exists()
            assert not package_source.exists()
    else:
        assert completed.game_tape.status.value != "PASSED"
        assert operation.resource_settlement == "UNRESOLVED"
        assert first.settlement.outcome == "INCOMPLETE"
        assert first.settlement.cleanup == ("UNVERIFIED" if quiescent is False else "UNAVAILABLE")
        assert all(step.status == "SKIPPED" for step in operation.evidence.plan.steps[1:])
        for workspace, environment, package_source in inputs:
            assert workspace.is_dir()
            assert environment.is_dir()
            assert package_source.is_dir()
    assert not (repository / ".venv").exists()
    assert not (repository / ".showcase").exists()


@pytest.mark.parametrize(
    ("phase", "quiescent", "diagnostic_failure"),
    (
        ("preparation", False, False),
        ("preparation", None, False),
        ("command", False, False),
        ("command", None, False),
        ("preparation", False, True),
    ),
)
def test_cancellation_after_unsettled_native_receipt_preserves_package_resources(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    quiescent: bool | None,
    diagnostic_failure: bool,
) -> None:
    runtime, repository, plan = execution
    runtime.plans.lock(plan)
    native_runner = fleet_plan_step_execution._direct_step_command_runner
    roots: list[tuple[Path, Path, Path]] = []
    failed_diagnostics: list[DiagnosticEvent] = []
    if diagnostic_failure:
        original_write = JsonlDiagnosticSink.write

        def refuse_diagnostic(sink: JsonlDiagnosticSink, event: DiagnosticEvent) -> None:
            if event.operation == "retain_incomplete_package_scratch":
                failed_diagnostics.append(event)
                raise OSError("injected bounded diagnostic storage failure")
            original_write(sink, event)

        monkeypatch.setattr(JsonlDiagnosticSink, "write", refuse_diagnostic)

    def cancel_after_native(*argv: str, **kwargs: Any) -> CommandResult:
        result = native_runner(*argv, **kwargs)
        selected = argv[:2] == ("uv", "sync" if phase == "preparation" else "run")
        if selected:
            roots.append(
                (
                    kwargs["cwd"],
                    Path(kwargs["env"]["UV_PROJECT_ENVIRONMENT"]),
                    Path(argv[argv.index("--project") + 1]),
                )
            )
            runtime.runs.cancel("cancel-after-unsettled-package")
            return replace(
                result,
                supervisor=(
                    replace(result.supervisor, final_process_group_quiescent=False)
                    if quiescent is False
                    else cast(CommandSupervisorReceipt, None)
                ),
            )
        return result

    monkeypatch.setattr(
        fleet_plan_step_execution, "_direct_step_command_runner", cancel_after_native
    )
    started = runtime.plans.run(
        plan,
        repository_ids={repository_id(repository)},
        fleet_run_id="cancel-after-unsettled-package",
    )
    cancelled = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
    assert cancelled.run.status.value == "CANCELLED"
    assert cancelled.game_tape.status.value != "PASSED"
    first_step = cancelled.game_tape.repositories[0].steps[0]
    assert first_step.settlement.outcome == "INCOMPLETE"
    assert first_step.settlement.cleanup == "UNAVAILABLE"
    assert roots
    diagnostic_path = ApplicationStatePathLayout.from_database(
        Path(os.environ["OH_GOON_DB"])
    ).diagnostics_log
    if diagnostic_failure:
        retained = [event.model_dump() for event in failed_diagnostics]
    else:
        diagnostics = [json.loads(line) for line in diagnostic_path.read_text().splitlines()]
        retained = [
            event
            for event in diagnostics
            if event.get("fleet_run_id") == started.fleet_run_id
            and event["operation"] == "retain_incomplete_package_scratch"
        ]
    assert len(retained) == 1
    assert retained[0]["repository_id"] == repository_id(repository)
    for workspace, environment, source in roots:
        assert workspace.is_dir()
        assert environment.is_dir()
        assert source.is_dir()
        assert str(source.parents[2]) in retained[0]["message"]
    assert not (repository / ".venv").exists()
    assert not (repository / ".showcase").exists()


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


def test_shared_schedule_failed_publication_releases_new_package_references(
    shared_execution: tuple[ApplicationRuntime, Path, Path, SharedPackageScheduleOptions],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, repository, _, options = shared_execution
    baseline = runtime.shared_plan_schedules.create("shared-baseline", **options)
    assert baseline.target.project_deliveries
    store = runtime.plans._content_store
    assert store is not None
    references: list[FleetContentObject] = []
    put = store.put_frozen_bytes

    def observe_put(data: bytes, *, owner_repository_id: str) -> FleetContentObject:
        stored = put(data, owner_repository_id=owner_repository_id)
        references.append(stored)
        return stored

    def fail_publication(*args: object, **kwargs: object) -> None:
        raise StateConflict("injected Shared Schedule persistence refusal")

    monkeypatch.setattr(store, "put_frozen_bytes", observe_put)
    monkeypatch.setattr(runtime.schedules, "create_prepared", fail_publication)
    with pytest.raises(StateConflict, match="persistence refusal"):
        runtime.shared_plan_schedules.create("shared-refused", **options)
    with pytest.raises(ScheduleNotFound):
        runtime.schedules.definition("shared-refused")
    assert references
    for reference in references:
        with pytest.raises(FleetPlanInvalid, match="unavailable"):
            store.get(reference.reference_id, owner_repository_id=repository_id(repository))
    for retained in baseline.target.project_deliveries:
        assert store.get(
            retained.reference_id, owner_repository_id=repository_id(repository)
        ).path.is_file()


def test_shared_schedule_repository_replacement_refuses_before_package_store_effects(
    shared_execution: tuple[ApplicationRuntime, Path, Path, SharedPackageScheduleOptions],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, repository, _, options = shared_execution
    baseline = runtime.shared_plan_schedules.create("shared-baseline", **options)
    assert baseline.target.project_deliveries
    prepare = runtime.plans.prepare_reviewed_execution_target
    store = runtime.plans._content_store
    assert store is not None
    calls: list[str] = []
    put = store.put_frozen_bytes

    def observe_put(data: bytes, *, owner_repository_id: str) -> FleetContentObject:
        calls.append(owner_repository_id)
        return put(data, owner_repository_id=owner_repository_id)

    def replace_after_preparation(*args: Any, **kwargs: Any) -> FleetPlanTarget:
        target = prepare(*args, **kwargs)
        repository.rename(repository.with_name("retired-target"))
        repository.mkdir()
        subprocess.run(["git", "init", "-q", str(repository)], check=True)
        return target

    monkeypatch.setattr(
        runtime.plans, "prepare_reviewed_execution_target", replace_after_preparation
    )
    monkeypatch.setattr(store, "put_frozen_bytes", observe_put)
    with pytest.raises(FleetPlanSourceStale, match="changed during preparation"):
        runtime.shared_plan_schedules.create("shared-stale", **options)
    assert calls == []
    with pytest.raises(ScheduleNotFound):
        runtime.schedules.definition("shared-stale")


@pytest.mark.parametrize(
    "quiescent", (True, False, None), ids=("settled", "unsettled", "unavailable")
)
def test_target_step_consuming_package_output_preserves_unsettled_run_resources(
    execution: tuple[ApplicationRuntime, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    quiescent: bool | None,
) -> None:
    runtime, repository, plan = execution
    package = plan.parent / "goons/general/orders"
    implementation = package / "src/orders_pipeline/cli.py"
    implementation.write_text(
        implementation.read_text()
        + """

def scratch_file():
    from pathlib import Path
    output = Path.cwd() / "held.txt"
    output.write_text("held package output")
    print(output)
"""
    )
    pyproject = package / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            "orders-pipeline = ",
            'orders-location = "orders_pipeline.cli:scratch_file"\norders-pipeline = ',
        )
    )
    (package / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "runtime": "python",
                "steps": [
                    {
                        "id": "where",
                        "run": "orders-location",
                        "outputs": {
                            "FILE": {"stream": "stdout", "max_bytes": 4096},
                        },
                    }
                ],
            }
        )
    )
    consumer = plan.parent / "goons/general/consume"
    consumer.mkdir()
    (consumer / "README.md").write_text("# general/consume\n\nConsume prior package output.\n")
    (consumer / "goon.yml").write_text(
        yaml.safe_dump(
            {
                "inputs": {"file": {"type": "string"}},
                "steps": [{"run": "git hash-object ${{ inputs.file }}"}],
            }
        )
    )
    plan.write_text(
        yaml.safe_dump(
            {
                "goons": [
                    {"import": "general/orders", "from": "goons"},
                    {
                        "import": "general/consume",
                        "from": "goons",
                        "with": {
                            "file": "${{ steps.general-orders.where.outputs.FILE }}",
                        },
                    },
                ]
            }
        )
    )
    runtime.plans.lock(plan)
    native = fleet_plan_step_execution._direct_step_command_runner
    consumed: list[Path] = []

    def observe_consumer(*argv: str, **kwargs: Any) -> CommandResult:
        result = native(*argv, **kwargs)
        if argv[:2] == ("git", "hash-object"):
            assert result.returncode == 0
            assert result.supervisor.final_process_group_quiescent is True
            consumed.append(Path(argv[2]))
            if quiescent is not True:
                runtime.runs.cancel("mixed-package-consumer")
                return replace(
                    result,
                    supervisor=(
                        replace(result.supervisor, final_process_group_quiescent=False)
                        if quiescent is False
                        else cast(CommandSupervisorReceipt, None)
                    ),
                )
        return result

    monkeypatch.setattr(fleet_plan_step_execution, "_direct_step_command_runner", observe_consumer)
    started = runtime.plans.run(
        plan, repository_ids={repository_id(repository)}, fleet_run_id="mixed-package-consumer"
    )
    completed = runtime.runs.wait_for_terminal(started.fleet_run_id, timeout=30)
    assert consumed
    for output in consumed:
        if quiescent is True:
            assert completed.game_tape.status.value == "PASSED"
            assert not output.exists()
        else:
            assert completed.run.status.value == "CANCELLED"
            assert completed.game_tape.repositories[0].steps[-1].settlement.outcome == "INCOMPLETE"
            assert output.read_text() == "held package output"
    assert not (repository / "held.txt").exists()
    assert not (package / "held.txt").exists()
