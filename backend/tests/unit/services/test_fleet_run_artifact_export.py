"""Artifact export binds recorded Run evidence to existing leased retained content."""

from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.game_tape_lifecycle import run_status
from oh_goon.core.models.fleet_plan import ResolvedCommandStep, ResolvedFleetPlan
from oh_goon.core.models.fleet_plan_evidence import (
    MissingPlanArtifact,
    PlanArtifactEvidence,
    PlanRetainedContentEvidence,
    PlanStepSettlementEvidence,
    PresentPlanArtifact,
)
from oh_goon.core.models.fleet_plan_execution_policy import CicdExecutionPolicy
from oh_goon.core.models.fleet_plan_step_primitives import PlanArtifactDeclaration
from oh_goon.core.models.game_tape import GameTapeStepExecution, GameTapeWhy
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import (
    CompletedFleetRun,
    FleetRunDetail,
    FleetRunFailure,
    FleetRunProgress,
    FleetRunState,
)
from oh_goon.core.models.run_admission import fleet_repository_scope
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.path_layouts import ContentStorePathLayout
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunStatus,
    GameTapeStatus,
    OperationAction,
)
from oh_goon.engine.command import (
    CommandOutputLimitExceeded,
    CommandSupervisorReceipt,
    CommandTimeoutExpired,
)
from oh_goon.integrations._fleet_plan_step_outcome import settle_step_execution
from oh_goon.integrations.exact_file_write import (
    ExactPublicationDurabilityError,
    publish_prepared_file,
)
from oh_goon.integrations.fleet_content_store import FleetContentStore
from oh_goon.integrations.fleet_plan_output_limit import bounded_execution_failure_outcome
from oh_goon.services.fleet_run_control_plane import FleetArtifactExport, FleetRunControlPlane
from tests.factories.capabilities import capability_snapshot
from tests.factories.fleet_plan_targets import structural_fleet_plan_target


@pytest.fixture
def export_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "original-project"})
    source = tmp_path / "model.json"
    payload = b'{"model": [0.1, 0.2, 0.3]}\n'
    source.write_bytes(payload)
    store = FleetContentStore(tmp_path / "store")
    stored = store.put_file(
        RepositoryScopedPath.create(
            owner_repository_id=repository.repository_id,
            root=tmp_path,
            path=source,
            label="export fixture",
        )
    )
    retained = PlanRetainedContentEvidence(
        kind="file",
        sha256=stored.identity.sha256,
        byte_count=stored.identity.byte_count,
        entry_count=stored.identity.entry_count,
        reference_id=stored.reference_id,
        owner_repository_id=repository.repository_id,
    )
    artifact = PresentPlanArtifact(
        path="ml-output/model.json",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        retained_object=retained,
    )
    plan = ResolvedFleetPlan.create(
        name="training",
        description="",
        execution=CicdExecutionPolicy(),
        steps=(
            ResolvedCommandStep(
                step_id="train",
                argv=("train",),
                timeout_seconds=30,
                artifacts=(PlanArtifactDeclaration(path=artifact.path, retention="content_store"),),
            ),
        ),
    )
    step = GameTapeStepExecution(
        step_execution_id="recorded-train",
        step_id="train",
        kind="command",
        order=1,
        status=GameTapeStatus.PASSED,
        exit_code=0,
        settlement=PlanStepSettlementEvidence(
            outcome="SUCCESS", cleanup="VERIFIED", reason="ACCEPTED_EXIT", observed_returncode=0
        ),
        artifacts=(artifact,),
        why=GameTapeWhy(declaration="train", reached=True, explanation="Declared training Step"),
    )
    recorded_repository = SimpleNamespace(repository=repository, steps=(step,))
    target = structural_fleet_plan_target(plan)
    now = utc_now()
    run = CompletedFleetRun(
        fleet_run_id="recorded-run",
        target=target,
        capability=capability_snapshot(OperationAction.EXECUTE_PLAN, target),
        repository_scope=fleet_repository_scope({repository.repository_id}),
        targets=(repository,),
        status=FleetRunStatus.COMPLETED,
        accepted_at=now,
        started_at=now,
        finished_at=now,
        progress=FleetRunProgress(total=1, succeeded=1, failed=0),
    )
    detail = cast(
        FleetRunDetail,
        SimpleNamespace(
            run=run,
            game_tape=SimpleNamespace(
                status=GameTapeStatus.PASSED, repositories=(recorded_repository,)
            ),
        ),
    )
    unused = cast(Any, None)
    owner = FleetRunControlPlane(
        unused, unused, unused, unused, unused, unused, content_store=store
    )
    read = Mock(return_value=detail)
    monkeypatch.setattr(owner, "get", read)
    return SimpleNamespace(
        owner=owner,
        read=read,
        store=store,
        stored=stored,
        artifact=artifact,
        retained=retained,
        detail=detail,
        step=step,
        recorded_repository=recorded_repository,
        destination=tmp_path / "exported model.json",
        source=source,
        payload=payload,
    )


