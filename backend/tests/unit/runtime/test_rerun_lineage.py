from dataclasses import replace
from pathlib import Path

import pytest

from oh_goon.core.capability_registry import resolve_capability_intent
from oh_goon.core.errors import JournalConflict, StateConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.models.run_admission import (
    DirectFleetExecutionAdmission,
    FleetRunAdmissionIntent,
    fleet_repository_scope,
)
from oh_goon.core.vocabulary import (
    FleetRunFailureStage,
    FleetRunSource,
    FleetRunTerminalIntent,
    OperationAction,
)
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.journal import admit_run, start_run, terminalize_run


def _intent(
    *, rerun_from_run_id: str | None = None, source: FleetRunSource = FleetRunSource.MANUAL
):
    resolved = resolve_capability_intent(OperationAction.INIT, None)
    return FleetRunAdmissionIntent(
        source=source,
        target=None,
        capability=resolved.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(rerun_from_run_id=rerun_from_run_id),
        repository_admissions=(),
    )


def _terminalize_parent(journal: Journal, run_id: str) -> None:
    start_run(journal, run_id)
    terminalize_run(
        journal,
        run_id,
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure.from_exception(
            StateConflict("terminal test parent"), stage=FleetRunFailureStage.EXECUTION
        ),
    )


def test_journal_requires_rerun_parent_to_exist(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "rerun-parent-exists.db"))
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    scope = fleet_repository_scope({repository.repository_id})

    with pytest.raises(JournalConflict, match="parent does not exist"):
        admit_run(journal, "child", _intent(rerun_from_run_id="missing"), scope, [repository])


def test_journal_requires_run_history_rerun_to_have_manual_source(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "rerun-source.db"))
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    scope = fleet_repository_scope({repository.repository_id})
    admit_run(journal, "parent", _intent(), scope, [repository])
    _terminalize_parent(journal, "parent")

    with pytest.raises(JournalConflict, match="MANUAL execution source"):
        admit_run(
            journal,
            "child",
            _intent(rerun_from_run_id="parent", source=FleetRunSource.CICD),
            scope,
            [repository],
        )


def test_journal_binds_rerun_to_exact_parent_authority(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "rerun-authority.db"))
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    scope = fleet_repository_scope({repository.repository_id})
    admit_run(journal, "parent", _intent(), scope, [repository])
    _terminalize_parent(journal, "parent")

    verify = resolve_capability_intent(OperationAction.VERIFY, None)
    changed = FleetRunAdmissionIntent(
        source=FleetRunSource.MANUAL,
        target=None,
        capability=verify.definition.durable_snapshot(),
        execution=DirectFleetExecutionAdmission(rerun_from_run_id="parent"),
        repository_admissions=(),
    )
    with pytest.raises(JournalConflict, match="another operation intent"):
        admit_run(journal, "child", changed, scope, [repository])


def test_journal_rejects_rerun_that_reuses_parent_run_identity(tmp_path: Path) -> None:
    journal = Journal(LocalDatabase(tmp_path / "rerun-self-parent.db"))
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    scope = fleet_repository_scope({repository.repository_id})

    with pytest.raises(JournalConflict, match="cannot reuse its parent run identity"):
        admit_run(journal, "same", _intent(rerun_from_run_id="same"), scope, [repository])


def test_journal_rerun_follows_current_capability_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "rerun-current-policy.db"))
    repository = RepositoryIdentity.model_validate({"path": tmp_path / "repo"})
    scope = fleet_repository_scope({repository.repository_id})
    admit_run(journal, "parent", _intent(), scope, [repository])
    _terminalize_parent(journal, "parent")

    resolved = resolve_capability_intent(OperationAction.INIT, None)
    blocked = replace(
        resolved,
        definition=replace(
            resolved.definition,
            label="Relabeled capability",
            description="Incidental presentation metadata changed.",
            rerun_supported=False,
        ),
    )
    monkeypatch.setattr(
        "oh_goon.runtime.journal_admission_validation.resolve_bound_capability_intent",
        lambda *_args, **_kwargs: blocked,
    )

    with pytest.raises(JournalConflict, match="not repeatable through Run history"):
        admit_run(journal, "child", _intent(rerun_from_run_id="parent"), scope, [repository])
