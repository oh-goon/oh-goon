from pathlib import Path

from oh_goon.core.models.fleet import OperationState
from oh_goon.core.vocabulary import OperationAction, ResultFreshness, Verdict
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.journal import Journal
from tests.factories.journal import commit_operation
from tests.factories.operation_reports import make_operation_report


def test_journal_index_and_read_view_share_one_operation_state_shape(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    journal = Journal(LocalDatabase(tmp_path / "state.db"))
    report = make_operation_report(
        repository,
        OperationAction.INIT,
        Verdict.READY,
        fleet_run_id=None,
        operation_id="op-init",
    )
    commit_operation(journal, report)
    view = journal.read_view()

    indexed = view._index.current_capability(
        view.session_id,
        repository,
        "initialize",
        None,
    )
    exposed = view.current_capability(repository, "initialize")

    assert isinstance(indexed, OperationState)
    assert indexed.freshness is ResultFreshness.CURRENT
    assert exposed == indexed
    assert exposed is not indexed
    assert exposed is not None and exposed.report is not indexed.report