def _export(case: SimpleNamespace, **options: Any) -> FleetArtifactExport:
    owner: FleetRunControlPlane = case.owner
    return owner.export_artifact(
        "recorded-run",
        step_id=options.pop("step_id", "train"),
        artifact_path=options.pop("artifact_path", case.artifact.path),
        destination=options.pop("destination", case.destination),
        **options,
    )


def _set_artifacts(case: SimpleNamespace, artifacts: tuple[PlanArtifactEvidence, ...]) -> None:
    case.step = case.step.model_copy(update={"artifacts": artifacts})
    case.recorded_repository.steps = (case.step,)


def test_export_uses_one_recorded_cut_after_original_source_disappears(export_case):
    export_case.source.unlink()
    result = _export(export_case)
    assert export_case.destination.read_bytes() == export_case.payload
    assert result.repository_id == export_case.recorded_repository.repository.repository_id
    assert result.sha256 == export_case.artifact.sha256
    assert result.byte_count == export_case.artifact.byte_count
    assert result.reference_id == export_case.retained.reference_id
    assert result.destination == export_case.destination
    export_case.read.assert_called_once_with("recorded-run")
    assert not list(export_case.destination.parent.glob(".oh-goon-export-*"))


@pytest.mark.parametrize(
    "status", [status for status in FleetRunStatus if status is not FleetRunStatus.QUEUED]
)
def test_export_preserves_completed_producers_unless_run_cancelled(
    export_case: SimpleNamespace, status: FleetRunStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = export_case
    fields = case.detail.run.model_dump(
        exclude={"status", "started_at", "finished_at", "cancel_requested_at", "failure"},
        exclude_computed_fields=True,
    )
    fields["status"] = status
    required, _ = status.receipt_timestamp_contract
    fields.update({name: utc_now() for name in required})
    if status is FleetRunStatus.FAILED:
        fields["failure"] = FleetRunFailure.from_exception(
            FleetPlanInvalid("Later Run work failed"), stage=FleetRunFailureStage.EXECUTION
        )
    case.detail.run = TypeAdapter(FleetRunState).validate_python(fields)
    case.detail.game_tape.status = run_status(case.detail.run)
    if status in {FleetRunStatus.RUNNING, FleetRunStatus.COMPLETED, FleetRunStatus.FAILED}:
        assert _export(case).destination.read_bytes() == case.payload
        return
    materialize = Mock(side_effect=AssertionError("Unsettled Run reached retained content"))
    monkeypatch.setattr(case.store, "materialize_file", materialize)
    with pytest.raises(FleetPlanInvalid, match="Run"):
        _export(case)
    materialize.assert_not_called()
    assert not case.destination.exists()
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))


@pytest.mark.parametrize("status", [GameTapeStatus.CANCELLED, GameTapeStatus.INTERRUPTED])
def test_export_refuses_cancelled_or_interrupted_run_projection(
    export_case: SimpleNamespace, status: GameTapeStatus, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = export_case
    fields = case.detail.run.model_dump(exclude_computed_fields=True)
    if status is GameTapeStatus.INTERRUPTED:
        fields["status"] = FleetRunStatus.FAILED
        fields["failure"] = FleetRunFailure.recovery("Interrupted before Run completion")
    else:
        fields["status"] = FleetRunStatus.CANCELLED
        fields["cancel_requested_at"] = utc_now()
    case.detail.run = TypeAdapter(FleetRunState).validate_python(fields)
    case.detail.game_tape.status = status
    materialize = Mock(side_effect=AssertionError("Interrupted Run reached retained content"))
    monkeypatch.setattr(case.store, "materialize_file", materialize)
    with pytest.raises(FleetPlanInvalid, match="Run"):
        _export(case)
    materialize.assert_not_called()
    assert not case.destination.exists()
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))


