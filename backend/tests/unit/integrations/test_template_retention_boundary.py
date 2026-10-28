"""M136: prove artifact evidence and strict content retention share one owner."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    PLAN_CONTRACT_RETURN_CODE,
    ResolvedCommandStep,
    ResolvedFleetPlan,
)
from oh_goon.core.models.fleet_plan_evidence import MissingPlanArtifact, PresentPlanArtifact
from oh_goon.core.models.fleet_plan_execution_policy import VerificationExecutionPolicy
from oh_goon.core.models.fleet_plan_resources import CapturedProjectSource
from oh_goon.core.models.fleet_plan_step_primitives import PlanArtifactDeclaration
from oh_goon.core.models.git_sources import LocalApplicationSource
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.path_authority import RepositoryScopedPath
from oh_goon.core.path_layouts import AttemptPathLayout, RunPathLayout
from oh_goon.engine.command import CommandResult, CommandSupervisorReceipt
from oh_goon.engine.errors import OperationCancelledError
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, from_command
from oh_goon.integrations._fleet_plan_step_sequence import FleetPlanStepSequence
from oh_goon.integrations.fleet_content_store import FleetContentObject, FleetContentStore
from oh_goon.integrations.fleet_plan_run_execution import FleetPlanRunExecutor
from oh_goon.integrations.fleet_plan_step_execution import StepRuntime
from oh_goon.integrations.fleet_plan_step_publication import (
    DirectArtifactRetention,
    collect_direct_artifacts,
    collect_strict_artifacts,
)

OWNER = "repo-aaaaaaaaaaaaaaaaaaaa"


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    return repository


def _prepared(store: object | None):
    def owned(path: Path, *, owned_root: Path, label: str) -> RepositoryScopedPath:
        return RepositoryScopedPath.create(
            owner_repository_id=OWNER,
            root=owned_root,
            path=path,
            label=label,
        )

    return SimpleNamespace(content_store=store, retained_content_path=owned)


def test_m136_default_evidence_reports_identity_without_retaining_bytes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "proof.json").write_bytes(b'{"success":true}\n')
    declarations = (PlanArtifactDeclaration(path="proof.json"),)
    assert declarations[0].retention == "evidence"
    (result,) = collect_direct_artifacts(repository, declarations)
    assert isinstance(result, PresentPlanArtifact)
    assert result.sha256 == hashlib.sha256(b'{"success":true}\n').hexdigest()
    assert result.byte_count == len(b'{"success":true}\n')
    assert result.retained_object is None


def test_m136_missing_file_is_missing_evidence_not_retained_content(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (result,) = collect_strict_artifacts(
        repository,
        (PlanArtifactDeclaration(path="missing.zip", retention="content_store"),),
        _prepared(None),
    )
    assert isinstance(result, MissingPlanArtifact)
    assert result.path == "missing.zip"


def test_m136_strict_publisher_sends_owned_file_and_reports_retained_evidence(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    payload = b"identical-release-bytes\n"
    (repository / "release.zip").write_bytes(payload)

    # Publication boundary test double; actual content-store digest, staging,
    # references and retention are separately exercised by existing store tests.
    class CapturingStore:
        published: list[Path]

        def __init__(self) -> None:
            self.published = []

        def put_file(self, scoped: RepositoryScopedPath):
            scoped.prove_owned()
            assert scoped.owner_repository_id == OWNER
            assert scoped.root == repository
            assert scoped.path.read_bytes() == payload
            self.published.append(scoped.path)
            identity = SimpleNamespace(
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_count=len(payload),
                entry_count=1,
            )
            return SimpleNamespace(
                reference_id="ref-" + "a" * 32,
                owner_repository_id=OWNER,
                kind="file",
                identity=identity,
                path=scoped.path,
            )

    store = CapturingStore()
    declaration = PlanArtifactDeclaration(path="release.zip", retention="content_store")
    (result,) = collect_strict_artifacts(repository, (declaration,), _prepared(store))
    assert isinstance(result, PresentPlanArtifact)
    assert result.retained_object is not None
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert result.retained_object.sha256 == result.sha256
    assert result.retained_object.byte_count == result.byte_count == len(payload)
    assert result.retained_object.owner_repository_id == OWNER
    assert store.published == [repository / "release.zip"]


@pytest.mark.parametrize("create_file", [False, True])
def test_m136_direct_mode_never_downgrades_content_store_to_evidence(
    tmp_path: Path, create_file: bool
) -> None:
    repository = _repository(tmp_path)
    if create_file:
        (repository / "release.zip").write_bytes(b"bytes")
    with pytest.raises(FleetPlanInvalid, match="Direct Fleet execution cannot publish"):
        collect_direct_artifacts(
            repository, (PlanArtifactDeclaration(path="release.zip", retention="content_store"),)
        )


def test_m136_strict_mode_refuses_missing_store_before_claiming_retention(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "release.zip").write_bytes(b"bytes")
    with pytest.raises(FleetPlanInvalid, match="without a strict run store"):
        collect_strict_artifacts(
            repository,
            (PlanArtifactDeclaration(path="release.zip", retention="content_store"),),
            _prepared(None),
        )


def test_m136_invalid_retention_cannot_enter_resolved_artifact_contract() -> None:
    with pytest.raises(ValueError):
        PlanArtifactDeclaration(path="a.zip", retention="forever")  # type: ignore[arg-type]
    evidence = PlanArtifactDeclaration(path="a.zip", retention="evidence")
    retained = PlanArtifactDeclaration(path="a.zip", retention="content_store")
    assert evidence.model_dump(mode="json") != retained.model_dump(mode="json")


def _direct_scope(tmp_path: Path) -> tuple[Path, DirectArtifactRetention]:
    layout = RunPathLayout.from_root(tmp_path / "run")
    workspace = AttemptPathLayout.from_root(layout.attempt_root("train", 1)).workspace
    workspace.mkdir(parents=True)
    return workspace, DirectArtifactRetention(
        workspace=RepositoryScopedPath.create(
            owner_repository_id=OWNER,
            root=layout.root,
            path=workspace,
            label="Test allocated Step workspace",
        ),
        content_store=FleetContentStore(tmp_path / "content-store"),
    )


def test_trusted_local_retention_keeps_requested_bytes_after_workspace_disposal(
    tmp_path: Path,
) -> None:
    workspace, retention = _direct_scope(tmp_path)
    payload = b"actual model parameters\n"
    model = workspace / "model.json"
    model.write_bytes(payload)
    artifact, missing = collect_direct_artifacts(
        workspace,
        (
            PlanArtifactDeclaration(path="model.json", retention="content_store"),
            PlanArtifactDeclaration(path="missing.json", retention="content_store"),
        ),
        retention=retention,
    )
    assert isinstance(artifact, PresentPlanArtifact)
    assert isinstance(missing, MissingPlanArtifact)
    assert artifact.retained_object is not None
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.byte_count == artifact.retained_object.byte_count == len(payload)
    model.unlink()
    stored = retention.content_store.get(
        artifact.retained_object.reference_id, owner_repository_id=OWNER, expected_kind="file"
    )
    assert stored.identity.sha256 == artifact.retained_object.sha256
    assert stored.path.read_bytes() == payload


@pytest.mark.parametrize(
    "boundary", ["other_workspace", "workspace_symlink", "artifact_symlink", "hardlink"]
)
def test_trusted_local_retention_refuses_foreign_filesystem_edges(
    tmp_path: Path, boundary: str
) -> None:
    workspace, retention = _direct_scope(tmp_path)
    model = workspace / "model.json"
    model.write_bytes(b"owned model")
    declaration = (PlanArtifactDeclaration(path="model.json", retention="content_store"),)
    (baseline,) = collect_direct_artifacts(workspace, declaration, retention=retention)
    assert isinstance(baseline, PresentPlanArtifact) and baseline.retained_object is not None
    retention.release(baseline.retained_object.reference_id)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "model.json").write_bytes(b"foreign model")
    if boundary == "other_workspace":
        selected = foreign
    elif boundary == "workspace_symlink":
        workspace.rename(workspace.with_name("original-workspace"))
        workspace.symlink_to(foreign, target_is_directory=True)
        selected = workspace
    else:
        model.unlink()
        if boundary == "artifact_symlink":
            model.symlink_to(foreign / "model.json")
        else:
            os.link(foreign / "model.json", model)
        selected = workspace
    with pytest.raises(FleetPlanInvalid):
        collect_direct_artifacts(selected, declaration, retention=retention)


def test_retained_artifact_evidence_identifies_the_bytes_actually_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, retention = _direct_scope(tmp_path)
    model = workspace / "model.json"
    model.write_bytes(b"before publication")
    expected = b"changed before native snapshot"
    put_file = retention.content_store.put_file

    def changed_source(
        source: RepositoryScopedPath, *, cancel_requested: Callable[[], bool] | None = None
    ) -> FleetContentObject:
        source.path.write_bytes(expected)
        return put_file(source, cancel_requested=cancel_requested)

    monkeypatch.setattr(retention.content_store, "put_file", changed_source)
    (artifact,) = collect_direct_artifacts(
        workspace,
        (PlanArtifactDeclaration(path="model.json", retention="content_store"),),
        retention=retention,
    )
    assert isinstance(artifact, PresentPlanArtifact) and artifact.retained_object is not None
    assert artifact.sha256 == hashlib.sha256(expected).hexdigest()
    assert artifact.byte_count == artifact.retained_object.byte_count == len(expected)


@pytest.mark.parametrize("failure", ["escape", "cancel"])
def test_partial_trusted_local_collection_releases_its_new_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    workspace, retention = _direct_scope(tmp_path)
    cancelled = False
    retention = replace(retention, cancel_requested=lambda: cancelled)
    (workspace / "first.json").write_bytes(b"first model")
    second = workspace / "second.json"
    second.write_bytes(b"second model")
    foreign = tmp_path / "foreign-model.json"
    foreign.write_bytes(b"foreign model")
    put_file = retention.content_store.put_file
    references: list[str] = []

    def break_next_artifact(
        source: RepositoryScopedPath, *, cancel_requested: Callable[[], bool] | None = None
    ) -> FleetContentObject:
        nonlocal cancelled
        retained = put_file(source, cancel_requested=cancel_requested)
        references.append(retained.reference_id)
        if failure == "escape":
            second.unlink()
            second.symlink_to(foreign)
        else:
            cancelled = True
        return retained

    monkeypatch.setattr(retention.content_store, "put_file", break_next_artifact)
    with pytest.raises(FleetPlanInvalid if failure == "escape" else OperationCancelledError):
        collect_direct_artifacts(
            workspace,
            tuple(
                PlanArtifactDeclaration(path=name, retention="content_store")
                for name in ("first.json", "second.json")
            ),
            retention=retention,
        )
    assert len(references) == 1
    with pytest.raises(FleetPlanInvalid):
        retention.content_store.get(references[0], owner_repository_id=OWNER)


@pytest.mark.parametrize("mode", ["direct", "strict"])
@pytest.mark.parametrize("failed_file", [1, 2])
def test_post_retention_file_read_failure_rolls_back_all_new_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, failed_file: int
) -> None:
    import oh_goon.integrations.fleet_plan_step_publication as publication

    workspace, retention = _direct_scope(tmp_path)
    for name in ("first.json", "second.json"):
        (workspace / name).write_text(name)
    put_file = retention.content_store.put_file
    retained_files: list[FleetContentObject] = []
    hash_file = publication.sha256_file_with_size

    def record_retention(
        source: RepositoryScopedPath, *, cancel_requested: Callable[[], bool] | None = None
    ) -> FleetContentObject:
        retained = put_file(source, cancel_requested=cancel_requested)
        retained_files.append(retained)
        return retained

    def fail_frozen_file_read(path: Path) -> tuple[str, int]:
        if len(retained_files) == failed_file:
            raise OSError("injected frozen artifact read failure")
        return hash_file(path)

    monkeypatch.setattr(retention.content_store, "put_file", record_retention)
    monkeypatch.setattr(publication, "sha256_file_with_size", fail_frozen_file_read)
    declarations = tuple(
        PlanArtifactDeclaration(path=name, retention="content_store")
        for name in ("first.json", "second.json")
    )
    with pytest.raises(OSError, match="injected frozen artifact read failure"):
        if mode == "direct":
            collect_direct_artifacts(workspace, declarations, retention=retention)
        else:
            collect_strict_artifacts(workspace, declarations, _prepared(retention.content_store))
    assert len(retained_files) == failed_file
    for retained in retained_files:
        with pytest.raises(FleetPlanInvalid):
            retention.content_store.get(retained.reference_id, owner_repository_id=OWNER)


@pytest.mark.parametrize(
    "entrypoint,missing",
    [
        ("prepare", "store"),
        ("execute", "store"),
        ("execute", "owner"),
        ("execute", "empty-owner"),
        ("execute", "invalid-owner"),
    ],
)
def test_trusted_local_retention_requires_store_and_valid_owner_before_work(
    tmp_path: Path, entrypoint: str, missing: str
) -> None:
    repository = _repository(tmp_path)
    plan = ResolvedFleetPlan.create(
        name="retained-training",
        description="",
        execution=VerificationExecutionPolicy(),
        steps=(_captured_step(repository),),
    )
    run_parent = tmp_path / "runs"
    executor = FleetPlanRunExecutor(
        GitService(),
        process_isolation_backends=(),
        content_store=None if missing == "store" else FleetContentStore(tmp_path / "store"),
        run_parent=run_parent,
    )
    with pytest.raises(FleetPlanInvalid):
        if entrypoint == "prepare":
            executor.prepare_direct_run(
                RepositoryIdentity(path=repository), plan, cancel_requested=None
            )
        else:
            executor.execute_direct_run(
                repository,
                plan,
                repository_id=None
                if missing == "owner"
                else ""
                if missing == "empty-owner"
                else "invalid-owner"
                if missing == "invalid-owner"
                else OWNER,
                cancel_requested=None,
            )
    assert not run_parent.exists()


def _captured_step(repository: Path) -> ResolvedCommandStep:
    project = CapturedProjectSource(
        content_sha256="a" * 64,
        archive_sha256="b" * 64,
        byte_count=1,
        entry_count=1,
        application=LocalApplicationSource(path=str(repository)),
    )
    return ResolvedCommandStep(
        step_id="train",
        argv=("train",),
        project_source=project,
        artifacts=(PlanArtifactDeclaration(path="model.json", retention="content_store"),),
    )


@pytest.mark.parametrize("native_returncode", [0, 1])
def test_direct_publication_failure_records_step_failure_and_preserves_native_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native_returncode: int
) -> None:
    repository = _repository(tmp_path)
    workspace, retention = _direct_scope(tmp_path)
    step = _captured_step(repository)
    launched: list[str] = []

    def execute(source: Path, selected: ResolvedCommandStep, *args, **kwargs) -> StepOutcome:
        launched.append(selected.step_id)
        (source / "model.json").write_bytes(b"actual training result")
        return from_command(
            CommandResult(
                argv=selected.argv,
                returncode=native_returncode,
                stdout="native training result",
                stderr="native failure" if native_returncode else "",
            )
        )

    def storage_failure(*args, **kwargs):
        raise OSError("retained storage is full")

    monkeypatch.setattr("oh_goon.integrations._fleet_plan_step_retry.execute_direct_step", execute)
    monkeypatch.setattr(retention.content_store, "put_file", storage_failure)
    sequence = FleetPlanStepSequence(
        repository,
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
            project_workspaces={step.step_id: workspace},
        ),
        direct_artifact_retention={step.step_id: retention},
    )
    sequence.run_direct(step)
    sequence.run_direct(ResolvedCommandStep(step_id="followup", argv=("followup",)))
    failed, skipped = sequence.results
    assert failed.status == "FAILED"
    assert failed.returncode == (native_returncode or PLAN_CONTRACT_RETURN_CODE)
    assert failed.attempts[0].returncode == native_returncode
    assert failed.artifacts == failed.outputs == ()
    assert skipped.status == "SKIPPED"
    assert launched == [step.step_id]
    assert "native training result" in sequence.stdout
    assert "retained storage is full" in sequence.stderr
    if native_returncode:
        assert "native failure" in sequence.stderr


@pytest.mark.parametrize("closure", ["unknown", "unverified"])
def test_unsettled_direct_step_never_retains_requested_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closure: str
) -> None:
    repository = _repository(tmp_path)
    workspace, retention = _direct_scope(tmp_path)
    step = _captured_step(repository)
    (workspace / "model.json").write_bytes(b"unsettled result")

    def unsettled(*args, **kwargs) -> StepOutcome:
        return StepOutcome(
            argv=step.argv,
            returncode=0,
            supervisor=CommandSupervisorReceipt(final_process_group_quiescent=False)
            if closure == "unverified"
            else None,
        )

    def forbidden_publication(*args, **kwargs):
        pytest.fail("Unsettled process reached artifact publication")

    monkeypatch.setattr(
        "oh_goon.integrations._fleet_plan_step_retry.execute_direct_step", unsettled
    )
    monkeypatch.setattr(retention.content_store, "put_file", forbidden_publication)
    sequence = FleetPlanStepSequence(
        repository,
        StepRuntime(
            git=GitService(),
            extensions=None,
            tools=None,
            cancel_requested=None,
            project_workspaces={step.step_id: workspace},
        ),
        direct_artifact_retention={step.step_id: retention},
    )
    sequence.run_direct(step)
    failed = sequence.results[0]
    assert failed.status == "FAILED" and failed.artifacts == ()
    assert failed.settlement.cleanup == ("UNAVAILABLE" if closure == "unknown" else "UNVERIFIED")
