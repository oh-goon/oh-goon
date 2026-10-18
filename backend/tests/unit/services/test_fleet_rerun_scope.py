from pathlib import Path

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run import FleetRunFailure
from oh_goon.core.product_identity import repository_attempt_operation_id
from oh_goon.core.vocabulary import FleetRunFailureStage, FleetRunTerminalIntent, OperationAction
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from oh_goon.services.fleet_rerun_scope import rerun_repository_scope
from tests.factories.capabilities import admit_test_run
from tests.factories.journal import commit_operation, start_repository, start_run, terminalize_run
from tests.factories.operation_reports import make_operation_report


def test_failed_parent_with_all_repository_effects_successful_cannot_replay_full_scope(
    tmp_path: Path,
) -> None:
    journal = Journal(LocalDatabase(tmp_path / "rerun-scope.db"))
    repositories = [
        _repository(tmp_path, "alpha"),
        _repository(tmp_path, "beta"),
    ]
    run_id = "post-effects-failure"
    admit_test_run(journal, run_id, OperationAction.INIT, repositories)
    start_run(journal, run_id)
    for repository in repositories:
        start_repository(journal, run_id, repository)
        commit_operation(
            journal,
            make_operation_report(
                repository,
                OperationAction.INIT,
                fleet_run_id=run_id,
                operation_id=repository_attempt_operation_id(run_id, repository.repository_id),
            ),
        )
    terminalize_run(
        journal,
        run_id,
        FleetRunTerminalIntent.FAIL,
        FleetRunFailure.from_exception(
            StateConflict("terminal publication failed after effects committed"),
            stage=FleetRunFailureStage.EXECUTION,
        ),
    )
    detail = journal.read_view().required_run_detail(run_id)

    assert detail.run.progress.completed == 2
    assert detail.run.progress.succeeded == 2
    with pytest.raises(StateConflict, match="no unresolved repository targets"):
        rerun_repository_scope(detail)


def _repository(tmp_path: Path, name: str) -> RepositoryIdentity:
    return RepositoryIdentity(name=name, path=tmp_path / name)