@pytest.mark.parametrize(
    "status,settlement",
    [
        (
            GameTapeStatus.CANCELLED,
            PlanStepSettlementEvidence(
                outcome="CANCELLED", cleanup="VERIFIED", reason="CALLER_CANCELLED"
            ),
        ),
        (GameTapeStatus.INTERRUPTED, PlanStepSettlementEvidence()),
        (GameTapeStatus.FAILED, PlanStepSettlementEvidence()),
        (
            GameTapeStatus.FAILED,
            PlanStepSettlementEvidence(
                outcome="INCOMPLETE", cleanup="UNVERIFIED", reason="CLEANUP_UNVERIFIED"
            ),
        ),
    ],
    ids=["cancelled", "interrupted", "cleanup-unavailable", "cleanup-unverified"],
)
def test_export_requires_selected_step_settlement_before_reading_retained_bytes(
    export_case: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    status: GameTapeStatus,
    settlement: PlanStepSettlementEvidence,
) -> None:
    case = export_case
    case.recorded_repository.steps = (
        case.step.model_copy(update={"status": status, "settlement": settlement}),
    )
    materialize = Mock(side_effect=AssertionError("Unsettled Step reached retained content"))
    monkeypatch.setattr(case.store, "materialize_file", materialize)
    with pytest.raises(FleetPlanInvalid, match="Step"):
        _export(case)
    materialize.assert_not_called()
    assert not case.destination.exists()
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))


@pytest.mark.parametrize("failure", ["timeout", "output_limit"])
@pytest.mark.parametrize("quiescent", [False, True])
def test_export_bounded_failure_artifacts_requires_verified_original_cleanup(
    export_case: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    failure: Literal["timeout", "output_limit"],
    quiescent: bool,
) -> None:
    case = export_case
    step = case.detail.run.target.plan.steps[0]
    supervisor = CommandSupervisorReceipt(
        process_group_cleanup_requested=True,
        cleanup_reason=failure,
        forced_termination=True,
        final_process_group_quiescent=quiescent,
    )
    error = (
        CommandTimeoutExpired(step.argv, 1, supervisor=supervisor)
        if failure == "timeout"
        else CommandOutputLimitExceeded(
            argv=step.argv,
            stream="stdout",
            limit_bytes=1,
            stdout="x",
            stderr="",
            supervisor=supervisor,
        )
    )
    outcome = bounded_execution_failure_outcome(step, error)
    settlement = settle_step_execution(step, outcome)
    assert settlement.outcome == ("TIMED_OUT" if failure == "timeout" else "OUTPUT_LIMITED")
    assert settlement.cleanup == ("VERIFIED" if quiescent else "UNVERIFIED")
    case.recorded_repository.steps = (
        case.step.model_copy(
            update={
                "status": GameTapeStatus.FAILED,
                "exit_code": outcome.returncode,
                "settlement": settlement,
            }
        ),
    )
    case.detail.run = case.detail.run.model_copy(
        update={"progress": FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1)}
    )
    case.detail.game_tape.status = GameTapeStatus.ATTENTION_REQUIRED
    if quiescent:
        exported = _export(case)
        assert exported.sha256 == case.artifact.sha256
        assert exported.destination.read_bytes() == case.payload
        return
    materialize = Mock(side_effect=AssertionError("Unsettled Step reached retained content"))
    monkeypatch.setattr(case.store, "materialize_file", materialize)
    with pytest.raises(FleetPlanInvalid, match="Step"):
        _export(case)
    materialize.assert_not_called()
    assert not case.destination.exists()
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))


@pytest.mark.parametrize("selected_failed", [False, True])
def test_export_preserves_settled_artifacts_when_run_has_failed_work(
    export_case: SimpleNamespace, selected_failed: bool
) -> None:
    case = export_case
    case.detail.run = case.detail.run.model_copy(
        update={"progress": FleetRunProgress(total=1, succeeded=0, failed=1, needs_attention=1)}
    )
    case.detail.game_tape.status = GameTapeStatus.ATTENTION_REQUIRED
    if selected_failed:
        case.recorded_repository.steps = (
            case.step.model_copy(
                update={
                    "status": GameTapeStatus.FAILED,
                    "exit_code": 1,
                    "settlement": PlanStepSettlementEvidence(
                        outcome="FAILED",
                        cleanup="VERIFIED",
                        reason="FAILED_EXIT",
                        observed_returncode=1,
                    ),
                }
            ),
        )
    assert _export(case).destination.read_bytes() == case.payload


@pytest.mark.parametrize(
    "defect",
    [
        "unknown-step",
        "unknown-path",
        "wrong-repository",
        "ambiguous",
        "missing",
        "not-retained",
        "owner",
        "kind",
        "hash",
        "size",
        "entries",
        "undeclared-retention",
    ],
)
def test_export_rejects_unproven_or_ambiguous_recorded_artifacts_without_publication(
    export_case, defect
):
    case = export_case
    options = {}
    if defect == "unknown-step":
        options["step_id"] = "not-recorded"
    elif defect == "unknown-path":
        options["artifact_path"] = "../model.json"
    elif defect == "wrong-repository":
        options["repository_id"] = "repo-" + "f" * 20
    elif defect == "ambiguous":
        case.detail.game_tape.repositories += (
            SimpleNamespace(
                repository=RepositoryIdentity.model_validate(
                    {"path": case.destination.parent / "peer"}
                ),
                steps=(case.step,),
            ),
        )
    elif defect == "missing":
        _set_artifacts(case, (MissingPlanArtifact(path=case.artifact.path),))
    elif defect == "not-retained":
        _set_artifacts(case, (case.artifact.model_copy(update={"retained_object": None}),))
    elif defect == "undeclared-retention":
        original = case.detail.run.target.plan
        step = original.steps[0].model_copy(
            update={"artifacts": (PlanArtifactDeclaration(path=case.artifact.path),)}
        )
        target = structural_fleet_plan_target(
            ResolvedFleetPlan.create(
                name=original.name,
                description=original.description,
                execution=original.execution,
                steps=(step,),
            )
        )
        case.detail.run = case.detail.run.model_copy(update={"target": target})
    else:
        update = {
            "owner": {"owner_repository_id": "repo-" + "f" * 20},
            "kind": {"kind": "tree"},
            "hash": {"sha256": "f" * 64},
            "size": {"byte_count": case.artifact.byte_count + 1},
            "entries": {"entry_count": 2},
        }[defect]
        reference = case.retained.model_copy(update=update)
        _set_artifacts(case, (case.artifact.model_copy(update={"retained_object": reference}),))
    with pytest.raises(FleetPlanInvalid):
        _export(case, **options)
    assert not case.destination.exists()
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))
    case.read.assert_called_once_with("recorded-run")


def test_repository_selection_resolves_shared_run_step_ambiguity(export_case):
    case = export_case
    case.detail.game_tape.repositories += (
        SimpleNamespace(
            repository=RepositoryIdentity.model_validate(
                {"path": case.destination.parent / "peer"}
            ),
            steps=(case.step,),
        ),
    )
    exported = _export(case, repository_id=case.retained.owner_repository_id)
    assert exported.repository_id == case.retained.owner_repository_id
    assert case.destination.read_bytes() == case.payload


@pytest.mark.parametrize("defect", ["tampered", "released", "wrong-reference", "size", "raw-sha"])
def test_export_refuses_unavailable_or_changed_retained_bytes_and_cleans_staging(
    export_case, defect
):
    case = export_case
    if defect == "tampered":
        case.stored.path.write_bytes(b"corrupt-model")
    elif defect == "released":
        case.store.release_reference(
            case.retained.reference_id, owner_repository_id=case.retained.owner_repository_id
        )
    elif defect == "wrong-reference":
        peer = case.store.put_file(
            RepositoryScopedPath.create(
                owner_repository_id="repo-" + "f" * 20,
                root=case.source.parent,
                path=case.source,
                label="peer reference",
            )
        )
        reference = case.retained.model_copy(update={"reference_id": peer.reference_id})
        _set_artifacts(case, (case.artifact.model_copy(update={"retained_object": reference}),))
    elif defect == "raw-sha":
        _set_artifacts(case, (case.artifact.model_copy(update={"sha256": "f" * 64}),))
    else:
        reference = case.retained.model_copy(update={"byte_count": case.artifact.byte_count + 1})
        _set_artifacts(
            case,
            (
                case.artifact.model_copy(
                    update={"byte_count": reference.byte_count, "retained_object": reference}
                ),
            ),
        )
    with pytest.raises(FleetPlanInvalid):
        _export(case)
    assert not case.destination.exists()
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))
    assert not list(ContentStorePathLayout.from_root(case.store.root).leases.glob("*/lease-*.json"))


@pytest.mark.parametrize("existing", ["file", "symlink", "parent-symlink"])
def test_export_never_overwrites_existing_files_or_follows_destination_aliases(
    export_case, existing
):
    case = export_case
    protected = case.destination.parent / "protected"
    protected.write_bytes(b"keep-me")
    if existing == "file":
        case.destination.write_bytes(b"keep-existing")
    elif existing == "symlink":
        case.destination.symlink_to(protected)
    else:
        alias = case.destination.parent / "alias"
        alias.symlink_to(case.destination.parent, target_is_directory=True)
        case.destination = alias / "unwritten"
    with pytest.raises(FleetPlanInvalid):
        _export(case)
    assert protected.read_bytes() == b"keep-me"
    if existing == "file":
        assert case.destination.read_bytes() == b"keep-existing"
    assert not list(protected.parent.glob(".oh-goon-export-*"))


def test_export_publication_race_preserves_the_other_writer_and_cleans_staging(
    export_case, monkeypatch
):
    case = export_case
    materialize = case.store.materialize_file

    def materialize_before_racing_writer(*args, **kwargs):
        result = materialize(*args, **kwargs)
        case.destination.write_bytes(b"concurrent-writer")
        return result

    monkeypatch.setattr(case.store, "materialize_file", materialize_before_racing_writer)
    with pytest.raises(FileExistsError):
        _export(case)
    assert case.destination.read_bytes() == b"concurrent-writer"
    assert not list(case.destination.parent.glob(".oh-goon-export-*"))


@pytest.fixture
def failed_export_cleanup(monkeypatch):
    import oh_goon.services.fleet_run_control_plane as module

    create_temporary = TemporaryDirectory
    owned = []

    def prepare_with_failing_cleanup(*args, **kwargs):
        temporary = create_temporary(*args, **kwargs)
        owned.append(temporary)
        return SimpleNamespace(
            name=temporary.name,
            cleanup=Mock(side_effect=OSError("synthetic staging cleanup failure")),
        )

    monkeypatch.setattr(module, "TemporaryDirectory", prepare_with_failing_cleanup)
    try:
        yield owned
    finally:
        for temporary in owned:
            temporary.cleanup()


def test_cleanup_failure_after_publication_preserves_exported_result(
    export_case, failed_export_cleanup
):
    exported = _export(export_case)
    assert exported.cleanup_status == "failed"
    assert exported.cleanup_failure == (
        "OSError: synthetic staging cleanup failure; private staging: "
        + failed_export_cleanup[0].name
    )
    assert exported.destination == export_case.destination
    assert export_case.destination.read_bytes() == export_case.payload
    assert Path(failed_export_cleanup[0].name).is_dir()


def test_cleanup_failure_before_publication_preserves_primary_error_and_reports_cleanup(
    export_case, failed_export_cleanup, monkeypatch
):
    primary = FleetPlanInvalid("Original retained-object identity failure")
    monkeypatch.setattr(export_case.store, "materialize_file", Mock(side_effect=primary))
    with pytest.raises(FleetPlanInvalid) as rejected:
        _export(export_case)
    assert rejected.value is primary
    assert str(rejected.value) == "Original retained-object identity failure"
    assert rejected.value.__notes__ == [
        "Artifact export staging cleanup failed: OSError: synthetic staging cleanup failure; "
        "private staging: " + failed_export_cleanup[0].name
    ]
    assert not export_case.destination.exists()


def test_postpublication_durability_failure_identifies_visible_destination(
    export_case, monkeypatch
):
    import oh_goon.services.fleet_run_control_plane as module

    publish = publish_prepared_file

    def publish_then_fail_durability(*args):
        publish(*args)
        raise ExactPublicationDurabilityError("Synthetic parent fsync failure")

    monkeypatch.setattr(module, "publish_prepared_file", publish_then_fail_durability)
    with pytest.raises(ExactPublicationDurabilityError) as rejected:
        _export(export_case)
    assert any(
        str(export_case.destination) in note and "published" in note and "unverified" in note
        for note in rejected.value.__notes__
    )
    assert export_case.destination.read_bytes() == export_case.payload
    assert not list(export_case.destination.parent.glob(".oh-goon-export-*"))
